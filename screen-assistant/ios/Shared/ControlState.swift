import Foundation

/// 控制与状态标志（App Group UserDefaults 中唯一允许的“状态”数据）。
///
/// 只存布尔/时间戳/计数器等控制状态，不存句子、回复或画面。
struct ControlState {
    enum BroadcastPhase: String, Equatable {
        case unknown
        case waitingForVideo
        case receivingVideo
        case paused
        case finished
    }

    struct BroadcastActivity: Equatable {
        let phase: BroadcastPhase
        let stateChangedAtMs: Int64
        let lastVideoSampleAtMs: Int64
    }

    enum ServicePhase: String, Equatable {
        case unknown
        case connected
        case unreachable
        case respondedWithError
        case configurationInvalid
    }

    struct ServiceConnection: Equatable {
        let phase: ServicePhase
        let httpStatusCode: Int
        let observedAtMs: Int64
    }

    private enum Keys {
        static let userWantsCapture = "control.userWantsCapture"
        static let captureGeneration = "control.captureGeneration"
        static let extensionRoundCounter = "control.extensionRoundCounter"
        static let broadcastPhase = "control.broadcastPhase"
        static let broadcastStateChangedAtMs = "control.broadcastStateChangedAtMs"
        static let lastVideoSampleAtMs = "control.lastVideoSampleAtMs"
        static let servicePhase = "control.servicePhase"
        static let serviceHTTPStatusCode = "control.serviceHTTPStatusCode"
        static let serviceObservedAtMs = "control.serviceObservedAtMs"
    }

    /// 仅记录 ReplayKit 生命周期状态，不保存媒体或识别内容。
    static func setBroadcastPhase(_ phase: BroadcastPhase, resetVideoSample: Bool = false) {
        guard let defaults = AppGroup.defaults else { return }
        let nowMs = Int64(Date().timeIntervalSince1970 * 1000)
        defaults.set(phase.rawValue, forKey: Keys.broadcastPhase)
        defaults.set(nowMs, forKey: Keys.broadcastStateChangedAtMs)
        if resetVideoSample {
            defaults.set(Int64(0), forKey: Keys.lastVideoSampleAtMs)
        }
    }

    /// 每两秒最多写一次真实视频帧到达时间，避免在高帧率回调中频繁写 App Group。
    static func noteVideoSample(now: Date = Date()) {
        guard let defaults = AppGroup.defaults else { return }
        let nowMs = Int64(now.timeIntervalSince1970 * 1000)
        let lastMs = Int64(defaults.integer(forKey: Keys.lastVideoSampleAtMs))
        guard nowMs - lastMs >= 2_000 else { return }
        defaults.set(BroadcastPhase.receivingVideo.rawValue, forKey: Keys.broadcastPhase)
        defaults.set(nowMs, forKey: Keys.lastVideoSampleAtMs)
        defaults.set(nowMs, forKey: Keys.broadcastStateChangedAtMs)
    }

    static func broadcastActivity() -> BroadcastActivity {
        let defaults = AppGroup.defaults
        let rawPhase = defaults?.string(forKey: Keys.broadcastPhase)
        let phase = rawPhase.flatMap(BroadcastPhase.init(rawValue:)) ?? .unknown
        return BroadcastActivity(
            phase: phase,
            stateChangedAtMs: Int64(defaults?.integer(forKey: Keys.broadcastStateChangedAtMs) ?? 0),
            lastVideoSampleAtMs: Int64(defaults?.integer(forKey: Keys.lastVideoSampleAtMs) ?? 0)
        )
    }

    /// 保存最近服务请求结果的类别与HTTP码，不保存URL、令牌或正文。
    static func recordServiceResponse(error: Error?, response: URLResponse?) {
        // URLSession may return both an HTTP response and a transfer error.
        // A received HTTP status is stronger evidence of server reachability.
        guard let code = (response as? HTTPURLResponse)?.statusCode else {
            setServiceConnection(.unreachable)
            return
        }
        if (500...599).contains(code) {
            setServiceConnection(.respondedWithError, httpStatusCode: code)
        } else {
            setServiceConnection(.connected)
        }
    }

    static func setServiceConnection(_ phase: ServicePhase, httpStatusCode: Int = 0) {
        guard let defaults = AppGroup.defaults else { return }
        let oldPhase = defaults.string(forKey: Keys.servicePhase)
        let oldCode = defaults.integer(forKey: Keys.serviceHTTPStatusCode)
        let nowMs = Int64(Date().timeIntervalSince1970 * 1000)
        let observedAtMs = Int64(defaults.integer(forKey: Keys.serviceObservedAtMs))
        let sameState = oldPhase == phase.rawValue && oldCode == httpStatusCode
        if sameState && nowMs - observedAtMs < 5_000 { return }
        defaults.set(phase.rawValue, forKey: Keys.servicePhase)
        defaults.set(httpStatusCode, forKey: Keys.serviceHTTPStatusCode)
        defaults.set(nowMs, forKey: Keys.serviceObservedAtMs)
    }

    static func serviceConnection() -> ServiceConnection {
        let defaults = AppGroup.defaults
        let phase = defaults?.string(forKey: Keys.servicePhase)
            .flatMap(ServicePhase.init(rawValue:)) ?? .unknown
        return ServiceConnection(
            phase: phase,
            httpStatusCode: defaults?.integer(forKey: Keys.serviceHTTPStatusCode) ?? 0,
            observedAtMs: Int64(defaults?.integer(forKey: Keys.serviceObservedAtMs) ?? 0)
        )
    }

    /// 主 App 中“识别开关”的期望状态。真正的采集授权与生命周期由系统
    /// 广播承载，本标志只表达用户意图；扩展每轮开始时读取。
    static func userWantsCapture() -> Bool {
        AppGroup.defaults?.bool(forKey: Keys.userWantsCapture) ?? false
    }

    static func setUserWantsCapture(_ v: Bool) {
        guard let d = AppGroup.defaults else { return }
        let oldValue = d.bool(forKey: Keys.userWantsCapture)
        guard oldValue != v else { return }
        d.set(d.integer(forKey: Keys.captureGeneration) &+ 1, forKey: Keys.captureGeneration)
        d.set(v, forKey: Keys.userWantsCapture)
    }

    /// 暂停、停止或重启时递增；扩展据此丢弃迟到的 OCR/网络回调。
    static func captureGeneration() -> Int {
        AppGroup.defaults?.integer(forKey: Keys.captureGeneration) ?? 0
    }

    /// 累计处理轮次（仅计数器，供主 App 状态页显示活跃度；不含任何内容数据）。
    static func bumpRoundCounter() {
        let d = AppGroup.defaults
        let next = (d?.integer(forKey: Keys.extensionRoundCounter) ?? 0) + 1
        d?.set(next, forKey: Keys.extensionRoundCounter)
    }

    static func roundCounter() -> Int {
        AppGroup.defaults?.integer(forKey: Keys.extensionRoundCounter) ?? 0
    }
}
