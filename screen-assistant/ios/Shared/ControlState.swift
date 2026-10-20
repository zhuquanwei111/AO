import Foundation

/// 控制与状态标志（App Group UserDefaults 中唯一允许的“状态”数据）。
///
/// 只存布尔/计数等控制状态，不存句子、回复或画面。
struct ControlState {
    private enum Keys {
        static let userWantsCapture = "control.userWantsCapture"
        static let extensionRoundCounter = "control.extensionRoundCounter"
    }

    /// 主 App 中“启动识别”开关的期望状态。注意：真正的采集授权与生命周期由系统
    /// 广播（用户从控制中心/RPSystemBroadcastPickerView 发起与停止）承载，本标志只表达
    /// 用户意图，扩展在每轮开始时读取；若用户在主 App 关闭了开关但广播仍在进行，
    /// 扩展将跳过处理直到广播被用户停止。
    static func userWantsCapture() -> Bool {
        AppGroup.defaults?.bool(forKey: Keys.userWantsCapture) ?? false
    }

    static func setUserWantsCapture(_ v: Bool) {
        AppGroup.defaults?.set(v, forKey: Keys.userWantsCapture)
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
