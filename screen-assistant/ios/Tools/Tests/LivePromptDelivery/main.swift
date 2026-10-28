import Foundation

func item(_ id: String, expires: Int64 = 20_000, priority: Int = 0) -> LivePromptDelivery.Item {
    .init(id: id, text: id, expiresAtMS: expires, priority: priority)
}
let fitsTwo: ([LivePromptDelivery.Item]) -> Bool = { $0.count <= 2 }
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
_ = delivery.receive([item("1"), item("2"), item("3"), item("urgent", priority: 100)], nowMS: 0)
assert(delivery.pending.map(\.id) == ["1", "2", "urgent"])
assert(delivery.take(nowMS: 0, fits: { $0.count == 1 }).map(\.id) == ["urgent"])
assert(delivery.take(nowMS: 3_000, fits: fitsTwo).isEmpty)
assert(delivery.take(nowMS: 20_000, fits: fitsTwo).isEmpty)
print("OK: live prompt deduplication, capacity, interval, expiry, priority and reset")
