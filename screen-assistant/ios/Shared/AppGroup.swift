import Foundation

/// 本应用与广播扩展共享的 App Group 常量。
///
/// 重要：三个标识符必须与 Apple Developer 后台的注册保持一致——
///   主 App Bundle ID:      com.screenassistant.ios
///   扩展 Bundle ID:        com.screenassistant.ios.broadcast
///   App Group:             group.com.screenassistant.ios
/// 若修改 App Group，需同步修改以下位置：
///   1) 本文件 `identifier`
///   2) ScreenAssistant/ScreenAssistant.entitlements
///   3) ScreenAssistantBroadcast/ScreenAssistantBroadcast.entitlements
///   4) Apple Developer → Certificates, Identifiers & Profiles → App Groups 注册
enum AppGroup {
    static let identifier = "group.com.screenassistant.ios"

    /// 共享 UserDefaults。App Group 未正确配置（entitlement/描述文件缺失）时返回 nil，
    /// 调用方必须将其视为“共享通道不可用”并降级，不得 crash。
    static var defaults: UserDefaults? {
        UserDefaults(suiteName: identifier)
    }
}

/// 跨进程 Darwin 通知（主 App ↔ 广播扩展之间的事件信号）。
/// 仅传递“事件发生”这一信号本身，不携带任何内容；内容一律通过 App Group UserDefaults
/// 的受控键（SharedConfig / ControlState）传递，且不包含句子或回复文本。
enum DarwinSignal {
    static let roundDidFinish = "com.screenassistant.ios.roundDidFinish"
    static let newReplyAvailable = "com.screenassistant.ios.newReply"

    static func post(_ name: String) {
        CFNotificationCenterPostNotification(
            CFNotificationCenterGetDarwinNotifyCenter(),
            CFNotificationName(name as CFString),
            nil, nil, true
        )
    }
}
