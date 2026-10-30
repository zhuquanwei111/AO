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
print("OK: mixed prompt deadlines and shared timing anchors")
