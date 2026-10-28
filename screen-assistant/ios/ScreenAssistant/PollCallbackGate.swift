import Foundation

/// 调用方须在持有当前场次和代次的串行上下文中调用。
enum PollCallbackGate {
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
