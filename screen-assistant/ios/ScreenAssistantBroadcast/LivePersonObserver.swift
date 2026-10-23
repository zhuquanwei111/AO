import Foundation
import ReplayKit
import CoreImage
import CoreMedia
import CoreVideo
import ImageIO
import Vision

/// 仅在运营场次中对已交付视频帧做低频 Vision 姿态观察；单在途、小图、无画面不作离镜结论。
final class LivePersonObserver {
    private let state = DispatchQueue(label: "com.screenassistant.live-person.state")
    private let vision = DispatchQueue(label: "com.screenassistant.live-person.vision", qos: .utility)
    private let context = CIContext(options: [.useSoftwareRenderer: false])
    private var nextSampleAt: TimeInterval = 0
    private var inFlight = false
    private var downCount = 0
    private var downStartedAtMS: Int64 = 0
    private var lastPromptAt: TimeInterval = 0

    func observe(_ sample: CMSampleBuffer) {
        guard LiveSessionState.isEnabled(), ControlState.userWantsCapture(),
              CMSampleBufferIsValid(sample), CMSampleBufferDataIsReady(sample),
              let pixels = CMSampleBufferGetImageBuffer(sample) else {
            state.async { self.resetPose(); self.nextSampleAt = 0 }
            return
        }
        let uptime = ProcessInfo.processInfo.systemUptime
        let shouldSample = state.sync { () -> Bool in
            guard !inFlight, uptime >= nextSampleAt else { return false }
            nextSampleAt = uptime + 3
            inFlight = true
            return true
        }
        guard shouldSample else { return }
        var orientation = CGImagePropertyOrientation.up
        if let raw = CMGetAttachment(sample, key: RPVideoSampleOrientationKey as CFString,
                                     attachmentModeOut: nil) as? NSNumber {
            orientation = CGImagePropertyOrientation(rawValue: raw.uint32Value) ?? .up
        }
        guard let image = autoreleasepool(invoking: { () -> CGImage? in
            var frame = CIImage(cvPixelBuffer: pixels).oriented(orientation)
            let extent = frame.extent
            guard extent.width > 0, extent.height > 0 else { return nil }
            // 复用 M1 诊断页持久化的独立选区；只读其位置，不受诊断默认关闭/60秒窗控制。
            let region = InputDiagnosticsStore.configuration().region.clamped
            let crop = CGRect(
                x: extent.minX + CGFloat(region.x) * extent.width,
                y: extent.minY + CGFloat(1 - region.y - region.height) * extent.height,
                width: CGFloat(region.width) * extent.width,
                height: CGFloat(region.height) * extent.height
            ).intersection(extent)
            guard !crop.isNull, crop.width > 0, crop.height > 0 else { return nil }
            frame = frame.cropped(to: crop)
            let scale = min(1, 512 / max(frame.extent.width, frame.extent.height))
            frame = frame.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
            return context.createCGImage(frame, from: frame.extent)
        }) else {
            state.async { self.inFlight = false; self.resetPose() }
            return
        }
        let observedAtMS = Int64(Date().timeIntervalSince1970 * 1000)
        vision.async { [weak self] in
            guard let self else { return }
            let request = VNDetectHumanBodyPoseRequest()
            var result = (false, 0.0)
            do {
                try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
                result = Self.isHeadDown(request.results?.first)
            } catch {
                result = (false, 0)
            }
            self.state.async {
                self.inFlight = false
                guard LiveSessionState.isEnabled(), ControlState.userWantsCapture() else {
                    self.resetPose()
                    return
                }
                guard result.0 else {
                    // 无姿态或不可读帧只重置连续窗口；不把它解释成离镜。
                    self.resetPose()
                    return
                }
                if self.downCount == 0 { self.downStartedAtMS = observedAtMS }
                self.downCount += 1
                let elapsed = observedAtMS - self.downStartedAtMS
                guard self.downCount >= 6, elapsed >= 15_000,
                      self.lastPromptAt == 0 || ProcessInfo.processInfo.systemUptime - self.lastPromptAt >= 90 else { return }
                self.lastPromptAt = ProcessInfo.processInfo.systemUptime
                let started = self.downStartedAtMS
                let count = self.downCount
                guard let baseURL = SharedConfig.serverBaseURL(),
                      let client = LiveOperationsClient(baseURL: baseURL) else { return }
                client.submitPersonSignal(startedAtMS: started, observedAtMS: observedAtMS,
                                          confidence: result.1, sampleCount: count) { }
            }
        }
    }

    private func resetPose() {
        downCount = 0
        downStartedAtMS = 0
    }

    private static func isHeadDown(_ observation: VNHumanBodyPoseObservation?) -> (Bool, Double) {
        guard let observation,
              let nose = try? observation.recognizedPoint(.nose),
              let left = try? observation.recognizedPoint(.leftShoulder),
              let right = try? observation.recognizedPoint(.rightShoulder),
              nose.confidence >= 0.45, left.confidence >= 0.45, right.confidence >= 0.45 else {
            return (false, 0)
        }
        let shouldersY = (left.location.y + right.location.y) / 2
        let verticalGap = nose.location.y - shouldersY
        let confidence = min(nose.confidence, min(left.confidence, right.confidence))
        return (verticalGap < 0.065, Double(confidence))
    }
}
