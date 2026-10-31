import Foundation

assert(LivePromptDelivery.promptLabel("comment_reply") == "评论回复")
assert(LivePromptDelivery.promptLabel("operation_notice") == "运营提醒")
assert(LivePromptDelivery.promptLabel(nil) == "提示")
assert(LivePromptDelivery.promptLabel("future") == "提示")

var decisions: [String] = []
var recorded = LivePromptDelivery(capacity: 1, intervalMS: 0)
let tracked = LivePromptDelivery.Item(id: "tracked", text: "private", expiresAtMS: 10,
                                      priority: 0, promptType: "comment_reply")
let dropped = LivePromptDelivery.Item(id: "dropped", text: "private", expiresAtMS: 20,
                                      priority: 0, promptType: "operation_notice")
_ = recorded.receive([tracked, dropped], nowMS: 0) { item, reason in
    decisions.append("\(item.id):\(item.promptType ?? "unknown"):\(reason)")
}
assert(decisions == ["tracked:comment_reply:received", "tracked:comment_reply:queued",
                     "dropped:operation_notice:received", "dropped:operation_notice:queue_full"])
_ = recorded.take(nowMS: 10, onDecision: { item, reason in decisions.append("\(item.id):\(reason)") }) { _ in true }
assert(decisions.last == "tracked:expired_in_queue")
var urgentDecisions: [String] = []
var urgentRecorder = LivePromptDelivery(capacity: 1, intervalMS: 0)
let urgentItem = LivePromptDelivery.Item(id: "urgent", text: "private", expiresAtMS: 20,
                                         priority: 100, promptType: "operation_notice")
let invalid = LivePromptDelivery.Item(id: "invalid", text: "private", expiresAtMS: 0,
                                      priority: 0, timingInvalid: true, promptType: "comment_reply")
_ = urgentRecorder.receive([tracked, urgentItem, invalid], nowMS: 0) { item, reason in
    urgentDecisions.append("\(item.id):\(reason)")
}
assert(urgentDecisions == ["tracked:received", "tracked:queued", "urgent:received",
                           "tracked:displaced_by_urgent", "urgent:queued",
                           "invalid:received", "invalid:invalid_timing"])
assert(urgentRecorder.take(nowMS: 1) { _ in true }.map(\.id) == ["urgent"])
var malformedPriority = LivePromptDelivery(intervalMS: 0)
let unsafePriority = LivePromptDelivery.Item(id: "bad", text: "bad", expiresAtMS: 100,
                                             priority: 0, priorityInvalid: true)
let validPriority = LivePromptDelivery.Item(id: "valid", text: "valid", expiresAtMS: 100, priority: 100)
assert(malformedPriority.receive([unsafePriority, validPriority], nowMS: 1).map(\.id) == ["bad", "valid"])
assert(malformedPriority.take(nowMS: 1) { _ in true }.map(\.id) == ["valid"])

func item(_ id: String, expires: Int64 = 20_000, priority: Int = 0) -> LivePromptDelivery.Item {
    .init(id: id, text: id, expiresAtMS: expires, priority: priority)
}
let fitsTwo: ([LivePromptDelivery.Item]) -> Bool = { $0.count <= 2 }
let displayed = [item("early", expires: 3_000), item("later", expires: 9_000)]
assert(LivePromptDelivery.unexpired(displayed, nowMS: 3_000).map(\.id) == ["later"])
assert(LivePromptDelivery.unexpired(displayed, nowMS: 9_000).isEmpty)
var readable = LivePromptDelivery()
_ = readable.receive([item("A", expires: 10_000)], nowMS: 0)
assert(readable.take(nowMS: 0, fits: fitsTwo).map(\.id) == ["A"])
_ = readable.receive([item("B", expires: 10_000)], nowMS: 887)
assert(readable.take(nowMS: 887, fits: fitsTwo).isEmpty)
assert(readable.take(nowMS: 7_999, fits: fitsTwo).isEmpty)
assert(readable.take(nowMS: 8_000, fits: fitsTwo).map(\.id) == ["B"])
readable.clear()
_ = readable.receive([item("A", expires: 2_000)], nowMS: 0)
let shortFrame = readable.take(nowMS: 0, fits: fitsTwo)
_ = readable.receive([item("B", expires: 4_000)], nowMS: 887)
assert(LivePromptDelivery.unexpired(shortFrame, nowMS: 2_000).isEmpty)
assert(readable.take(nowMS: 8_000, fits: fitsTwo).isEmpty)
assert(readable.expiredCount == 1)
readable.clear()
_ = readable.receive([item("A")], nowMS: 0)
_ = readable.take(nowMS: 0, fits: fitsTwo)
_ = readable.receive([item("ordinary"), item("urgent", priority: 100)], nowMS: 887)
assert(readable.take(nowMS: 887, fits: fitsTwo).map(\.id) == ["urgent"])
assert(readable.take(nowMS: 8_886, fits: fitsTwo).isEmpty)
assert(readable.take(nowMS: 8_887, fits: fitsTwo).map(\.id) == ["ordinary"])
readable.clear()
assert(readable.receive([item("A")], nowMS: 0).map(\.id) == ["A"])
assert(readable.take(nowMS: 0, fits: fitsTwo).map(\.id) == ["A"])
var delivery = LivePromptDelivery(capacity: 3, intervalMS: 3_000)
assert(delivery.receive([item("a"), item("b"), item("a")], nowMS: 0).map(\.id) == ["a", "b"])
assert(delivery.take(nowMS: 0, fits: fitsTwo).map(\.id) == ["a", "b"])
assert(delivery.receive([item("c"), item("d"), item("e"), item("f")], nowMS: 1).count == 4)
assert(delivery.overflowCount == 1 && delivery.pending.map(\.id) == ["c", "d", "e"])
assert(delivery.take(nowMS: 2_999, fits: fitsTwo).isEmpty)
assert(delivery.take(nowMS: 3_000, fits: fitsTwo).map(\.id) == ["c", "d"])
assert(delivery.take(nowMS: 20_000, fits: fitsTwo).isEmpty && delivery.expiredCount == 1)
delivery.clear()
_ = delivery.receive([item("a"), item("urgent", priority: 100)], nowMS: 1)
assert(delivery.take(nowMS: 1, fits: { $0.count == 1 }).map(\.id) == ["urgent"])
delivery.clear()
assert(delivery.pending.isEmpty)
assert(delivery.receive([item("a")], nowMS: 1).count == 1)
delivery.clear()
assert(LivePromptDelivery.displayExpiry(nil, serverTimeMS: 1_000, processingMS: 0, startNS: 0, endNS: 0) == nil)
assert(LivePromptDelivery.displayExpiry(16_000, serverTimeMS: nil, processingMS: 0, startNS: 0, endNS: 0) == nil)
assert(LivePromptDelivery.displayExpiry(16_000, serverTimeMS: 1_000, processingMS: 15_001, startNS: 0, endNS: 15_000_000_000) == nil)
assert(LivePromptDelivery.displayExpiry(16_000, serverTimeMS: 1_000, processingMS: -1, startNS: 0, endNS: 1) == nil)
assert(LivePromptDelivery.displayExpiry(16_000, serverTimeMS: 1_000, processingMS: 0, startNS: 2, endNS: 1) == nil)
let expiry = LivePromptDelivery.displayExpiry(16_000, serverTimeMS: 1_000, processingMS: 15_000, startNS: 0, endNS: 15_010_000_000)!
assert(expiry == 29_999)
assert(LivePromptDelivery.displayExpiry(16_000, serverTimeMS: 1_000, processingMS: 15_000, startNS: 0, endNS: 15_200_000_000) == 29_999)
assert(LivePromptDelivery.displayExpiry(16_000, serverTimeMS: 1_000, processingMS: 15_000, startNS: 0, endNS: 31_000_000_000) == 31_000)
for wallClock in [-86_400_000, 0, 86_400_000] {
    _ = wallClock // 截止点计算不读取设备墙钟。
    assert(LivePromptDelivery.displayExpiry(16_000, serverTimeMS: 1_000, processingMS: 15_000, startNS: 0, endNS: 15_010_000_000) == expiry)
}
assert(LivePromptDelivery.displayExpiry(16_000, serverTimeMS: 1_000, processingMS: 0, startNS: 0, endNS: 1) == 14_998)
assert(LivePromptDelivery.displayExpiry(16_000, serverTimeMS: 1_000, processingMS: 15_000, startNS: 0, endNS: 16_000_000_000) == 29_999)
assert(LivePromptDelivery.displayExpiry(1_002, serverTimeMS: 1_000, processingMS: 0, startNS: 0, endNS: 1_000_000) == 1)
_ = delivery.receive([item("timed", expires: expiry)], nowMS: 15_010)
assert(delivery.take(nowMS: 29_998, fits: fitsTwo).map(\.id) == ["timed"])
delivery.clear()
_ = delivery.receive([item("slept", expires: expiry)], nowMS: 15_010)
assert(delivery.take(nowMS: 30_000, fits: fitsTwo).isEmpty)
delivery.clear()
_ = delivery.receive([item("1"), item("2"), item("3"), item("urgent", priority: 100)], nowMS: 0)
assert(delivery.pending.map(\.id) == ["1", "2", "urgent"])
assert(delivery.take(nowMS: 0, fits: { $0.count == 1 }).map(\.id) == ["urgent"])
assert(delivery.take(nowMS: 2_999, fits: fitsTwo).isEmpty)
assert(delivery.take(nowMS: 3_000, fits: fitsTwo).map(\.id) == ["1", "2"])
assert(delivery.take(nowMS: 20_000, fits: fitsTwo).isEmpty)
delivery.clear()
_ = delivery.receive([item("long-urgent", expires: 100_000, priority: 100)], nowMS: 100)
assert(delivery.take(nowMS: 100, fits: fitsTwo).map(\.id) == ["long-urgent"])
_ = delivery.receive([item("ordinary", expires: 5_000)], nowMS: 101)
assert(delivery.take(nowMS: 3_099, fits: fitsTwo).isEmpty)
assert(delivery.take(nowMS: 3_100, fits: fitsTwo).map(\.id) == ["ordinary"])
print("OK: live prompt deduplication, capacity, interval, expiry, priority and reset")
