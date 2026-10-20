import ReplayKit
import Vision
import CoreImage
import CoreGraphics
import CoreVideo
import CoreMedia
import UserNotifications
import Foundation

/// 广播上传扩展主类：跨应用采集 → 方向转正 → ROI 内存裁剪 → 中文 OCR →
/// 基础格式化 → POST /v1/answer → 本地通知展示。
///
/// 隐私与生命周期约束：
///   - 视频帧仅存在于本方法调用栈与 CIImage/VNImageRequestHandler 内存中，
///     方法返回后不保留任何画面引用（官方文档要求），不落盘。
///   - 不持久化句子/回复；每轮的句子与回复只活到本轮结束。
///   - 一轮 = 帧→OCR→请求→响应，全程 roundActive；期间到达的帧直接跳过
///     （PRD：“下一轮忙则跳过以免堆积”）。
///   - 空识别 / 网络失败 / 422 / 503 / 504：本轮静默丢弃，下一轮继续。
///   - 204：不刷新当前展示（通知层面 = 不发新通知）。
final class SampleHandler: RPBroadcastSampleHandler {

    /// 所有轮次状态只在此串行队列上读写。
    /// （ReplayKit 回调本身串行，但 OCR/网络完成闭包在后台队列触发。）
    private let stateQueue = DispatchQueue(label: "com.screenassistant.broadcast.state")

    /// 下一轮可开始的最早时刻（CFAbsoluteTime）。仅在 stateQueue 内读写。
    /// 逐帧路径只做“时间未到直接丢弃”，把每帧共享状态读取压到每轮一次
    /// （扩展 jetsam 硬上限约 50MB、切 App 瞬间系统会堆积 6-8 个待处理缓冲——
    /// R/36 调研数据：Apple 论坛 thread/651367；本实现不保留任何 sampleBuffer
    /// 引用，逐帧成本 = 一次同步块内 3 次比较）。
    private var nextRoundEligibleTime: CFAbsoluteTime = 0
    /// 一轮是否正在进行（帧→OCR→请求→响应 全链路）。
    private var roundActive = false

    private let ciContext = CIContext(options: [.useSoftwareRenderer: false])

    // MARK: - 生命周期

    override func broadcastStarted(withSetupInfo setupInfo: [String: NSObject]?) {
        stateQueue.sync {
            nextRoundEligibleTime = 0
            roundActive = false
        }
    }

    override func broadcastPaused() {
        // 暂停：无处理；恢复后节流逻辑自然继续。
    }

    override func broadcastResumed() {}

    override func broadcastFinished() {
        stateQueue.sync {
            roundActive = false
        }
    }

    // MARK: - 帧处理

    override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer,
                                      with sampleBufferType: RPSampleBufferType) {
        guard sampleBufferType == .video else { return }

        // 闸门（stateQueue 内，纯比较，无共享状态读取）：一轮进行中、或距下一轮
        // 时间未到 → 直接丢弃本帧；识别开关关闭时每 1s 复查一次（及时响应启停）。
        let now = CFAbsoluteTimeGetCurrent()
        var startRound = false
        stateQueue.sync {
            if !roundActive, now >= nextRoundEligibleTime {
                if ControlState.userWantsCapture() {
                    roundActive = true
                    startRound = true
                } else {
                    nextRoundEligibleTime = now + 1
                }
            }
        }
        guard startRound else { return }

        // 帧内存处理链：sampleBuffer 引用在方法返回后即失效（官方文档明确），
        // 因此在返回前把需要的内容渲染为独立 CGImage。
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
            endRound()
            return
        }

        // 1) 方向转正：RPVideoSampleOrientationKey 附件 → CGImagePropertyOrientation
        //    （Telegram-iOS / Cap 生产实现同款写法）
        var orientation = CGImagePropertyOrientation.up
        if let raw = CMGetAttachment(sampleBuffer,
                                     key: RPVideoSampleOrientationKey as CFString,
                                     attachmentModeOut: nil) as? NSNumber {
            orientation = CGImagePropertyOrientation(rawValue: raw.uint32Value) ?? .up
        }

        // oriented() 对 .left/.right 会交换宽高，extent 在其后读取才是竖屏语义尺寸。
        var ciImage = CIImage(cvPixelBuffer: pixelBuffer).oriented(orientation)

        // 2) ROI 内存裁剪：归一化矩形（左上原点）→ 像素坐标（CIImage 左下原点）。
        let extent = ciImage.extent
        guard extent.width > 0, extent.height > 0 else {
            endRound()
            return
        }
        let roi = SharedConfig.roi()
        let cropRect = CGRect(
            x: extent.minX + roi.minX * extent.width,
            y: extent.minY + (1 - roi.maxY) * extent.height, // 左上原点 → 左下原点
            width: roi.width * extent.width,
            height: roi.height * extent.height
        ).intersection(extent)
        guard !cropRect.isNull, cropRect.width > 0, cropRect.height > 0 else {
            endRound()
            return
        }
        ciImage = ciImage.cropped(to: cropRect)

        // 3) 下采样到最长边限制（扩展 jetsam 硬上限约 50MB 的内存保护，
        //    R/36 调研：切 App 瞬间系统会堆积多个待处理缓冲）
        let longSide = max(ciImage.extent.width, ciImage.extent.height)
        if longSide > SharedConfig.maxCropPixelDimension {
            let scale = SharedConfig.maxCropPixelDimension / longSide
            ciImage = ciImage.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        }

        // 4) 渲染为独立 CGImage；此后不再引用 sampleBuffer/pixelBuffer。
        guard let cgImage = ciContext.createCGImage(ciImage, from: ciImage.extent) else {
            endRound()
            return
        }

        // 5) OCR（后台队列；识别文本仅存在于闭包链内存）
        recognizeText(in: cgImage) { [weak self] recognizedText in
            guard let self else { return }

            // 6) 基础格式化；空白/超限 → 空轮（不发请求不发通知）
            guard let sentence = SentenceFormatter.sentence(fromOCRText: recognizedText),
                  sentence.count <= SharedConfig.maxSentenceLength else {
                self.endRound()
                return
            }

            // 7) 服务未配置 → 本轮丢弃（主 App 设置页有明确提示）
            guard let endpoint = AnswerEndpoint(timeout: SharedConfig.requestTimeoutSeconds()) else {
                self.endRound()
                return
            }
            ControlState.bumpRoundCounter()
            DarwinSignal.post(DarwinSignal.roundDidFinish)

            // 8) 请求；响应回来后本轮结束。
            endpoint.request(sentence: sentence) { [weak self] outcome in
                guard let self else { return }
                defer { self.endRound() }
                switch outcome {
                case .ok(let reply):
                    // 只展示本轮回复；不做本地去重或历史拼接。
                    // 展示路径分流：PiP 活跃且主 App 心跳新鲜 → 瞬态邮箱 + Darwin
                    // 信号（主 App 渲染进 PiP 悬浮条）；否则 → 本地通知（回退路径）。
                    // 主 App 挂起/被杀时心跳超时，自动落到通知，保证新回复仍可展示。
                    if ControlState.pipLivenessIsFresh() {
                        ReplyTransportFactory.make().publish(reply: reply)
                        DarwinSignal.post(DarwinSignal.newReplyAvailable)
                    } else {
                        NotificationPresenter.present(reply: reply)
                    }
                case .duplicate:
                    // 204：保留当前展示 → 不发新通知
                    break
                case .discarded:
                    // 网络/422/503/504/超时：本轮丢弃，静默，下轮继续
                    break
                }
            }
        }
    }

    /// 结束一轮：清 roundActive 并按配置间隔排定下一轮可开始时刻。
    private func endRound() {
        stateQueue.sync {
            roundActive = false
            nextRoundEligibleTime = CFAbsoluteTimeGetCurrent()
                + SharedConfig.intervalSeconds()
        }
    }

    // MARK: - Vision OCR

    /// 对裁剪后的 CGImage 执行中文 OCR。
    private func recognizeText(in cgImage: CGImage,
                               completion: @escaping (String) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let request = VNRecognizeTextRequest { request, error in
                guard error == nil,
                      let observations = request.results as? [VNRecognizedTextObservation] else {
                    completion("")
                    return
                }
                // Vision 不保证返回阅读顺序；按 boundingBox 近似排序
                //（自上而下、同行自左向右；boundingBox 为左下原点归一化坐标）。
                let sorted = observations.sorted { a, b in
                    let dy = abs(a.boundingBox.minY - b.boundingBox.minY)
                    if dy > 0.01 { return a.boundingBox.minY > b.boundingBox.minY }
                    return a.boundingBox.minX < b.boundingBox.minX
                }
                // accurate 路径对中文按“行/行片段”切分（官方文档）；
                // 行顺序拼接原始文本，格式化交给 SentenceFormatter。
                var lines: [String] = []
                for obs in sorted {
                    if let candidate = obs.topCandidates(1).first {
                        lines.append(candidate.string)
                    }
                }
                completion(lines.joined(separator: "\n"))
            }
            request.recognitionLevel = .accurate
            request.usesLanguageCorrection = true
            // 官方：简/繁中文识别语言为 zh-Hans / zh-Hant，须为前两个元素，
            // 英语是唯一可搭配语言。
            request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]

            let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
            do {
                try handler.perform([request])
            } catch {
                completion("")
            }
        }
    }
}

/// 展示回退层：本地通知（PRD 允许的降级路径）。
/// 隐私边界（交付说明同步标注）：
///   - 通知正文包含回复文本，会经过 iOS 通知系统（横幅 + 通知中心保留），
///     系统可能保留/摘要通知内容——不宣称满足“零文本系统留存”。
///   - 用户可在系统设置关闭“显示预览”，或在本 App 设置中关闭“通知含正文”。
enum NotificationPresenter {
    static func present(reply: String) {
        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { settings in
            guard settings.authorizationStatus == .authorized ||
                  settings.authorizationStatus == .provisional else { return }
            let content = UNMutableNotificationContent()
            content.title = "屏识AI助手"
            content.body = SharedConfig.notificationShowsContent()
                ? String(reply.prefix(180))
                : "有新的识别回复（已隐藏正文）"
            // 固定 identifier：新通知覆盖旧通知，通知中心不堆积回复历史。
            let request = UNNotificationRequest(
                identifier: "screen-assistant.reply",
                content: content,
                trigger: nil
            )
            center.add(request)
        }
    }
}
