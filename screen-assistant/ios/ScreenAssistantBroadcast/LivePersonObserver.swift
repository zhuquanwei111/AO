import Foundation
import ReplayKit
import CoreImage
import CoreMedia
import CoreVideo
import ImageIO

/// 低频、单在途人物观察。默认保留旧head_down出口；新观测须由集成方接线。
final class LivePersonObserver {
    private struct SourceToken: Equatable {
        let sessionID: String
        let credentialToken: String
        let captureGeneration: Int
        let broadcastSegment: String
        let region: DiagnosticRegion
        let orientation: UInt32
    }
    private let state = DispatchQueue(label: "com.screenassistant.live-person.state")
    private let vision = DispatchQueue(label: "com.screenassistant.live-person.vision", qos: .utility)
    private let context = CIContext(options: [.useSoftwareRenderer: false])
    /// 在Vision串行队列上调用；只接数值，处理应立即返回，不执行阻塞网络/媒体存储。
    private let onObservation: ((LivePersonObservation) -> Void)?
    private var nextSampleAt: TimeInterval = 0
    private var inFlight = false
    private var sourceToken: SourceToken?
    private var segmentID = UUID().uuidString
    private var sequence = 0
    private var previousGray: [Double]?
    private var lastPTS: Double?
    private var lastSampleUptime: TimeInterval?
    private var headWindow = PersonHeadWindow()
    private var pendingSignal: URLSessionDataTask?
    private var signalID: UUID?
    private let network: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 20
        config.timeoutIntervalForResource = 25
        return URLSession(configuration: config)
    }()

    init(onObservation: ((LivePersonObservation) -> Void)? = nil) {
        self.onObservation = onObservation
    }

    /// 可供统一生命周期接线直接调用；不提前释放旧Vision槽位。
    func reset() { state.sync { invalidate() } }

    private func invalidate() {
        sourceToken = nil
        segmentID = UUID().uuidString
        sequence = 0
        previousGray = nil
        lastPTS = nil
        lastSampleUptime = nil
        nextSampleAt = 0
        headWindow.start(segmentID)
        pendingSignal?.cancel()
        pendingSignal = nil
        signalID = nil
    }

    private static func token(orientation: UInt32) -> SourceToken? {
        guard LiveSessionState.isEnabled(), ControlState.userWantsCapture(),
              let credentials = LiveSessionState.credentials(),
              let snapshot = InputDiagnosticsStore.snapshot(),
              snapshot.phase != .paused, snapshot.phase != .finished else { return nil }
        // M1的生命周期转换即使诊断关闭也更换segment；不要求开启60秒检查。
        return SourceToken(sessionID: credentials.sessionID, credentialToken: credentials.token,
                           captureGeneration: ControlState.captureGeneration(),
                           broadcastSegment: snapshot.segmentID,
                           region: InputDiagnosticsStore.configuration().region.clamped,
                           orientation: orientation)
    }

    func observe(_ sample: CMSampleBuffer) {
        guard LiveSessionState.isEnabled(), ControlState.userWantsCapture(),
              CMSampleBufferIsValid(sample), CMSampleBufferDataIsReady(sample),
              let pixels = CMSampleBufferGetImageBuffer(sample) else {
            reset()
            return
        }
        let uptime = ProcessInfo.processInfo.systemUptime
        let reserved = state.sync { () -> Bool in
            guard !inFlight, uptime >= nextSampleAt else { return false }
            nextSampleAt = uptime + 3
            inFlight = true
            return true
        }
        guard reserved else { return }
        var orientation = CGImagePropertyOrientation.up
        if let raw = CMGetAttachment(sample, key: RPVideoSampleOrientationKey as CFString,
                                     attachmentModeOut: nil) as? NSNumber {
            orientation = CGImagePropertyOrientation(rawValue: raw.uint32Value) ?? .up
        }
        guard let token = Self.token(orientation: orientation.rawValue) else {
            state.sync { inFlight = false; invalidate(); nextSampleAt = uptime + 3 }
            return
        }
        let seconds = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample))
        let pts = seconds.isFinite ? seconds : nil
        let work = state.sync { () -> (String, Int, [Double]?, Bool) in
            if sourceToken != token || lastSampleUptime.map({ uptime - $0 > 6.5 }) == true ||
                (pts != nil && lastPTS != nil && pts! < lastPTS!) {
                invalidate()
                sourceToken = token
            }
            let fresh = pts != nil && (lastPTS == nil || pts! > lastPTS!)
            if fresh { lastPTS = pts }
            lastSampleUptime = uptime
            nextSampleAt = uptime + 3
            sequence = min(sequence + 1, Int.max - 1)
            return (segmentID, sequence, previousGray, fresh)
        }
        guard let image = autoreleasepool(invoking: { () -> CGImage? in
            var frame = CIImage(cvPixelBuffer: pixels).oriented(orientation)
            let extent = frame.extent
            guard extent.width > 0, extent.height > 0 else { return nil }
            let r = token.region
            let crop = CGRect(x: extent.minX + CGFloat(r.x) * extent.width,
                              y: extent.minY + CGFloat(1 - r.y - r.height) * extent.height,
                              width: CGFloat(r.width) * extent.width,
                              height: CGFloat(r.height) * extent.height).intersection(extent)
            guard !crop.isNull, crop.width >= 96, crop.height >= 96 else { return nil }
            frame = frame.cropped(to: crop)
            let scale = min(1, 512 / max(crop.width, crop.height))
            frame = frame.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
            return context.createCGImage(frame, from: frame.extent)
        }) else {
            state.sync { inFlight = false; invalidate(); nextSampleAt = uptime + 3 }
            return
        }
        let observedAtMS = Int64(Date().timeIntervalSince1970 * 1000)
        vision.async { [weak self] in
            guard let self else { return }
            var result = autoreleasepool { PersonVisionFeatures.inspect(image, previous: work.2) }
            if !work.3 { result.quality = "source_timestamp_unknown_or_stalled" }
            let currentToken = Self.token(orientation: token.orientation)
            let accepted = self.state.sync { () -> LivePersonObservation? in
                self.inFlight = false
                guard self.segmentID == work.0, self.sourceToken == token, currentToken == token,
                      ProcessInfo.processInfo.systemUptime - uptime < 6.5 else {
                    self.invalidate()
                    return nil
                }
                self.previousGray = result.gray.isEmpty ? nil : result.gray
                let observation = LivePersonObservation(
                    segmentID: work.0, sequence: work.1, observedAtMS: observedAtMS,
                    uptimeMS: Int64(uptime * 1000), quality: result.quality,
                    personCount: result.count, confidence: result.confidence, personBox: result.box,
                    bodyVector: result.body, faceVector: result.face, headRatio: result.headRatio,
                    headConfidence: result.headConfidence, ptsSeconds: pts)
                if let candidate = self.headWindow.consume(observation) {
                    self.submitLegacy(candidate, token: token)
                }
                return observation
            }
            if let accepted { self.onObservation?(accepted) }
        }
    }

    private func submitLegacy(_ candidate: PersonHeadCandidate, token: SourceToken) {
        guard pendingSignal == nil, let base = SharedConfig.serverBaseURL() else { return }
        let url = ["v1", "live", "sessions", token.sessionID, "signals"].reduce(base) {
            $0.appendingPathComponent($1, isDirectory: false)
        }
        var request = URLRequest(url: url, timeoutInterval: 20)
        request.httpMethod = "POST"
        request.setValue(token.credentialToken, forHTTPHeaderField: "X-Live-Session-Token")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        // Shared客户端在调用时重读凭据；此薄适配固定取样时场次，防旧观测发给新场。
        request.httpBody = try? JSONSerialization.data(withJSONObject: [
            "event_id": UUID().uuidString.lowercased(), "source": "ios_replaykit_vision",
            "signal": "head_down", "confidence": candidate.confidence,
            "sample_count": candidate.sampleCount, "started_at_ms": candidate.startedAtMS,
            "observed_at_ms": candidate.observedAtMS
        ])
        let id = UUID()
        signalID = id
        let task = network.dataTask(with: request) { [weak self] _, _, _ in
            guard let self else { return }
            self.state.async {
                if self.signalID == id { self.pendingSignal = nil; self.signalID = nil }
            }
        }
        pendingSignal = task
        task.resume()
    }
}
