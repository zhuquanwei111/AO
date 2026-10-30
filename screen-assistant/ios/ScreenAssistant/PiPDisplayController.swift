import UIKit
import AVKit
import AVFoundation
import CoreMedia
import CoreVideo
import Combine

/// PiP 悬浮回复条控制器（主 App 侧）。
///
/// 链路：广播扩展 OCR+POST 得到 200 回复 → UDS 短回复通道 → 本控制器读取并把文本渲染为
/// 视频帧 → AVSampleBufferDisplayLayer → 系统 PiP 窗口展示。
///
/// 平台边界（如实）：
///   - PiP 会话启用 AVAudioSession `.playback` 与 `.mixWithOthers`，并在结束时停用；
///     不播放静音内容或循环音频。Info.plist 声明 `UIBackgroundModes=audio`。
///     后台实际存活仍需真机验证，音频后台模式也不保证进程永不挂起。
///   - 主 App 的 UDS listener 随 PiP 会话启停；扩展无法连接时有限重试并丢弃回复，
///     不将正文改写到通知或文件。
///   - **回声风险（R/36 指出，如实）**：广播采集整个屏幕，含本 App 自己的 PiP
///     窗口——若 PiP 覆盖识别区域，OCR 可能读到自己的回复形成回环。公开 API
///     无法读取 PiP 窗口位置，几何剔除不可行；缓解 = 用户将 PiP 窗口拖离识别
///     区域（界面文案与真机验收清单已含此项）。
///   - PiP 窗口为系统窗口，可被用户拖动；覆盖区域为系统自身交互边界，
///     不承诺完全触摸穿透（对齐计划“采集区域不影响前台操作”的 iOS 口径）。
///   - iOS 15 的 ContentSource 构造器强制要求 playbackDelegate，全部必需方法
///     已实现（live 语义：时间范围无限、暂停仅记状态、跳转无操作）。
///   - `AVSampleBufferDisplayLayer.enqueue` 自 iOS 17 标记弃用（iOS 17+ 官方
///     建议改用 AVSampleBufferVideoRenderer；R/36 O8）。本工程目标 iOS 15，
///     使用 enqueue（iOS 15/16 必须走此路径），真机验证时留意 17+ 上的行为。
final class PiPDisplayController: NSObject, ObservableObject {
    static let shared = PiPDisplayController()

    enum DisplayState: Equatable {
        case unsupported
        case idle
        case starting
        case running
        case suspended
        case stopped
        case failed(String)
    }

    @Published private(set) var state: DisplayState = .idle
    @Published private(set) var lastReplyAt: Date?

    private let heartbeatInterval: TimeInterval = 2
    private let replyPollInterval: TimeInterval = 0.1

    private var pipController: AVPictureInPictureController?
    private var heartbeatTimer: Timer?
    private var replyPollTimer: Timer?
    private let transport = ReplyTransportFactory.make()
    private var audioSessionActive = false
    private var lastRenderedCGImage: CGImage?
    private var lastDisplayedText: String?
    private var lastDisplayedKind: DisplayKind?
    private var liveContentActive = false
    private var liveSessionActive = false
    private var isPlaying = true

    private enum DisplayKind: Equatable {
        case quickAnswer
        case operation
        case connection

        var label: String {
            switch self {
            case .quickAnswer: return "问答"
            case .operation: return "运营"
            case .connection: return "连接"
            }
        }

        var tint: UIColor {
            switch self {
            case .quickAnswer: return .systemBlue
            case .operation: return .systemOrange
            case .connection: return .systemYellow
            }
        }
    }

    /// host view：必须真实附着在 window 上 PiP 才能启动。
    /// 1x1pt 尺寸会让系统无法计算 PiP 收缩动画导致 startPictureInPicture
    /// 静默失败（参考实现实证），采用 16:9 且近透明、不拦截交互。
    private lazy var hostView: HostView = {
        let view = HostView(frame: CGRect(x: 20, y: 80, width: 160, height: 90))
        view.alpha = 0.01
        view.isUserInteractionEnabled = false
        return view
    }()

    private var displayLayer: AVSampleBufferDisplayLayer { hostView.displayLayer }

    // MARK: - 生命周期

    private override init() {
        super.init()
    }

    var isSupported: Bool {
        AVPictureInPictureController.isPictureInPictureSupported()
    }

    func start() {
        guard isSupported else {
            ControlState.setUserWantsCapture(false)
            state = .unsupported
            return
        }
        guard pipController == nil else { return }
        attachHostViewIfNeeded()

        do {
            try transport.startReceiving()
        } catch {
            ControlState.setUserWantsCapture(false)
            state = .failed("回复通道启动失败：\(error.localizedDescription)")
            return
        }

        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .moviePlayback, options: [.mixWithOthers])
            try session.setActive(true)
            audioSessionActive = true
        } catch {
            transport.stopReceiving()
            ControlState.setUserWantsCapture(false)
            state = .failed("PiP 音频会话启动失败：\(error.localizedDescription)")
            return
        }

        let source = AVPictureInPictureController.ContentSource(
            sampleBufferDisplayLayer: displayLayer,
            playbackDelegate: self
        )
        let controller = AVPictureInPictureController(contentSource: source)
        controller.delegate = self
        // App 切后台时自动进入 PiP（iOS 14.2+，官方 API；afm 同款）
        controller.canStartPictureInPictureAutomaticallyFromInline = true
        controller.requiresLinearPlayback = true
        pipController = controller

        // 等比缩放渲染（afm 同款）+ live 内容时间基：rate 1，主时钟驱动
        displayLayer.videoGravity = .resizeAspect
        var timebase: CMTimebase?
        guard CMTimebaseCreateWithSourceClock(
            allocator: kCFAllocatorDefault,
            sourceClock: CMClockGetHostTimeClock(),
            timebaseOut: &timebase
        ) == noErr, let timebase else {
            state = .failed("无法建立 PiP 视频时间基")
            teardown()
            return
        }
        CMTimebaseSetRate(timebase, rate: 1)
        CMTimebaseSetTime(timebase, time: .zero)
        displayLayer.controlTimebase = timebase

        isPlaying = true
        enqueue(placeholderFrame())
        if !controller.isPictureInPicturePossible {
            // afm 实证：layer 未附着可见 window 层级或非前台时不可 detach
            state = .failed("PiP 当前不可用：请确认 App 在前台")
            teardown()
            return
        }
        state = .starting
        controller.startPictureInPicture()

        startHeartbeat()
        startReplyPolling()
    }

    func stop() {
        pipController?.stopPictureInPicture()
        teardown()
        state = .stopped
    }

    private func teardown() {
        heartbeatTimer?.invalidate()
        heartbeatTimer = nil
        replyPollTimer?.invalidate()
        replyPollTimer = nil
        transport.stopReceiving()
        ControlState.setUserWantsCapture(false)
        hostView.removeFromSuperview()
        pipController = nil
        liveContentActive = false
        lastRenderedCGImage = nil
        lastDisplayedText = nil
        lastDisplayedKind = nil
        displayLayer.flushAndRemoveImage()
        if audioSessionActive {
            try? AVAudioSession.sharedInstance().setActive(false,
                                                           options: .notifyOthersOnDeactivation)
            audioSessionActive = false
        }
    }

    // MARK: - 心跳（liveness + 重喂帧）

    private func startHeartbeat() {
        heartbeatTimer?.invalidate()
        let timer = Timer(timeInterval: heartbeatInterval, repeats: true) { [weak self] _ in
            guard let self else { return }
            if self.pipController?.isPictureInPictureSuspended == true {
                self.state = .suspended
                return
            }
            if self.state == .suspended, self.pipController?.isPictureInPictureActive == true {
                self.state = .running
            }
            guard self.isPlaying else { return }
            // 刷新最后一帧；不播放音频内容。
            let image = self.lastRenderedCGImage ?? self.placeholderFrame()
            self.enqueue(image)
        }
        RunLoop.main.add(timer, forMode: .common)
        heartbeatTimer = timer
    }

    private func startReplyPolling() {
        replyPollTimer?.invalidate()
        let timer = Timer(timeInterval: replyPollInterval, repeats: true) { [weak self] _ in
            self?.consumeReply()
        }
        RunLoop.main.add(timer, forMode: .common)
        replyPollTimer = timer
    }

    // MARK: - 回复消费与渲染

    private func consumeReply() {
        guard state == .running || state == .starting else { return }
        guard let reply = transport.takeLatestReply() else { return }
        showContent(reply, kind: .quickAnswer)
    }

    /// M4 的行动建议统一走同一显示入口，并明确标为运营内容。
    func showLivePrompt(_ text: String) {
        showContent(text, kind: .operation)
    }

    /// 必须在主线程调用；仅表示选中并生成帧，不代表系统 PiP 已显示。
    func showLiveFrame(_ items: [LivePromptDelivery.Item]) -> Bool {
        guard canShowLiveFrame, !items.isEmpty else { return false }
        var text = liveFrameText(items)
        if !liveTextFits(text) {
            let characters = Array(text)
            var low = 0
            var high = characters.count
            while low < high {
                let middle = (low + high + 1) / 2
                if liveTextFits(String(characters.prefix(middle)) + "…") { low = middle }
                else { high = middle - 1 }
            }
            text = String(characters.prefix(low)) + "…"
        }
        let image = renderReplyFrame(text, kind: .operation)
        lastRenderedCGImage = image
        lastDisplayedText = text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        lastDisplayedKind = .operation
        liveContentActive = true
        if isPlaying { enqueue(image) }
        lastReplyAt = Date()
        return true
    }

    func liveFrameFits(_ items: [LivePromptDelivery.Item]) -> Bool {
        liveTextFits(liveFrameText(items))
    }

    var canShowLiveFrame: Bool {
        state == .running && isPlaying && pipController?.isPictureInPictureActive == true
    }
    var hasLiveContent: Bool { liveContentActive }

    /// 直播场次空闲时保留安静画面；连接诊断仍由主界面展示。
    func setLiveSessionActive(_ active: Bool) {
        liveSessionActive = active
        guard active, !liveContentActive, pipController != nil else { return }
        lastDisplayedText = nil
        lastDisplayedKind = nil
        lastRenderedCGImage = placeholderFrame()
        if isPlaying, let image = lastRenderedCGImage { enqueue(image) }
    }

    func endLiveContent() {
        guard liveContentActive else { return }
        liveContentActive = false
        lastDisplayedText = nil
        lastDisplayedKind = nil
        lastRenderedCGImage = placeholderFrame()
        if isPlaying, let image = lastRenderedCGImage { enqueue(image) }
    }

    private func liveFrameText(_ items: [LivePromptDelivery.Item]) -> String {
        items.map { PiPTextFormatter.plainText($0.text) }.joined(separator: "\n")
    }

    private func liveTextFits(_ text: String) -> Bool {
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineBreakMode = .byWordWrapping
        paragraph.lineSpacing = 4
        let bounds = (text as NSString).boundingRect(
            with: CGSize(width: 592, height: CGFloat.greatestFiniteMagnitude),
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: [.font: UIFont.systemFont(ofSize: 26), .paragraphStyle: paragraph],
            context: nil)
        return ceil(bounds.height) <= 274
    }

    /// 只在用户已启动 PiP 时显示最近真实请求的服务状态；不尝试启动系统 PiP。
    func showConnectionStatus(_ text: String) {
        guard !liveSessionActive, !liveContentActive, pipController != nil,
              state == .starting || state == .running || state == .suspended else { return }
        showContent(text, kind: .connection)
    }

    private func showContent(_ rawText: String, kind: DisplayKind) {
        DispatchQueue.main.async { [weak self] in
            guard let self, self.pipController != nil else { return }
            guard !self.liveSessionActive || kind == .operation else { return }
            guard !self.liveContentActive || kind == .operation else { return }
            let text = PiPTextFormatter.plainText(rawText)
            guard !text.isEmpty else { return }
            let comparisonText = text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
            guard comparisonText != self.lastDisplayedText || kind != self.lastDisplayedKind else {
                return
            }
            let image = self.renderReplyFrame(text, kind: kind)
            self.lastRenderedCGImage = image
            self.lastDisplayedText = comparisonText
            self.lastDisplayedKind = kind
            if self.isPlaying { self.enqueue(image) }
            self.lastReplyAt = Date()
        }
    }

    /// 把回复文本渲染为 640×360 帧图（内存态，不落盘）。
    private func renderReplyFrame(_ reply: String, kind: DisplayKind) -> CGImage {
        let size = CGSize(width: 640, height: 360)
        let renderer = UIGraphicsImageRenderer(size: size)
        let uiImage = renderer.image { context in
            let cg = context.cgContext
            cg.setFillColor(UIColor(white: 0.10, alpha: 0.92).cgColor)
            cg.fill(CGRect(origin: .zero, size: size))

            let title = "屏识"
            let titleAttrs: [NSAttributedString.Key: Any] = [
                .font: UIFont.boldSystemFont(ofSize: 24),
                .foregroundColor: UIColor.systemTeal
            ]
            (title as NSString).draw(at: CGPoint(x: 24, y: 18), withAttributes: titleAttrs)

            let tagRect = CGRect(x: size.width - 112, y: 14, width: 88, height: 38)
            let tagBackground = tagRect.insetBy(dx: -6, dy: -3)
            let tagPath = UIBezierPath(roundedRect: tagBackground, cornerRadius: 9)
            cg.setFillColor(kind.tint.withAlphaComponent(0.24).cgColor)
            cg.addPath(tagPath.cgPath)
            cg.fillPath()
            cg.setStrokeColor(kind.tint.withAlphaComponent(0.85).cgColor)
            cg.setLineWidth(1.5)
            cg.addPath(tagPath.cgPath)
            cg.strokePath()
            let tagAttrs: [NSAttributedString.Key: Any] = [
                .font: UIFont.boldSystemFont(ofSize: 20),
                .foregroundColor: kind.tint
            ]
            (kind.label as NSString).draw(in: tagRect, withAttributes: tagAttrs)

            let paragraph = NSMutableParagraphStyle()
            paragraph.lineBreakMode = .byWordWrapping
            paragraph.lineSpacing = 4
            let bodyAttrs: [NSAttributedString.Key: Any] = [
                .font: UIFont.systemFont(ofSize: 26),
                .foregroundColor: UIColor.white,
                .paragraphStyle: paragraph
            ]
            let textRect = CGRect(x: 24, y: 68, width: size.width - 48, height: size.height - 86)
            (reply as NSString).draw(in: textRect, withAttributes: bodyAttrs)
        }
        return uiImage.cgImage ?? placeholderFrame()
    }

    private func placeholderFrame() -> CGImage {
        let size = CGSize(width: 640, height: 360)
        let renderer = UIGraphicsImageRenderer(size: size)
        let uiImage = renderer.image { context in
            let cg = context.cgContext
            cg.setFillColor(UIColor(white: 0.10, alpha: 0.92).cgColor)
            cg.fill(CGRect(origin: .zero, size: size))
            let attrs: [NSAttributedString.Key: Any] = [
                .font: UIFont.systemFont(ofSize: 24),
                .foregroundColor: UIColor.white
            ]
            ((liveSessionActive ? "屏识" : "屏识AI助手 · 等待识别结果…") as NSString)
                .draw(at: CGPoint(x: 24, y: 24), withAttributes: attrs)
        }
        return uiImage.cgImage!
    }

    // MARK: - 帧入队

    private func enqueue(_ cgImage: CGImage) {
        guard let buffer = Self.makeSampleBuffer(from: cgImage,
                                                 timebase: displayLayer.controlTimebase) else { return }
        // enqueue 在新 SDK 中标记弃用（R/36 O8 与 afm 注释分别标注 iOS 17/18，
        // 以实际 SDK 警告为准）；iOS 15/16 须用本路径。刻意不引入
        // sampleBufferRenderer 门控：该符号在旧 Xcode SDK 不存在，会给 Mac
        // 构建者增加编译失败风险；enqueue 弃用≠移除，待真机验证后再迁移。
        // 同一张快照替换待显示帧，避免心跳或短时间连续收件在 layer 中排成长队。
        // flush() 保留当前已显示图像，待新样本就绪后再替换，减少空白闪烁。
        displayLayer.flush()
        displayLayer.enqueue(buffer)
    }

    /// CGImage → CVPixelBuffer → CMSampleBuffer（内存态转换）。
    static func makeSampleBuffer(from cgImage: CGImage, timebase: CMTimebase?) -> CMSampleBuffer? {
        let width = cgImage.width
        let height = cgImage.height
        var pixelBuffer: CVPixelBuffer?
        let attrs: [CFString: Any] = [kCVPixelBufferCGImageCompatibilityKey: true]
        guard CVPixelBufferCreate(kCFAllocatorDefault, width, height,
                                  kCVPixelFormatType_32BGRA, attrs as CFDictionary,
                                  &pixelBuffer) == kCVReturnSuccess,
              let pixelBuffer else { return nil }

        CVPixelBufferLockBaseAddress(pixelBuffer, [])
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, []) }
        guard let base = CVPixelBufferGetBaseAddress(pixelBuffer) else { return nil }
        let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
        guard let context = CGContext(
            data: base,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: bytesPerRow,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue |
                        CGBitmapInfo.byteOrder32Little.rawValue
        ) else { return nil }
        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))

        var formatDescription: CMVideoFormatDescription?
        guard CMVideoFormatDescriptionCreateForImageBuffer(
            allocator: kCFAllocatorDefault, imageBuffer: pixelBuffer, formatDescriptionOut: &formatDescription
        ) == noErr, let formatDescription else { return nil }

        let pts = timebase.map { CMTimebaseGetTime($0) } ?? CMTime(value: 0, timescale: 600)
        var timing = CMSampleTimingInfo(
            duration: CMTime.invalid,
            presentationTimeStamp: pts,
            decodeTimeStamp: CMTime.invalid
        )
        var sampleBuffer: CMSampleBuffer?
        guard CMSampleBufferCreateForImageBuffer(
            allocator: kCFAllocatorDefault,
            imageBuffer: pixelBuffer,
            dataReady: true,
            makeDataReadyCallback: nil,
            refcon: nil,
            formatDescription: formatDescription,
            sampleTiming: &timing,
            sampleBufferOut: &sampleBuffer
        ) == noErr else { return nil }
        return sampleBuffer
    }

    private func attachHostViewIfNeeded() {
        guard hostView.superview == nil,
              let scene = UIApplication.shared.connectedScenes
                .compactMap({ $0 as? UIWindowScene })
                .first,
              let window = scene.keyWindow ?? scene.windows.first
        else { return }
        window.addSubview(hostView)
    }
}

// MARK: - AVPictureInPictureControllerDelegate

extension PiPDisplayController: AVPictureInPictureControllerDelegate {
    func pictureInPictureControllerDidStartPictureInPicture(
        _ pictureInPictureController: AVPictureInPictureController) {
        DispatchQueue.main.async { [weak self] in
            guard let self, self.pipController === pictureInPictureController else { return }
            self.state = .running
        }
    }

    func pictureInPictureControllerDidStopPictureInPicture(
        _ pictureInPictureController: AVPictureInPictureController) {
        DispatchQueue.main.async { [weak self] in
            guard let self, self.pipController === pictureInPictureController else { return }
            self.teardown()
            self.state = .stopped
        }
    }

    func pictureInPictureController(
        _ pictureInPictureController: AVPictureInPictureController,
        failedToStartPictureInPictureWithError error: Error) {
        DispatchQueue.main.async { [weak self] in
            guard let self, self.pipController === pictureInPictureController else { return }
            self.teardown()
            self.state = .failed(error.localizedDescription)
        }
    }
}

// MARK: - AVPictureInPictureSampleBufferPlaybackDelegate（iOS 15 构造器必需）

extension PiPDisplayController: AVPictureInPictureSampleBufferPlaybackDelegate {
    /// 用户点 PiP 上的播放/暂停：记录状态即可；心跳与帧重喂不受影响
    /// （保持展示活性优先，本内容非真实播放）。
    func pictureInPictureController(
        _ pictureInPictureController: AVPictureInPictureController,
        setPlaying playing: Bool) {
        isPlaying = playing
        if playing, let image = lastRenderedCGImage {
            enqueue(image)
        }
    }

    /// live 语义：时间范围无限。
    func pictureInPictureControllerTimeRangeForPlayback(
        _ pictureInPictureController: AVPictureInPictureController) -> CMTimeRange {
        CMTimeRange(start: .zero, duration: .positiveInfinity)
    }

    func pictureInPictureControllerIsPlaybackPaused(
        _ pictureInPictureController: AVPictureInPictureController) -> Bool {
        !isPlaying
    }

    func pictureInPictureController(
        _ pictureInPictureController: AVPictureInPictureController,
        didTransitionToRenderSize newRenderSize: CMVideoDimensions) {
        // 无操作
    }

    func pictureInPictureController(
        _ pictureInPictureController: AVPictureInPictureController,
        skipByInterval skipInterval: CMTime,
        completion: @escaping () -> Void) {
        // live 内容不支持跳转
        completion()
    }
}

// MARK: - host view

private extension PiPDisplayController {
    final class HostView: UIView {
        override class var layerClass: AnyClass { AVSampleBufferDisplayLayer.self }
        var displayLayer: AVSampleBufferDisplayLayer { layer as! AVSampleBufferDisplayLayer }
    }
}

/// 清理用于排版的常见 Markdown 外壳，不改写正文语义或标点。
private enum PiPTextFormatter {
    static func plainText(_ source: String) -> String {
        var text = source.replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")

        // 暂存围栏正文，避免把其中合法的 #、列表符等当作排版前缀清掉。
        var fencedBlocks: [String: String] = [:]
        let fencePattern = #"(?ms)^[ \t]*```[^\n]*\n(.*?)^[ \t]*```[ \t]*$"#
        if let expression = try? NSRegularExpression(pattern: fencePattern) {
            let nsText = text as NSString
            let matches = expression.matches(in: text, range: NSRange(location: 0, length: nsText.length))
            for (index, match) in matches.enumerated().reversed() {
                let token = "PIPFENCEDBLOCK\(index)END"
                fencedBlocks[token] = nsText.substring(with: match.range(at: 1))
                text = (text as NSString).replacingCharacters(in: match.range, with: token)
            }
        }
        text = replacing(#"(?m)^[ \t]*```[^\n]*[ \t]*$"#, in: text, with: "")
        text = replacing(#"(?m)^[ \t]{0,3}#{1,6}[ \t]+"#, in: text, with: "")
        text = replacing(#"(?m)^[ \t]{0,3}>[ \t]?"#, in: text, with: "")
        text = replacing(#"(?m)^[ \t]*(?:[-+*]|[0-9]{1,3}[.)])[ \t]+"#, in: text, with: "")

        text = replacing(#"!\[([^\]\n]*)\]\([^\)\n]*\)"#, in: text, with: "$1")
        text = replacing(#"\[([^\]\n]+)\]\([^\)\n]*\)"#, in: text, with: "$1")
        text = replacing(#"`([^`\n]+)`"#, in: text, with: "$1")
        text = replacing(#"\*\*([^*\n]+)\*\*"#, in: text, with: "$1")
        text = replacing(#"__([^_\n]+)__"#, in: text, with: "$1")
        text = replacing(#"(?<![\p{L}\p{N}])\*([^\s*](?:[^*\n]*?[^\s*])?)\*(?![\p{L}\p{N}])"#,
                         in: text, with: "$1")
        text = replacing(#"(?<![\p{L}\p{N}])_([^_\n]+)_(?![\p{L}\p{N}])"#,
                         in: text, with: "$1")

        let lines = text.components(separatedBy: "\n").map {
            $0.trimmingCharacters(in: .whitespaces)
        }
        var compactLines: [String] = []
        var previousWasBlank = false
        for line in lines {
            let isBlank = line.isEmpty
            if isBlank && previousWasBlank { continue }
            compactLines.append(line)
            previousWasBlank = isBlank
        }
        text = compactLines.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
        for (token, block) in fencedBlocks {
            text = text.replacingOccurrences(of: token, with: block)
        }
        return text
    }

    private static func replacing(_ pattern: String, in text: String, with template: String) -> String {
        guard let expression = try? NSRegularExpression(pattern: pattern) else { return text }
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        return expression.stringByReplacingMatches(in: text, range: range, withTemplate: template)
    }
}
