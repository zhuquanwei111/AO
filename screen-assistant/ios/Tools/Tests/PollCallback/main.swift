import Foundation

var recorded: [String] = []
func deliver(_ session: String, _ generation: Int, _ result: String,
             currentSession: String?, currentGeneration: Int,
             active: Bool = true, stopping: Bool = false) -> Bool {
    PollCallbackGate.commitIfCurrent(sessionID: session, generation: generation,
        currentSessionID: currentSession, currentGeneration: currentGeneration,
        active: active, stopping: stopping) { recorded.append(result) }
}

for outcome in ["success", "failure", "cancel", "timeout"] {
    assert(!deliver("A", 1, outcome, currentSession: "B", currentGeneration: 2))
    assert(!deliver("A", 1, outcome, currentSession: "A", currentGeneration: 2))
    assert(!deliver("A", 1, outcome, currentSession: "A", currentGeneration: 1, active: false))
    assert(!deliver("A", 1, outcome, currentSession: "A", currentGeneration: 1, stopping: true))
}
assert(recorded.isEmpty)
assert(deliver("B", 2, "success", currentSession: "B", currentGeneration: 2))
assert(deliver("B", 2, "failure", currentSession: "B", currentGeneration: 2))
assert(recorded == ["success", "failure"])
var overview = OverviewRequestTracker()
let old = overview.begin()
let newer = overview.begin()
assert(!overview.finish(old))
assert(overview.finish(newer))
assert(!overview.finish(newer))
let stopped = overview.begin()
overview.clear()
assert(!overview.finish(stopped))
let failedStart = overview.begin()
assert(overview.finish(failedStart))
print("OK: poll diagnostics are committed only for the current active request")
