import UIKit
import AVKit
import AVFoundation
import CoreMedia
import CoreVideo
import Combine

/// PiP 悬浮回复条控制器（主 App 侧）。
///
/// 链路：广播扩展 OCR+POST 得到 200 回复 →（pipActive 且心跳新鲜时）
/// 经 ReplyTransport 瞬态邮箱 + Darwin 信号 → 本控制器读取并把文本渲染为
/// 视频帧 → AVSampleBufferDisplayLayer → 系统 PiP 窗口展示。
///
/// 平台边界（如实）：
///   - **音频会话默认不激活、不播放任何音频**（遵守“禁止无关后台音频伪保活”）。
///     R/36 深核表明：PiP 后台存活 = PiP 激活 + UIBackgroundModes=audio + 活跃
///     .playback 会话三者齐备，.playback 会话是 PiP detach 前置（即使无声）。
///     该会话是否属于禁令范畴需主控/用户裁决——实现已就绪但由
///     `usesSilentPlaybackSessionForPiP` 常量门控（默认 false），获批后改一行即启用。
///     Info.plist 的 `UIBackgroundModes: audio` 是 Apple PiP 文档要求的能力声明
///     （"Audio, AirPlay, and Picture in Picture"）。
///   - 主 App 后台存活依赖 PiP 自身系统生命周期（关闭 PiP 窗口即停止存活、
///     广播停止则识别停止——系统限制，已写入界面文案）；若主 App 被挂起，
///     扩展侧 liveness 心跳检测自动把展示回落到通知，不阻塞采集/OCR/网络主链路。
///     此行为需真机验证。
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
        case stopped
        case failed(String)
    }

    @Published private(set) var state: DisplayState = .idle
    @Published private(set) var lastReplyAt: Date?

    /// 【待授权决策】PiP 后台存活的官方机制开关。
    ///
    /// R/36 深核（官方语义 O5 + afm-screen-translator 实证 + App Store 上架先例
    /// PiP Screen Translate id6756233631）：**PiP 激活 + UIBackgroundModes=audio +
    /// 活跃 .playback 会话**三者齐备时主 App 后台不挂起；.playback 会话是
    /// sample-buffer PiP detach 的前置条件（即使无声）。这正落入用户
    /// “禁止无关后台音频伪保活”的禁令边界，需主控/用户裁决：
    ///   - false（当前默认）：不激活任何音频会话，完全遵守禁令；
    ///     代价：PiP 在真机上可能无法 detach 或切后台即挂起，展示回落通知。
    ///   - true（获批后）：激活 .playback/.mixWithOthers 无声会话——该会话是
    ///     PiP 官方采纳链路的组成部分（非播放任何内容），属于“相关”保活。
    /// 获批后仅需把本常量改为 true，其余代码无需变动。
    static let usesSilentPlaybackSessionForPiP = false

    /// 心跳周期（秒）：既是扩展侧 liveness 心跳，也是 PiP 层的重喂帧间隔
    /// （防止 displayLayer 长时间无新帧出现的超时/缓冲行为——间隔需真机调参）。
    private let heartbeatInterval: TimeInterval = 2

    private var pipController: AVPictureInPictureController?
    private var heartbeatTimer: Timer?
    private var replyObserver: DarwinObserver?
    private let transport = ReplyTransportFactory.make()
    private var lastRenderedCGImage: CGImage?
    private var isPlaying = true

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
            state = .unsupported
            return
        }
        guard pipController == nil else { return }
        attachHostViewIfNeeded()

        // 静音 .playback 会话（PiP detach 前置，R/36 核实；默认关闭待授权，
        // 见 usesSilentPlaybackSessionForPiP 决策注释）
        if Self.usesSilentPlaybackSessionForPiP {
            let session = AVAudioSession.sharedInstance()
            try? session.setCategory(.playback, mode: .default, options: [.mixWithOthers])
            try? session.setActive(true)
        }

        let source = AVPictureInPictureController.ContentSource(
            sampleBufferDisplayLayer: displayLayer,
            playbackDelegate: self
        )
        let controller = AVPictureInPictureController(contentSource: source)
        controller.delegate = self
        // App 切后台时自动进入 PiP（iOS 14.2+，官方 API）
        controller.canStartPictureInPictureAutomaticallyFromInline = true
        pipController = controller

        // live 内容时间基：rate 1，主时钟驱动
        let timebase = CMTimebase(masterClock: CMClockGetHostClockTimebase())
        CMTimebaseSetRate(timebase, 1)
        CMTimebaseSetTime(timebase, CMTime.zero)
        displayLayer.controlTimebase = timebase

        isPlaying = true
        enqueue(placeholderFrame())
        state = .starting
        controller.startPictureInPicture()

        // 观察扩展的新回复信号
        replyObserver = DarwinObserver(name: DarwinSignal.newReplyAvailable) { [weak self] in
            self?.consumeReply()
        }
        startHeartbeat()
    }

    func stop() {
        pipController?.stopPictureInPicture()
        teardown()
        state = .stopped
    }

    private func teardown() {
        heartbeatTimer?.invalidate()
        heartbeatTimer = nil
        replyObserver = nil
        ControlState.setPipActive(false)
        hostView.removeFromSuperview()
        pipController = nil
        lastRenderedCGImage = nil
        displayLayer.flushAndRemoveImage()
        if Self.usesSilentPlaybackSessionForPiP {
            try? AVAudioSession.sharedInstance().setActive(false,
                                                           options: .notifyOthersOnDeactivation)
        }
    }

    // MARK: - 心跳（liveness + 重喂帧）

    private func startHeartbeat() {
        heartbeatTimer?.invalidate()
        let timer = Timer(timeInterval: heartbeatInterval, repeats: true) { [weak self] _ in
            guard let self else { return }
            ControlState.markPipHeartbeat()
            // 重喂当前帧，保持 PiP 活性（无音频、纯渲染，内存态帧）
            if let image = self.lastRenderedCGImage ?? self.placeholderFrame() {
                self.enqueue(image)
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        heartbeatTimer = timer
        ControlState.setPipActive(true)
    }

    // MARK: - 回复消费与渲染

    private func consumeReply() {
        guard state == .running || state == .starting else { return }
        guard let reply = transport.consumeLatest() else { return }
        let image = renderReplyFrame(reply)
        lastRenderedCGImage = image
        enqueue(image)
        lastReplyAt = Date()
    }

    /// 把回复文本渲染为 640×360 帧图（内存态，不落盘）。
    private func renderReplyFrame(_ reply: String) -> CGImage {
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

            let bodyAttrs: [NSAttributedString.Key: Any] = [
                .font: UIFont.systemFont(ofSize: 26),
                .foregroundColor: UIColor.white
            ]
            let textRect = CGRect(x: 24, y: 58, width: size.width - 48, height: size.height - 80)
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
            ("屏识AI助手 · 等待识别结果…" as NSString)
                .draw(at: CGPoint(x: 24, y: 24), withAttributes: attrs)
        }
        return uiImage.cgImage!
    }

    // MARK: - 帧入队

    private func enqueue(_ cgImage: CGImage) {
        guard let buffer = Self.makeSampleBuffer(from: cgImage,
                                                 timebase: displayLayer.controlTimebase) else { return }
        // iOS 17 起 enqueue 标记弃用（R/36 O8；iOS 15/16 须用本路径，
        // 17+ 的 AVSampleBufferVideoRenderer 迁移待真机验证后另行处理）
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
            kCFAllocatorDefault, pixelBuffer, &formatDescription
        ) == noErr, let formatDescription else { return nil }

        let pts = timebase.map { CMTimebaseGetTime($0) } ?? CMTime(value: 0, timescale: 600)
        var timing = CMSampleTimingInfo(
            duration: CMTime.invalid,
            presentationTimeStamp: pts,
            decodeTimeStamp: CMTime.invalid
        )
        var sampleBuffer: CMSampleBuffer?
        guard CMSampleBufferCreateForImageBuffer(
            kCFAllocatorDefault,
            pixelBuffer,
            true,
            nil,
            nil,
            formatDescription,
            &timing,
            &sampleBuffer
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
        state = .running
    }

    func pictureInPictureControllerDidStopPictureInPicture(
        _ pictureInPictureController: AVPictureInPictureController) {
        teardown()
        state = .stopped
    }

    func pictureInPictureController(
        _ pictureInPictureController: AVPictureInPictureController,
        failedToStartPictureInPictureWithError error: Error) {
        teardown()
        state = .failed(error.localizedDescription)
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
    }

    /// 显式禁止系统为本 PiP 维持后台音频播放路径：本展示无任何音频，
    /// 不依赖音频会话保活（合规声明）。
    func pictureInPictureControllerShouldProhibitBackgroundAudioPlayback(
        _ pictureInPictureController: AVPictureInPictureController) -> Bool {
        true
    }

    /// live 语义：时间范围无限。
    func pictureInPictureControllerTimeRangeForPlayback(
        _ pictureInPictureController: AVPictureInPictureController) -> CMTimeRange {
        CMTimeRange(start: .invalid, duration: .positiveInfinity)
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
