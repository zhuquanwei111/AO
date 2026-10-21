import ReplayKit
import CoreImage
import CoreGraphics
import CoreVideo
import CoreMedia
import Darwin
import Foundation
import os

/// 广播上传扩展主类：跨应用采集 → 方向转正 → ROI 内存裁剪 → 中文 OCR →
/// 基础格式化 → POST /v1/answer → UDS 短回复发送。
///
/// 另含**通道冒烟模式**（R/36 指定的第一个真机验证切片）：
/// 当 App Group 中 `control.shmSmokeMode` 为 true 时，每轮不跑 OCR/网络，
/// 仅向 shm_open 共享内存（`/<group id>/xproc`）写递增序列号——供主 App 的
/// ShmCrossProcessProbeView 验证“扩展→App 真无文件后备通道”是否在本机可用。
/// 该探针只供真机诊断，不切换产品主链路。
///
/// 隐私与生命周期约束：
///   - 视频帧仅存在于本方法调用栈与 CIImage/VNImageRequestHandler 内存中，
///     方法返回后不保留任何画面引用（官方文档要求），不落盘。
///   - 不持久化句子/回复；每轮的句子与回复只活到本轮结束。
///   - 一轮 = 帧→OCR→请求→响应，全程 roundActive；期间到达的帧直接跳过
///     （PRD：“下一轮忙则跳过以免堆积”）。
///   - 空识别 / 网络失败 / 422 / 503 / 504：本轮静默丢弃，下一轮继续。
///   - 204：不刷新当前 PiP 展示。
final class SampleHandler: RPBroadcastSampleHandler {

    /// 所有轮次状态只在此串行队列上读写。
    /// （ReplayKit 回调本身串行，但 OCR/网络完成闭包在后台队列触发。）
    private let stateQueue = DispatchQueue(label: "com.screenassistant.broadcast.state")

    /// 下一轮可开始的最早时刻（CFAbsoluteTime）。仅在 stateQueue 内读写。
    /// 忙时跳过新帧，不保留 sampleBuffer 队列；扩展峰值内存尚未真机测量。
    private var nextRoundEligibleTime: CFAbsoluteTime = 0
    /// 一轮是否正在进行（帧→OCR→请求→响应 全链路）。
    private var roundActive = false
    /// 暂停、继续、结束广播时使旧 OCR 与 HTTP 回调失效。
    private var roundGeneration = 0
    private var isBroadcastPaused = false
    private var activeCaptureGeneration = 0
    private var activeRequest: URLSessionDataTask?
    private var activeOCR: OCRService.CancellationToken?

    private let ciContext = CIContext(options: [.useSoftwareRenderer: false])
    private let memoryLog = Logger(subsystem: "com.screenassistant.broadcast", category: "ocr-memory")
    // 40MB 为待验证工作预算、50MB 为历史风险参考；均非系统硬保证。

    // MARK: - 生命周期

    override func broadcastStarted(withSetupInfo setupInfo: [String: NSObject]?) {
        stateQueue.sync {
            nextRoundEligibleTime = 0
            roundActive = false
            isBroadcastPaused = false
            roundGeneration &+= 1
            cancelActiveWork()
        }
    }

    override func broadcastPaused() {
        stateQueue.sync {
            isBroadcastPaused = true
            roundGeneration &+= 1
            roundActive = false
            cancelActiveWork()
        }
    }

    override func broadcastResumed() {
        stateQueue.sync {
            isBroadcastPaused = false
            roundGeneration &+= 1
            roundActive = false
            nextRoundEligibleTime = 0
            cancelActiveWork()
        }
    }

    override func broadcastFinished() {
        stateQueue.sync {
            roundGeneration &+= 1
            roundActive = false
            isBroadcastPaused = true
            cancelActiveWork()
        }
        ControlState.setUserWantsCapture(false)
    }

    // MARK: - 帧处理

    override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer,
                                      with sampleBufferType: RPSampleBufferType) {
        guard sampleBufferType == .video else { return }

        // 闸门：正在处理、暂停或间隔未到时丢弃当前帧；控制开关每帧读取，
        // 用 generation 及时使旧 OCR/HTTP 结果失效。
        let now = CFAbsoluteTimeGetCurrent()
        var startRound = false
        var generation = 0
        var captureGeneration = 0
        stateQueue.sync {
            let wantsCapture = ControlState.userWantsCapture()
            let configuredGeneration = ControlState.captureGeneration()
            if roundActive && (!wantsCapture || activeCaptureGeneration != configuredGeneration) {
                roundGeneration &+= 1
                roundActive = false
                cancelActiveWork()
            }
            if !isBroadcastPaused, !roundActive, now >= nextRoundEligibleTime {
                if wantsCapture {
                    roundActive = true
                    generation = roundGeneration
                    captureGeneration = configuredGeneration
                    activeCaptureGeneration = configuredGeneration
                    startRound = true
                } else {
                    nextRoundEligibleTime = now + 1
                }
            }
        }
        guard startRound else { return }

        // 通道冒烟模式：仅写 shm 序列号，不跑主链路（R/36 真机验证切片 1）
        if ControlState.shmSmokeMode() {
            runShmSmokeRound(generation: generation)
            return
        }

        logMemory(stage: "frame-start")
        // 把像素缓冲、Core Image 中间对象限制在此 autoreleasepool 内；只让最终
        // ROI CGImage 进入 OCR 单槽，扩展调用返回后释放 sample/pixel buffer。
        guard let cgImage = autoreleasepool(invoking: { makeOCRImage(from: sampleBuffer) }) else {
            endRound(generation: generation)
            return
        }
        logMemory(stage: "roi-rendered")

        // 5) OCR（架构无关组件，后台队列；识别文本仅存在于闭包链内存）
        guard let ocr = OCRService.recognizeText(in: cgImage, completion: { [weak self] recognizedText in
            guard let self else { return }
            self.logMemory(stage: "ocr-finished")
            guard self.isCurrentRound(generation, captureGeneration: captureGeneration) else {
                self.endRound(generation: generation)
                return
            }

            // 6) 基础格式化；空白/超限 → 空轮（不发请求不发通知）
            guard let sentence = SentenceFormatter.sentence(fromOCRText: recognizedText),
                  sentence.count <= SharedConfig.maxSentenceLength else {
                self.endRound(generation: generation)
                return
            }

            // 7) 服务未配置 → 本轮丢弃（主 App 设置页有明确提示）
            guard let endpoint = AnswerEndpoint(timeout: SharedConfig.requestTimeoutSeconds()) else {
                self.endRound(generation: generation)
                return
            }
            guard self.isCurrentRound(generation, captureGeneration: captureGeneration) else {
                self.endRound(generation: generation)
                return
            }
            ControlState.bumpRoundCounter()

            // 8) 请求；响应回来后本轮结束。
            let request = endpoint.request(sentence: sentence) { [weak self] outcome in
                guard let self else { return }
                guard self.isCurrentRound(generation, captureGeneration: captureGeneration) else {
                    self.endRound(generation: generation)
                    return
                }
                defer { self.endRound(generation: generation) }
                switch outcome {
                case .ok(let reply):
                    // 只发送短回复；PiP 未监听时 UDS 有界重试后丢弃载荷。
                    ReplyTransportFactory.make().send(reply: reply)
                case .duplicate:
                    // 204：保留当前展示 → 不发新通知
                    break
                case .discarded:
                    // 网络/422/503/504/超时：本轮丢弃，静默，下轮继续
                    break
                }
            }
            self.stateQueue.sync {
                if self.roundActive && !self.isBroadcastPaused &&
                    self.roundGeneration == generation &&
                    self.activeCaptureGeneration == captureGeneration &&
                    ControlState.userWantsCapture() &&
                    ControlState.captureGeneration() == captureGeneration {
                    self.activeRequest = request
                } else {
                    request?.cancel()
                }
            }
        }) else {
            endRound(generation: generation)
            return
        }
        stateQueue.sync {
            if roundActive && !isBroadcastPaused && roundGeneration == generation &&
                activeCaptureGeneration == captureGeneration {
                activeOCR = ocr
            } else {
                ocr.cancel()
            }
        }
        logMemory(stage: "ocr-submitted")
    }

    /// 结束一轮：清 roundActive 并按配置间隔排定下一轮可开始时刻。
    private func isCurrentRound(_ generation: Int, captureGeneration: Int) -> Bool {
        stateQueue.sync {
            !isBroadcastPaused && roundGeneration == generation &&
                ControlState.userWantsCapture() &&
                ControlState.captureGeneration() == captureGeneration
        }
    }

    private func endRound(generation: Int) {
        stateQueue.sync {
            guard roundGeneration == generation else { return }
            roundActive = false
            activeOCR = nil
            activeRequest = nil
            nextRoundEligibleTime = CFAbsoluteTimeGetCurrent()
                + SharedConfig.intervalSeconds()
        }
    }

    /// Core Image 只在采集回调中读取当前帧；ROI 先裁剪再限制最长边，坐标统一限制在 extent。
    private func makeOCRImage(from sampleBuffer: CMSampleBuffer) -> CGImage? {
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return nil }
        var orientation = CGImagePropertyOrientation.up
        if let raw = CMGetAttachment(sampleBuffer,
                                     key: RPVideoSampleOrientationKey as CFString,
                                     attachmentModeOut: nil) as? NSNumber {
            orientation = CGImagePropertyOrientation(rawValue: raw.uint32Value) ?? .up
        }

        var ciImage = CIImage(cvPixelBuffer: pixelBuffer).oriented(orientation)
        let extent = ciImage.extent
        guard extent.width > 0, extent.height > 0 else { return nil }
        let roi = SharedConfig.roi()
        let normalized = CGRect(
            x: min(max(roi.minX, 0), 1),
            y: min(max(1 - roi.maxY, 0), 1),
            width: min(max(roi.width, 0), 1),
            height: min(max(roi.height, 0), 1)
        )
        let cropRect = CGRect(
            x: extent.minX + normalized.minX * extent.width,
            y: extent.minY + normalized.minY * extent.height,
            width: normalized.width * extent.width,
            height: normalized.height * extent.height
        ).intersection(extent)
        guard !cropRect.isNull, cropRect.width > 0, cropRect.height > 0 else { return nil }
        ciImage = ciImage.cropped(to: cropRect)

        let longSide = max(ciImage.extent.width, ciImage.extent.height)
        let maxDimension = SharedConfig.maxCropPixelDimension
        if longSide > maxDimension {
            let scale = maxDimension / longSide
            ciImage = ciImage.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        }
        return ciContext.createCGImage(ciImage, from: ciImage.extent)
    }

    /// 在 stateQueue 内调用；只取消句柄与网络任务，不保存图像或文字诊断内容。
    private func cancelActiveWork() {
        activeOCR?.cancel()
        activeOCR = nil
        activeRequest?.cancel()
        activeRequest = nil
        logMemory(stage: "work-cancelled")
    }

    /// 仅写固定阶段名与当前进程常驻内存数值，供 Instruments/设备日志配合采样。
    private func logMemory(stage: String) {
        var info = mach_task_basic_info()
        var count = mach_msg_type_number_t(MemoryLayout<mach_task_basic_info>.size) / 4
        let result = withUnsafeMutablePointer(to: &info) { pointer in
            pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(MACH_TASK_BASIC_INFO), $0, &count)
            }
        }
        guard result == KERN_SUCCESS else { return }
        let rssMB = Double(info.resident_size) / 1_048_576
        memoryLog.info("mem stage=\(stage, privacy: .public) rss_mb=\(rssMB, format: .fixed(precision: 1), privacy: .public)")
    }

    // MARK: - 通道冒烟（真机验证切片 1，R/36 指定）

    /// 向 shm_open 命名共享内存写递增序列号（与 ShmCrossProcessProbeView 布局一致：
    /// 前 8 字节 Int64 little-endian）。命名格式 `<group id>/xproc`（Apple 规范）。
    private func runShmSmokeRound(generation: Int) {
        let name = "/\(AppGroup.identifier)/xproc"
        let bytes = 64
        let fd = shm_open(name, O_CREAT | O_RDWR, mode_t(0o600))
        guard fd >= 0 else {
            // shm 在本机不可用（ENOENT 前科）：结束本轮，让主链路冒烟结论记录失败
            endRound(generation: generation)
            return
        }
        guard ftruncate(fd, off_t(bytes)) == 0,
              let map = mmap(nil, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0),
              map != MAP_FAILED else {
            close(fd)
            endRound(generation: generation)
            return
        }
        let seqPtr = map.assumingMemoryBound(to: Int64.self)
        seqPtr.pointee += 1
        munmap(map, bytes)
        close(fd)
        endRound(generation: generation)
    }
}
