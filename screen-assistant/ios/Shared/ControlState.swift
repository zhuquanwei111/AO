import Foundation

/// 控制与状态标志（App Group UserDefaults 中唯一允许的“状态”数据）。
///
/// 只存布尔/时间戳/计数器等控制状态，不存句子、回复或画面。
struct ControlState {
    private enum Keys {
        static let userWantsCapture = "control.userWantsCapture"
        static let extensionRoundCounter = "control.extensionRoundCounter"
        static let pipActive = "control.pipActive"
        static let pipHeartbeatAt = "control.pipHeartbeatAt"
    }

    /// 主 App 心跳超时阈值（秒）：超过即视为 PiP 展示路径不可用，
    /// 扩展侧自动回落通知展示。取值 = 心跳周期 2s × 5 次冗余。
    static let pipLivenessTimeoutSeconds: TimeInterval = 10

    /// 主 App 中“识别开关”的期望状态。真正的采集授权与生命周期由系统
    /// 广播承载，本标志只表达用户意图；扩展每轮开始时读取。
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

    // MARK: - PiP 展示路径状态（主 App 写，扩展读）

    /// 主 App 的 PiP 是否已启动（用户期望回复走 PiP 邮箱而非通知）。
    static func pipActive() -> Bool {
        AppGroup.defaults?.bool(forKey: Keys.pipActive) ?? false
    }

    static func setPipActive(_ v: Bool) {
        let d = AppGroup.defaults
        d?.set(v, forKey: Keys.pipActive)
        if v {
            markPipHeartbeat()
        } else {
            d?.set(0, forKey: Keys.pipHeartbeatAt)
        }
    }

    /// 主 App 存活心跳（PiP 运行期间每 2s 写一次时间戳；仅时间戳，无文本）。
    static func markPipHeartbeat() {
        AppGroup.defaults?.set(Date().timeIntervalSince1970, forKey: Keys.pipHeartbeatAt)
    }

    /// 扩展侧判定 PiP 展示路径是否真实可用：
    /// 标志开启 **且** 心跳新鲜（主 App 未挂起/未被杀）。
    /// 心跳超时说明主 App 已挂起或 PiP 已死 → 扩展自动回落通知展示，
    /// 保证“新回复仍能展示”，不依赖 PiP 保活假设。
    static func pipLivenessIsFresh() -> Bool {
        guard pipActive() else { return false }
        let last = AppGroup.defaults?.double(forKey: Keys.pipHeartbeatAt) ?? 0
        guard last > 0 else { return false }
        return Date().timeIntervalSince1970 - last <= pipLivenessTimeoutSeconds
    }
}
