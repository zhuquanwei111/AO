import Foundation

func inspect(_ text: String) -> PromptTimingJSON.Inspection {
    PromptTimingJSON.inspect(Data(text.utf8))!
}

for bad in ["null", "true", "1.0", "\"bad\"", "-1"] {
    let page = inspect("{\"server_time_ms\":1000,\"server_processing_ms\":0,\"prompts\":[{\"expires_at_ms\":5000},{\"expires_at_ms\":\(bad)},{\"expires_at_ms\":6000}]}")
    let deadlines = page.expiries.map {
        LivePromptDelivery.displayExpiry($0, serverTimeMS: page.serverTimeMS, processingMS: page.processingMS,
                                         startNS: 0, endNS: 1_000_000)
    }
    assert(deadlines[0] == 3999 && deadlines[1] == nil && deadlines[2] == 4999)
    var delivery = LivePromptDelivery(intervalMS: 0)
    let items = deadlines.enumerated().map { index, deadline in
        LivePromptDelivery.Item(id: "\(index)", text: "\(index)", expiresAtMS: deadline ?? 0,
                                priority: 0, timingInvalid: deadline == nil)
    }
    assert(delivery.receive(items, nowMS: 1).count == 3)
    assert(delivery.take(nowMS: 1, fits: { _ in true }).map(\.id) == ["0", "2"])
    assert(delivery.expiredCount == 0)
}
let missing = inspect("{\"server_time_ms\":1000,\"server_processing_ms\":0,\"prompts\":[{\"expires_at_ms\":5000},{},{\"expires_at_ms\":6000}]}")
assert(missing.expiries == [5000, nil, 6000])
for badAnchor in ["null", "true", "1.0", "\"bad\"", "-1"] {
    let page = inspect("{\"server_time_ms\":\(badAnchor),\"server_processing_ms\":0,\"prompts\":[{\"expires_at_ms\":5000},{\"expires_at_ms\":6000}]}")
    assert(page.expiries.allSatisfy {
        LivePromptDelivery.displayExpiry($0, serverTimeMS: page.serverTimeMS, processingMS: page.processingMS,
                                         startNS: 0, endNS: 1_000_000) == nil
    })
}
let mixed = inspect("{\"server_time_ms\":1000,\"server_processing_ms\":0,\"prompts\":[{\"expires_at_ms\":1000},{\"expires_at_ms\":6000}]}")
let expired = LivePromptDelivery.displayExpiry(mixed.expiries[0], serverTimeMS: mixed.serverTimeMS,
                                                processingMS: mixed.processingMS, startNS: 0, endNS: 1_000_000)!
assert(expired == 1)
let strictPriority = inspect("{\"server_time_ms\":1000,\"server_processing_ms\":0,\"prompts\":[{\"priority\":100},{\"priority\":100.0},{\"priority\":true},{\"priority\":null},{}]}")
assert(strictPriority.priorities == [100, nil, nil, nil, nil])
for invalid in ["null", "true", "100.0", "1e2", "\"100\"", "9223372036854775808"] {
    let input = inspect("{\"server_time_ms\":1000,\"server_processing_ms\":0,\"prompts\":[{\"priority\":\(invalid)}]}")
    assert(input.priorities == [nil])
}
var guardedPriority = LivePromptDelivery(intervalMS: 0)
let urgent = LivePromptDelivery.Item(id: "urgent", text: "urgent", expiresAtMS: 20,
                                     priority: 100)
let malformed = LivePromptDelivery.Item(id: "bad", text: "bad", expiresAtMS: 20,
                                        priority: nil, priorityInvalid: true)
let expiredOrdinary = LivePromptDelivery.Item(id: "expired", text: "expired", expiresAtMS: 1,
                                               priority: 25)
assert(guardedPriority.receive([urgent, malformed, expiredOrdinary], nowMS: 1).count == 3)
assert(guardedPriority.expiredCount == 1)
let urgentFrame = guardedPriority.take(nowMS: 1) { _ in true }
assert(urgentFrame.map(\.id) == ["urgent"] && urgentFrame[0].expiresAtMS == 20)
print("OK: mixed prompt deadlines and shared timing anchors")
