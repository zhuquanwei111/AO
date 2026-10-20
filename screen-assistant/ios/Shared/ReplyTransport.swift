import Foundation

/// 扩展 → 主 App 的回复传递通道（可替换 transport 抽象）。
///
/// 背景（已报送主控的风险结论）：iOS 15 公开 API 中不存在严格"零落盘"的
/// 扩展→主 App 通道（Darwin 通知无载荷、XPC 不适用、POSIX shm 沙箱不可用、
/// App Group 容器是唯一官方通道且为磁盘介质）。
///
/// 因此 transport 被抽象为接口，默认实现采用"单槽瞬态邮箱"：
///   - 仅保留最新一条回复（覆盖写，无历史堆积）
///   - 消费即删除（读后 unlink，毫秒级落盘窗口）
///   - 带时间戳，过期即失效（停止后旧响应不回填）
/// 若 36（专项调研）找到无文件 IPC（如可用共享内存方案），实现本协议替换
/// `ReplyTransportFactory.make()` 的返回值即可，两端调用方代码不变。
protocol ReplyTransport {
    /// 扩展侧：发布一条回复（单槽覆盖写）。
    func publish(reply: String)
    /// 主 App 侧：读取最新回复并清除邮箱；无回复/过期回复返回 nil。
    func consumeLatest() -> String?
}

/// 单槽瞬态邮箱默认实现（App Group 容器文件，写→读后即删）。
struct TransientFileMailboxTransport: ReplyTransport {

    /// 邮箱有效期：发布后未被消费，超过该时长即视为过期（不回填）。
    static let staleAfterSeconds: TimeInterval = 120

    private var mailboxURL: URL? {
        FileManager.default
            .containerURL(forSecurityApplicationGroupIdentifier: AppGroup.identifier)?
            .appendingPathComponent("reply-mailbox.json")
    }

    func publish(reply: String) {
        guard let url = mailboxURL else { return }
        let payload: [String: Any] = [
            "reply": reply,
            "writtenAt": Date().timeIntervalSince1970
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: payload) else { return }
        try? data.write(to: url, options: .atomic)
    }

    func consumeLatest() -> String? {
        guard let url = mailboxURL else { return nil }
        defer { try? FileManager.default.removeItem(at: url) } // 消费即删
        guard let data = try? Data(contentsOf: url),
              let payload = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let reply = payload["reply"] as? String,
              let writtenAt = payload["writtenAt"] as? Double
        else { return nil }
        // 过期回复不回填（停止后旧响应不得进入展示）
        guard Date().timeIntervalSince1970 - writtenAt <= Self.staleAfterSeconds else {
            return nil
        }
        return reply
    }
}

/// transport 工厂：替换实现时只改这里。
enum ReplyTransportFactory {
    static func make() -> ReplyTransport {
        TransientFileMailboxTransport()
    }
}
