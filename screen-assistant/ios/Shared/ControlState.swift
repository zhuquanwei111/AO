import Foundation

/// 控制与状态标志（App Group UserDefaults 中唯一允许的“状态”数据）。
///
/// 只存布尔/时间戳/计数器等控制状态，不存句子、回复或画面。
struct ControlState {
    private enum Keys {
        static let userWantsCapture = "control.userWantsCapture"
        static let captureGeneration = "control.captureGeneration"
        static let extensionRoundCounter = "control.extensionRoundCounter"
        static let shmSmokeMode = "control.shmSmokeMode"
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

    /// 通道冒烟模式（R/36 指定的第一个真机验证切片）：
    /// 开启后扩展不跑 OCR/网络，仅向 shm 写递增序列号；
    /// 主 App 用 ShmCrossProcessProbeView 观察跨进程通道是否成立。
    static func shmSmokeMode() -> Bool {
        AppGroup.defaults?.bool(forKey: Keys.shmSmokeMode) ?? false
    }

    static func setShmSmokeMode(_ v: Bool) {
        AppGroup.defaults?.set(v, forKey: Keys.shmSmokeMode)
    }
}
