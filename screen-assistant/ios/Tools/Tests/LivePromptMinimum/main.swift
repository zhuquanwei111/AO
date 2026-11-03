import Foundation

func item(_ id: String, expiry: Int64, priority: Int = 25, type: String? = nil) -> LivePromptDelivery.Item {
    .init(id: id, text: id, expiresAtMS: expiry, priority: priority, promptType: type)
}
let fitsOne: ([LivePromptDelivery.Item]) -> Bool = { $0.count <= 1 }
var decisions: [String] = []
var queue = LivePromptDelivery(intervalMS: 0)
let short = [item("211", expiry: 211, type: "comment_reply"),
             item("2999", expiry: 2_999, type: "operation_notice"),
             item("3000", expiry: 3_000)]
_ = queue.receive(short, nowMS: 0) { decisions.append("\($0.id):\($1)") }
assert(queue.pending.map(\.id) == ["3000"])
assert(decisions.contains("211:insufficient_reading_time"))
assert(decisions.contains("2999:insufficient_reading_time"))
assert(queue.take(nowMS: 0, fits: fitsOne).map(\.id) == ["3000"])

for type in ["comment_reply", "operation_notice", nil] as [String?] {
    queue.clear()
    decisions.removeAll()
    _ = queue.receive([item("short", expiry: 2_999, type: type),
                       item("boundary", expiry: 3_000, type: type)], nowMS: 0) {
        decisions.append("\($0.id):\($1)")
    }
    assert(queue.pending.map(\.id) == ["boundary"])
    assert(decisions.contains("short:insufficient_reading_time"))
    assert(queue.take(nowMS: 0, fits: fitsOne).map(\.id) == ["boundary"])
}

queue.clear()
decisions.removeAll()
_ = queue.receive([item("late", expiry: 4_000), item("next", expiry: 8_000)], nowMS: 0)
assert(queue.take(nowMS: 1_001, onDecision: { decisions.append("\($0.id):\($1)") }, fits: fitsOne).map(\.id) == ["next"])
assert(decisions.contains("late:insufficient_reading_time"))

var protected = LivePromptDelivery()
_ = protected.receive([item("shown", expiry: 20_000)], nowMS: 0)
let shown = protected.take(nowMS: 0, fits: fitsOne)
decisions.removeAll()
_ = protected.receive([item("flash", expiry: 1_098), item("next", expiry: 20_000)], nowMS: 887) {
    decisions.append("\($0.id):\($1)")
}
assert(decisions.contains("flash:insufficient_reading_time"))
assert(protected.take(nowMS: 887, fits: fitsOne).isEmpty)
assert(LivePromptDelivery.unexpired(shown, nowMS: 887).map(\.id) == ["shown"])
assert(protected.take(nowMS: 8_000, fits: fitsOne).map(\.id) == ["next"])

queue.clear()
_ = queue.receive([item("urgent", expiry: 211, priority: 100)], nowMS: 0)
let urgent = queue.take(nowMS: 0, fits: fitsOne)
assert(urgent.map(\.id) == ["urgent"] && urgent[0].expiresAtMS == 211)
assert(LivePromptDelivery.unexpired(urgent, nowMS: 211).isEmpty)
print("OK: minimum ordinary reading time")
