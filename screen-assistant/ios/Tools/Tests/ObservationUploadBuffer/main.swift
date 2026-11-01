import Foundation

final class FakeTransport {
    var completions: [(Data?, Int?, Bool) -> Void] = []
    func send(_ completion: @escaping (Data?, Int?, Bool) -> Void) { completions.append(completion) }
}
var buffer = ObservationUploadBuffer()
let make: (Int, Int) -> ObservationUploadBuffer.Item = {
    .init(id: UUID(), body: Data(repeating: UInt8($0), count: $1), generation: 7,
          sessionID: "old", token: "fixture", baseURL: URL(string: "https://example.invalid")!, isComment: true)
}
assert(buffer.append(make(1, 1)) == .accepted)
let blocked = buffer.begin()!
assert(buffer.begin() == nil) // 假传输不回调，后续轮次仍可入队
assert(buffer.append(make(2, 65_536)) == .accepted)
assert(buffer.append(make(3, 1)) == .accepted)
assert(buffer.append(make(4, 1)) == .accepted)
assert(buffer.append(make(6, 1)) == .overflow && buffer.overflowCount == 1)
assert(buffer.append(make(5, 65_537)) == .oversize && buffer.oversizeCount == 1)
assert(buffer.complete(blocked.id))
let next = buffer.begin()!
assert(next.body.count == 65_536 && next.generation == 7 && next.sessionID == "old")
buffer.clear()
let replacement = make(7, 1)
assert(buffer.append(replacement) == .accepted && buffer.begin()?.id == replacement.id)
assert(!buffer.complete(next.id) && buffer.active?.id == replacement.id)

let scheduled = Data("{\"status\":\"accepted\",\"reason\":\"scheduled\",\"prompt_id\":null,\"received_at_ms\":1,\"effective_at_ms\":1,\"time_basis\":\"observed\"}".utf8)
let busy = Data("{\"status\":\"accepted\",\"reason\":\"analysis_busy\",\"prompt_id\":null}".utf8)
let fake = FakeTransport()
var outcomes: [ObservationUploadBuffer.Response] = []
fake.send { data, status, failed in
    outcomes.append(ObservationUploadBuffer.classify(data: data, statusCode: status, hasError: failed))
}
fake.completions[0](scheduled, 200, false)
assert(outcomes == [.received])
assert(ObservationUploadBuffer.classify(data: scheduled, statusCode: 200, hasError: false) == .received)
assert(ObservationUploadBuffer.classify(data: busy, statusCode: 200, hasError: false) == .analysisBusy)
assert(ObservationUploadBuffer.classify(data: scheduled, statusCode: 500, hasError: false) == .failed)
assert(ObservationUploadBuffer.classify(data: scheduled, statusCode: 200, hasError: true) == .failed)
assert(ObservationUploadBuffer.classify(data: nil, statusCode: 200, hasError: false) == .other)

let sample = String(repeating: "汉😀\n\"\\", count: 40)
let bbox: [String: Double] = ["x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4]
let line: [String: Any] = ["text": sample, "bbox": bbox, "confidence": 0.99]
let body: [String: Any] = ["event_id": UUID().uuidString.lowercased(), "kind": "platform_notice",
    "source": "ios_replaykit_platform_notice_roi", "observed_at_ms": 1_700_000_000_000,
    "availability": "available", "coverage": "user_selected_notice_roi", "reliability": "known",
    "confidence": 0.99, "content": ["text": String(repeating: "汉😀\n\"\\", count: 100),
        "region_confirmed": true, "explicit_warning_confirmed": false,
        "text_position": bbox, "ocr_lines": Array(repeating: line, count: 20)]]
let encoded = try! JSONSerialization.data(withJSONObject: body)
print("synthetic notice JSON bytes: \(encoded.count)")
assert(encoded.count <= 65_536)
let metadata = try! JSONSerialization.jsonObject(with: encoded) as! [String: Any]
assert((metadata["event_id"] as? String) == (body["event_id"] as? String))
assert(metadata["observed_at_ms"] as? Int64 == 1_700_000_000_000)
print("OK: bounded FIFO and JSON encoding sample")
