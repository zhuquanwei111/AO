import Foundation
import ReplayKit
import CoreImage
import CoreGraphics
import CoreMedia
import CoreVideo
import ImageIO
import Vision
import AudioToolbox

/// 只检查系统已经交付的样本，不启动相机、录音、音频播放或网络请求。
/// 回调同步计数；每秒至多一个自有小图作业，音频每源每半秒至多抽样1024值。
final class InputDiagnosticsSampler {
    private let queue = DispatchQueue(label: "com.screenassistant.input-diagnostics.state")
    private let imageQueue = DispatchQueue(label: "com.screenassistant.input-diagnostics.image", qos: .utility)
    private let context: CIContext
    private var configuration = InputDiagnosticConfiguration.disabled
    private var broadcastPhase = DiagnosticPhase.finished
    private var state = InputDiagnosticAccumulator(requestID: "disabled", phase: .disabled, now: 0)
    private var nextConfigurationRead: TimeInterval = 0
    private var nextPublish: TimeInterval = 0
    private var nextImage: TimeInterval = 0
    private var nextAppAudio: TimeInterval = 0
    private var nextMicrophone: TimeInterval = 0
    private var imageInFlight = false
    private var previousLuminances: [Double]?

    init(context: CIContext) { self.context = context }

    func transition(to phase: DiagnosticPhase) {
        queue.sync {
            broadcastPhase = phase
            let latest = InputDiagnosticsStore.configuration()
            if phase == .finished, latest.id == configuration.id {
                // 保留本段数值供回到App查看，但停止接收并拒绝所有迟到任务。
                state.snapshot.phase = .finished
                publish(force: true)
            } else {
                configuration = latest
                reset()
            }
        }
    }

    private func reset() {
        state.reset(requestID: configuration.id,
                    phase: configuration.enabled ? broadcastPhase : .disabled,
                    now: Date().timeIntervalSince1970)
        previousLuminances = nil
        nextImage = 0
        nextAppAudio = 0
        nextMicrophone = 0
        // imageInFlight 不重置：旧作业完成前不能再放入新图像。
        publish(force: true)
    }

    private func refreshConfiguration(at uptime: TimeInterval) {
        guard uptime >= nextConfigurationRead else { return }
        nextConfigurationRead = uptime + 0.25
        let latest = InputDiagnosticsStore.configuration()
        if latest.id != configuration.id {
            configuration = latest
            reset()
        }
    }

    func observe(_ sample: CMSampleBuffer, type: RPSampleBufferType) {
        queue.sync {
            let uptime = ProcessInfo.processInfo.systemUptime
            refreshConfiguration(at: uptime)
            guard configuration.enabled, broadcastPhase == .running else { return }
            let now = Date().timeIntervalSince1970
            if let end = configuration.endsAt, now >= end {
                if state.snapshot.phase == .running {
                    state.snapshot.phase = .completed
                    publish(force: true)
                }
                return
            }
            let seconds = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample))
            let pts = seconds.isFinite ? seconds : nil
            switch type {
            case .video:
                state.record(.video, at: now, pts: pts)
                inspectVideo(sample, at: now, uptime: uptime)
            case .audioApp:
                state.record(.appAudio, at: now, pts: pts)
                if uptime >= nextAppAudio {
                    nextAppAudio = uptime + 0.5
                    Self.inspectAudio(sample, summary: &state.snapshot.appAudio, at: now)
                }
            case .audioMic:
                state.record(.microphone, at: now, pts: pts)
                if uptime >= nextMicrophone {
                    nextMicrophone = uptime + 0.5
                    Self.inspectAudio(sample, summary: &state.snapshot.microphone, at: now)
                }
            @unknown default:
                state.snapshot.issue = "unknown_sample_type"
            }
            publish()
        }
    }

    private func publish(force: Bool = false) {
        let uptime = ProcessInfo.processInfo.systemUptime
        guard force || uptime >= nextPublish else { return }
        nextPublish = uptime + 1
        state.snapshot.updatedAt = Date().timeIntervalSince1970
        InputDiagnosticsStore.publish(state.snapshot)
    }

    private func inspectVideo(_ sample: CMSampleBuffer, at now: TimeInterval, uptime: TimeInterval) {
        guard CMSampleBufferIsValid(sample), CMSampleBufferDataIsReady(sample),
              let pixels = CMSampleBufferGetImageBuffer(sample) else {
            state.snapshot.video.lastBufferReady = false
            state.snapshot.video.issue = "video_buffer_unavailable"
            return
        }
        state.snapshot.video.lastBufferReady = true
        state.snapshot.video.issue = nil
        state.snapshot.video.width = CVPixelBufferGetWidth(pixels)
        state.snapshot.video.height = CVPixelBufferGetHeight(pixels)
        guard uptime >= nextImage, !imageInFlight else { return }
        nextImage = uptime + 1
        var orientation = CGImagePropertyOrientation.up
        if let value = CMGetAttachment(sample, key: RPVideoSampleOrientationKey as CFString,
                                       attachmentModeOut: nil) as? NSNumber {
            orientation = CGImagePropertyOrientation(rawValue: value.uint32Value) ?? .up
        }
        // 在回调内创建自有小图；异步闭包不捕获 sample / pixel buffer / CIImage。
        guard let image = autoreleasepool(invoking: { () -> CGImage? in
            var frame = CIImage(cvPixelBuffer: pixels).oriented(orientation)
            let extent = frame.extent
            guard extent.width > 0, extent.height > 0 else { return nil }
            let r = configuration.region.clamped
            let crop = CGRect(x: extent.minX + CGFloat(r.x) * extent.width,
                              y: extent.minY + CGFloat(1 - r.y - r.height) * extent.height,
                              width: CGFloat(r.width) * extent.width,
                              height: CGFloat(r.height) * extent.height).intersection(extent)
            guard !crop.isNull, crop.width > 0, crop.height > 0 else { return nil }
            frame = frame.cropped(to: crop)
            let scale = min(1, 192 / max(crop.width, crop.height))
            frame = frame.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
            return context.createCGImage(frame, from: frame.extent)
        }) else {
            state.snapshot.video.analysisIssue = "region_render_failed"
            return
        }
        imageInFlight = true
        let segment = state.snapshot.segmentID
        let previous = previousLuminances
        imageQueue.async { [weak self] in
            let result = autoreleasepool { () -> ([Double]?, Int?, String?) in
                let levels = Self.luminances(image)
                let request = VNDetectFaceRectanglesRequest()
                do {
                    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
                    return (levels, request.results?.count, levels == nil ? "luminance_failed" : nil)
                } catch {
                    return (levels, nil, "vision_\((error as NSError).code)")
                }
            }
            guard let self else { return }
            self.queue.async {
                self.imageInFlight = false
                let latest = InputDiagnosticsStore.configuration()
                if latest.id != self.configuration.id {
                    self.configuration = latest
                    self.reset()
                }
                guard self.state.snapshot.segmentID == segment,
                      self.state.snapshot.phase == .running else { return }
                if let end = self.configuration.endsAt, Date().timeIntervalSince1970 >= end {
                    self.state.snapshot.phase = .completed
                    self.publish(force: true)
                    return
                }
                self.state.snapshot.video.analysisIssue = result.2
                if let levels = result.0,
                   let evidence = ImageDiagnosticEvidence(luminances: levels, previous: previous,
                                                          faceCount: result.1, at: now) {
                    self.state.applyImage(evidence, segmentID: segment)
                    self.previousLuminances = levels
                }
                self.publish()
            }
        }
    }

    private static func luminances(_ image: CGImage) -> [Double]? {
        var bytes = [UInt8](repeating: 0, count: 256)
        let drawn = bytes.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(data: buffer.baseAddress, width: 16, height: 16,
                                          bitsPerComponent: 8, bytesPerRow: 16,
                                          space: CGColorSpaceCreateDeviceGray(),
                                          bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: 16, height: 16))
            return true
        }
        return drawn ? bytes.map { Double($0) / 255 } : nil
    }

    private static func inspectAudio(_ sample: CMSampleBuffer,
                                     summary: inout AudioDiagnosticSummary, at now: TimeInterval) {
        // 失败即清当前强弱值；不能沿用以前非静音或把失败当静音。
        summary.level = nil
        summary.measuredAt = nil
        guard CMSampleBufferIsValid(sample), CMSampleBufferDataIsReady(sample),
              let description = CMSampleBufferGetFormatDescription(sample),
              let format = CMAudioFormatDescriptionGetStreamBasicDescription(description)?.pointee else {
            summary.issue = "audio_format_unavailable"
            return
        }
        summary.sampleRate = format.mSampleRate.isFinite && format.mSampleRate > 0
            && format.mSampleRate < 1_000_000 ? format.mSampleRate : nil
        summary.channels = Int(format.mChannelsPerFrame)
        let floating = format.mFormatFlags & kAudioFormatFlagIsFloat != 0
        let signed = format.mFormatFlags & kAudioFormatFlagIsSignedInteger != 0
        guard format.mFormatID == kAudioFormatLinearPCM, format.mSampleRate.isFinite,
              summary.sampleRate != nil, (1...8).contains(Int(format.mChannelsPerFrame)),
              format.mFormatFlags & kAudioFormatFlagIsBigEndian == 0,
              format.mFormatFlags & kAudioFormatFlagIsPacked != 0,
              (floating && format.mBitsPerChannel == 32) || (!floating && signed && format.mBitsPerChannel == 16) else {
            summary.issue = "unsupported_pcm"
            return
        }
        guard CMSampleBufferGetNumSamples(sample) > 0, CMSampleBufferGetNumSamples(sample) <= 48000 else {
            summary.issue = "audio_window_unsupported"
            return
        }
        let capacity = Int(format.mChannelsPerFrame)
        let byteCount = MemoryLayout<AudioBufferList>.size + (capacity - 1) * MemoryLayout<AudioBuffer>.stride
        let memory = UnsafeMutableRawPointer.allocate(byteCount: byteCount,
                                                     alignment: MemoryLayout<AudioBufferList>.alignment)
        defer { memory.deallocate() }
        let list = memory.bindMemory(to: AudioBufferList.self, capacity: 1)
        list.initialize(to: AudioBufferList(mNumberBuffers: 0,
                                            mBuffers: AudioBuffer(mNumberChannels: 0, mDataByteSize: 0, mData: nil)))
        var retainedBlock: CMBlockBuffer?
        defer { withExtendedLifetime(retainedBlock) {} }
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sample, bufferListSizeNeededOut: nil, bufferListOut: list, bufferListSize: byteCount,
            blockBufferAllocator: kCFAllocatorDefault, blockBufferMemoryAllocator: kCFAllocatorDefault,
            flags: UInt32(kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment), blockBufferOut: &retainedBlock)
        guard status == noErr else {
            summary.issue = "audio_buffer_\(status)"
            return
        }
        guard list.pointee.mNumberBuffers <= UInt32(capacity) else {
            summary.issue = "audio_buffer_layout"
            return
        }
        let buffers = UnsafeMutableAudioBufferListPointer(list)
        var values: [Double] = []
        values.reserveCapacity(1024)
        for buffer in buffers {
            guard let data = buffer.mData else { continue }
            let elementBytes = floating ? MemoryLayout<Float>.size : MemoryLayout<Int16>.size
            let count = Int(buffer.mDataByteSize) / elementBytes
            let budget = max(1, 1024 / max(buffers.count, 1))
            let step = max(1, (count + budget - 1) / budget)
            for index in stride(from: 0, to: count, by: step) {
                guard values.count < 1024 else { break }
                values.append(floating ? Double(data.assumingMemoryBound(to: Float.self)[index])
                              : Double(data.assumingMemoryBound(to: Int16.self)[index]) / 32768)
            }
        }
        guard let level = AudioDiagnosticLevel(samples: values) else {
            summary.issue = "audio_samples_invalid"
            return
        }
        summary.measure(level, at: now)
    }
}
