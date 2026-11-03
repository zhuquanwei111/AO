import Foundation

func event(_ text: String, comment: Bool = true, observedAtMS: Int64 = 1_000) -> ObservationUploadBuffer.Event {
    .init(id: UUID(), body: Data(text.utf8), isComment: comment, observedAtMS: observedAtMS)
}
func batch(_ events: [ObservationUploadBuffer.Event], session: String = "old") -> ObservationUploadBuffer.Batch {
    .init(id: UUID(), events: events, observedAtMS: 1_000, generation: 7,
          sessionID: session, token: "fixture", baseURL: URL(string: "https://example.invalid")!)
}

let stableID = UUID()
let comment = ObservationUploadBuffer.comment(text: "昵称 你好😀\n下一行", position: nil,
    confidence: nil, observedAtMS: 123_456, id: stableID)!
let commentDTO = try! JSONSerialization.jsonObject(with: comment.body) as! [String: Any]
assert(Set(commentDTO.keys) == Set(["event_id", "input_mode", "source", "coverage", "reliability", "text", "observed_at_ms"]))
assert(commentDTO["event_id"] as? String == stableID.uuidString.lowercased())
assert(commentDTO["observed_at_ms"] as? Int == 123_456 && commentDTO["reliability"] as? String == "known")
assert(commentDTO["text"] as? String == "昵称 你好😀\n下一行")
let metadataBatchID = UUID()
let metadataIndices: [Int] = [0, 7]
let metadataRows = metadataIndices.map { index in
    ObservationUploadBuffer.comment(text: "片段\(index)", position: nil, confidence: nil,
        observedAtMS: 123_456, lineIndex: index)!
}
let encodedMetadata = ObservationUploadBuffer.metadataComments(metadataRows, batchID: metadataBatchID,
    occupiedBytes: 0)
assert(encodedMetadata.events.count == 2 && encodedMetadata.dropped == 0)
for (index, encoded) in encodedMetadata.events.enumerated() {
    let dto = try! JSONSerialization.jsonObject(with: encoded.body) as! [String: Any]
    assert(dto["capture_comment_count"] as? Int == 2)
    assert(dto["capture_batch_id"] as? String == metadataBatchID.uuidString.lowercased())
    assert(dto["line_index"] as? Int == [0, 7][index])
    assert(dto["event_id"] as? String == metadataRows[index].id.uuidString.lowercased())
    assert(dto["observed_at_ms"] as? Int == 123_456 && dto["kind"] as? String == "comment")
    assert(dto["input_mode"] == nil && encoded.useObservationEndpoint)
}
let limited = ObservationUploadBuffer.metadataComments(metadataRows, batchID: metadataBatchID,
    occupiedBytes: ObservationUploadBuffer.BatchBuilder.maxBytes - encodedMetadata.events[0].body.count)
assert(limited.events.count == 1 && limited.dropped == 1)
let limitedDTO = try! JSONSerialization.jsonObject(with: limited.events[0].body) as! [String: Any]
assert(limitedDTO["capture_comment_count"] as? Int == 1)
let many = (0..<129).map { (index: Int) in
    ObservationUploadBuffer.comment(text: "评论\(index)😀", position: nil, confidence: nil,
        observedAtMS: 123_456, lineIndex: index)!
}
let prefix = ObservationUploadBuffer.metadataComments(many, batchID: metadataBatchID, occupiedBytes: 0)
assert(prefix.events.count == 128 && prefix.dropped == 1)
assert(prefix.events.reduce(0) { $0 + $1.body.count } <= ObservationUploadBuffer.BatchBuilder.maxBytes)
for (index, encoded) in prefix.events.enumerated() {
    let dto = try! JSONSerialization.jsonObject(with: encoded.body) as! [String: Any]
    assert(dto["capture_comment_count"] as? Int == 128 && dto["line_index"] as? Int == index)
    assert(dto["event_id"] as? String == many[index].id.uuidString.lowercased())
}
assert(ObservationUploadBuffer.metadataComments([], batchID: metadataBatchID, occupiedBytes: 0).events.isEmpty)
if let _ = ObservationUploadBuffer.comment(text: String(repeating: "😀", count: 501), position: nil,
    confidence: nil, observedAtMS: 0) { assertionFailure("超长正文必须整项拒绝") }

// 真实一轮人数+20行入一个批次，按原顺序逐条完成。
var builder = ObservationUploadBuffer.BatchBuilder()
let rows = [event("人数", comment: false)] + (0..<20).map {
    ObservationUploadBuffer.comment(text: "评论\($0)", position: nil, confidence: nil, observedAtMS: 1_000)!
}
for row in rows { assert(builder.append(row) == .accepted) }
assert(builder.events.count == 21)
var buffer = ObservationUploadBuffer()
let first = batch(builder.events)
assert(buffer.append(first) == .accepted)
for row in rows {
    let current = buffer.begin()!
    assert(current.id == first.id && current.current?.id == row.id)
    if row.isComment {
        let decoded = try! JSONSerialization.jsonObject(with: current.current!.body) as! [String: Any]
        assert(decoded["observed_at_ms"] as? Int == 1_000 && decoded["event_id"] as? String == row.id.uuidString.lowercased())
    }
    assert(buffer.complete(batchID: first.id, eventID: row.id))
}
assert(buffer.begin() == nil)

// 128条边界、129条前缀；每条原JSON不截断。
var lineLimit = ObservationUploadBuffer.BatchBuilder()
assert(lineLimit.append(event("人数", comment: false)) == .accepted)
for _ in 0..<128 { assert(lineLimit.append(event("行")) == .accepted) }
assert(lineLimit.append(event("第129行")) == .commentLimit)
assert(lineLimit.events.count == 129 && lineLimit.events.filter(\.isComment).count == 128)
var bytes = ObservationUploadBuffer.BatchBuilder()
let mixed = String(repeating: "汉😀\n\"\\", count: 200)
let base = try! JSONSerialization.data(withJSONObject: ["text": mixed, "pad": ""])
let utf8Item = try! JSONSerialization.data(withJSONObject: ["text": mixed, "pad": String(repeating: "a", count: 65_536 - base.count)])
assert(utf8Item.count == 65_536)
assert((try! JSONSerialization.jsonObject(with: utf8Item) as! [String: String])["text"] == mixed)
let maxItem = ObservationUploadBuffer.Event(id: UUID(), body: utf8Item, isComment: true, observedAtMS: 1_000)
for _ in 0..<8 { assert(bytes.append(maxItem) == .accepted) }
assert(bytes.bytes == 524_288)
assert(bytes.append(event("汉😀")) == .byteLimit)
assert(bytes.events.count == 8)
var memoryBound = ObservationUploadBuffer()
let fullBatches = (0..<4).map { _ in batch(bytes.events) }
assert(memoryBound.append(fullBatches[0]) == .accepted && memoryBound.begin()?.id == fullBatches[0].id)
for full in fullBatches.dropFirst() { assert(memoryBound.append(full) == .accepted) }
let totalBodies = (memoryBound.active!.events + memoryBound.pending.flatMap(\.events)).reduce(0) { $0 + $1.body.count }
assert(totalBodies == 2_097_152)
assert(memoryBound.append(batch([event("拒入")])) == .overflow)
var invalidBuffer = ObservationUploadBuffer()
assert(invalidBuffer.append(batch((0..<129).map { _ in event("超出") })) == .invalid)
assert(invalidBuffer.begin() == nil)
var oversize = ObservationUploadBuffer.BatchBuilder()
assert(oversize.append(.init(id: UUID(), body: Data(repeating: 1, count: 65_537), isComment: true, observedAtMS: 1_000)) == .itemOversize)
var countLimit = ObservationUploadBuffer.BatchBuilder()
assert(countLimit.append(event("人数1", comment: false)) == .accepted)
assert(countLimit.append(event("人数2", comment: false)) == .countLimit)

// 1批在途、3批等待，满载保留旧批；旧回调不能完成新批。
let one = batch([event("1")])
assert(buffer.append(one) == .accepted && buffer.begin()?.id == one.id)
let waits = (2...4).map { batch([event("\($0)")]) }
for value in waits { assert(buffer.append(value) == .accepted) }
assert(buffer.append(batch([event("5")])) == .overflow)
assert(buffer.pending.map(\.id) == waits.map(\.id))
assert(buffer.complete(batchID: one.id, eventID: one.events[0].id))
let old = buffer.begin()!
assert(old.id == waits[0].id)
buffer.clear()
let replacement = batch([event("new")], session: "new")
assert(buffer.append(replacement) == .accepted && buffer.begin()?.id == replacement.id)
assert(!buffer.complete(batchID: old.id, eventID: old.events[0].id))
assert(buffer.active?.id == replacement.id)
let waiting = batch([event("a"), event("b")])
assert(buffer.append(waiting) == .accepted)
let stopped = buffer.discard(inFlight: true)
assert(stopped.unknown == 1 && stopped.unsent == 2 && buffer.begin() == nil)
assert(!buffer.complete(batchID: replacement.id, eventID: replacement.events[0].id))

// 每项依据原采样时间比较；临界点不拒发，已在途的不因响应晚到改判未发送。
let age = event("age", observedAtMS: 1_000)
assert(!ObservationUploadBuffer.isExpired(age, nowMS: 10_999))
assert(!ObservationUploadBuffer.isExpired(age, nowMS: 11_000))
assert(ObservationUploadBuffer.isExpired(age, nowMS: 11_001))
assert(!ObservationUploadBuffer.isExpired(age, nowMS: 999)) // 墙钟回拨不伪造新时间
let older = event("older", observedAtMS: 1_000)
let newer = event("newer", observedAtMS: 9_000)
let mixedBatch = batch([older, newer])
assert(buffer.append(mixedBatch) == .accepted)
assert(buffer.begin()?.current?.id == older.id)
assert(ObservationUploadBuffer.isExpired(older, nowMS: 11_001))
assert(buffer.complete(batchID: mixedBatch.id, eventID: older.id))
assert(buffer.begin()?.current?.id == newer.id)
assert(!ObservationUploadBuffer.isExpired(newer, nowMS: 11_001))
let originalBody = newer.body
assert(buffer.complete(batchID: mixedBatch.id, eventID: newer.id)) // 已在途，回执晚到仍按回执处理
assert(newer.body == originalBody && newer.observedAtMS == 9_000)

let scheduled = Data("{\"status\":\"accepted\",\"reason\":\"scheduled\",\"prompt_id\":null}".utf8)
let busy = Data("{\"status\":\"accepted\",\"reason\":\"analysis_busy\",\"prompt_id\":null}".utf8)
let duplicate = Data("{\"status\":\"duplicate\",\"reason\":\"duplicate_event\"}".utf8)
let merged = Data("{\"status\":\"accepted\",\"reason\":\"duplicate_message\"}".utf8)
assert(ObservationUploadBuffer.classify(data: scheduled, statusCode: 200, hasError: false) == .received)
assert(ObservationUploadBuffer.classify(data: busy, statusCode: 200, hasError: false) == .analysisBusy)
assert(ObservationUploadBuffer.classify(data: duplicate, statusCode: 200, hasError: false) == .duplicate)
assert(ObservationUploadBuffer.classify(data: merged, statusCode: 200, hasError: false) == .merged)
assert(ObservationUploadBuffer.classify(data: nil, statusCode: 422, hasError: false) == .rejected)
assert(ObservationUploadBuffer.classify(data: scheduled, statusCode: 500, hasError: false) == .unknown)
assert(ObservationUploadBuffer.classify(data: scheduled, statusCode: 200, hasError: true) == .unknown)
assert(ObservationUploadBuffer.classify(data: nil, statusCode: 200, hasError: false) == .unknown)
let lateReply = batch([age])
assert(buffer.append(lateReply) == .accepted && buffer.begin()?.current?.id == age.id)
assert(ObservationUploadBuffer.isExpired(age, nowMS: 11_001))
assert(ObservationUploadBuffer.classify(data: scheduled, statusCode: 200, hasError: false) == .received)
assert(buffer.complete(batchID: lateReply.id, eventID: age.id))
let disconnected = batch([event("in flight"), event("not sent")])
assert(buffer.append(disconnected) == .accepted && buffer.begin()?.id == disconnected.id)
assert(ObservationUploadBuffer.classify(data: nil, statusCode: nil, hasError: true) == .unknown)
assert(buffer.complete(batchID: disconnected.id, eventID: disconnected.events[0].id))
let afterDisconnect = buffer.discard(inFlight: false)
assert(afterDisconnect.unknown == 0 && afterDisconnect.unsent == 1)

let sample = String(repeating: "汉😀\n\"\\", count: 40)
let bbox: [String: Double] = ["x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4]
let line: [String: Any] = ["text": sample, "bbox": bbox, "confidence": 0.99]
let body: [String: Any] = ["event_id": UUID().uuidString.lowercased(), "kind": "online_count",
    "source": "ios_replaykit_online_count_roi", "observed_at_ms": 1_700_000_000_000,
    "availability": "available", "coverage": "user_selected_count_roi", "reliability": "known",
    "confidence": 0.99, "content": ["raw_text": sample, "field_semantics_confirmed": true,
        "text_position": bbox, "ocr_lines": Array(repeating: line, count: 20)]]
let encoded = try! JSONSerialization.data(withJSONObject: body)
assert(encoded.count <= 65_536)
let metadata = try! JSONSerialization.jsonObject(with: encoded) as! [String: Any]
assert((metadata["event_id"] as? String) == (body["event_id"] as? String))
assert(metadata["observed_at_ms"] as? Int64 == 1_700_000_000_000)
print("OK: bounded batch FIFO, limits, session isolation and response classification")
