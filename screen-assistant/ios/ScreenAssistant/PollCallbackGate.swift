import Foundation

/// 调用方须在持有当前场次和代次的串行上下文中调用。
enum PollCallbackGate {
    static func matchesCredentials(sessionID: String, token: String,
                                   currentSessionID: String?, currentToken: String?,
                                   savedSessionID: String?, savedToken: String?) -> Bool {
        sessionID == currentSessionID && token == currentToken &&
            sessionID == savedSessionID && token == savedToken
    }

    @discardableResult
    static func commitIfCurrent(sessionID: String, generation: Int,
                                currentSessionID: String?, currentGeneration: Int,
                                active: Bool, stopping: Bool,
                                commit: () -> Void) -> Bool {
        guard active, !stopping, sessionID == currentSessionID,
              generation == currentGeneration else { return false }
        commit()
        return true
    }
}

/// 标记当前概览请求，旧回调不能结束后来发起的请求。
struct OverviewRequestTracker {
    private var current: UUID?

    mutating func begin() -> UUID {
        let id = UUID()
        current = id
        return id
    }

    mutating func finish(_ id: UUID) -> Bool {
        guard current == id else { return false }
        current = nil
        return true
    }

    mutating func clear() { current = nil }
}
