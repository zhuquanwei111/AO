import Foundation

/// App Group UDS 短回复通道的适配器。
/// 每个进程持有一个单例；正文只存在于进程内存和 socket 缓冲中。
protocol ReplyTransport: AnyObject {
    func startReceiving() throws
    func stopReceiving()
    func send(reply: String)
    func takeLatestReply() -> String?
}

final class SAReplyTransport: ReplyTransport {
    static let shared = SAReplyTransport()

    private let channel = SAReplyChannel(appGroupIdentifier: AppGroup.identifier)

    private init() {}

    func startReceiving() throws {
        try channel.startReceiving()
    }

    func stopReceiving() {
        channel.stop()
    }

    func send(reply: String) {
        // Attach a numeric session token so a bounded UDS retry from an older
        // capture run cannot refill PiP after pause, stop, or restart.
        channel.send(reply: "\(ControlState.captureGeneration())\n\(reply)")
    }

    func takeLatestReply() -> String? {
        guard ControlState.userWantsCapture(),
              let envelope = channel.takeLatestReply(),
              let separator = envelope.firstIndex(of: "\n"),
              let generation = Int(envelope[..<separator]),
              generation == ControlState.captureGeneration() else { return nil }
        let reply = String(envelope[envelope.index(after: separator)...])
        return reply.isEmpty ? nil : reply
    }
}

enum ReplyTransportFactory {
    /// 主 App 与扩展分别在各自进程内持有一个通道实例。
    static func make() -> ReplyTransport {
        SAReplyTransport.shared
    }
}
