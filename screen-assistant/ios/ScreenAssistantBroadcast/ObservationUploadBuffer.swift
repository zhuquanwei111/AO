import Foundation

/// 由调用方串行访问。每个槽位是一次采样，批内仍逐条发送旧接口。
struct ObservationUploadBuffer {
    struct Event {
        let id: UUID
        let body: Data
        let isComment: Bool
        let observedAtMS: Int64
        var lineIndex: Int? = nil
        var useObservationEndpoint = false
    }
    static let maxAgeMS: Int64 = 10_000
    static func isExpired(_ event: Event, nowMS: Int64) -> Bool {
        nowMS >= event.observedAtMS && nowMS - event.observedAtMS > maxAgeMS
    }
    static func comment(text: String, position: [String: Double]?, confidence: Double?,
                        observedAtMS: Int64, id: UUID = UUID(), lineIndex: Int? = nil) -> Event? {
        let stripped = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !stripped.isEmpty, stripped.unicodeScalars.count <= 500 else { return nil }
        var payload: [String: Any] = [
            "event_id": id.uuidString.lowercased(), "input_mode": "live_observation",
            "source": "ios_replaykit_ocr", "coverage": "ocr_visible_sample",
            "reliability": "known", "text": text, "observed_at_ms": observedAtMS
        ]
        if let position { payload["text_position"] = position }
        if let confidence { payload["confidence"] = confidence }
        guard let body = try? JSONSerialization.data(withJSONObject: payload) else { return nil }
        return Event(id: id, body: body, isComment: true, observedAtMS: observedAtMS, lineIndex: lineIndex)
    }

    /// 固定评论前缀后再编码总数；额外元数据同样计入实际 UTF-8 字节上限。
    static func metadataComments(_ candidates: [Event], batchID: UUID,
                                 occupiedBytes: Int) -> (events: [Event], dropped: Int) {
        guard occupiedBytes >= 0, occupiedBytes <= BatchBuilder.maxBytes else {
            return ([], candidates.count)
        }
        var count = min(candidates.count, BatchBuilder.maxComments)
        while count > 0 {
            var result: [Event] = []
            var bytes = occupiedBytes
            for original in candidates.prefix(count) {
                guard let index = original.lineIndex, (0...65535).contains(index),
                      var payload = (try? JSONSerialization.jsonObject(with: original.body)) as? [String: Any],
                      let text = payload.removeValue(forKey: "text") as? String else { break }
                var content: [String: Any] = ["text": text]
                if let position = payload.removeValue(forKey: "text_position") { content["text_position"] = position }
                payload.removeValue(forKey: "input_mode")
                payload["kind"] = "comment"
                payload["availability"] = "available"
                payload["capture_batch_id"] = batchID.uuidString.lowercased()
                payload["line_index"] = index
                payload["capture_comment_count"] = count
                payload["content"] = content
                guard let body = try? JSONSerialization.data(withJSONObject: payload),
                      body.count <= BatchBuilder.maxItemBytes,
                      body.count <= BatchBuilder.maxBytes - bytes else { break }
                bytes += body.count
                let event = Event(id: original.id, body: body, isComment: true,
                                  observedAtMS: original.observedAtMS, lineIndex: index,
                                  useObservationEndpoint: true)
                result.append(event)
            }
            if result.count == count { return (result, candidates.count - count) }
            count = min(count - 1, result.count)
        }
        return ([], candidates.count)
    }
    struct Batch {
        let id: UUID
        let events: [Event]
        let observedAtMS: Int64
        let generation: Int
        let sessionID: String
        let token: String
        let baseURL: URL
        var cursor = 0

        var current: Event? { cursor < events.count ? events[cursor] : nil }
        var remaining: ArraySlice<Event> { events[cursor...] }
    }
    enum BuildAdmission { case accepted, commentLimit, countLimit, byteLimit, itemOversize }
    struct BatchBuilder {
        private(set) var events: [Event] = []
        private(set) var bytes = 0
        private var comments = 0
        private var counts = 0
        private var closed: BuildAdmission?
        static let maxComments = 128
        static let maxBytes = 524_288
        static let maxItemBytes = 65_536

        mutating func append(_ event: Event) -> BuildAdmission {
            if let closed { return closed }
            if event.isComment && comments >= Self.maxComments {
                closed = .commentLimit
                return .commentLimit
            }
            if !event.isComment && counts >= 1 {
                closed = .countLimit
                return .countLimit
            }
            if event.body.count > Self.maxItemBytes {
                closed = .itemOversize
                return .itemOversize
            }
            if event.body.count > Self.maxBytes - bytes {
                closed = .byteLimit
                return .byteLimit
            }
            events.append(event)
            bytes += event.body.count
            if event.isComment { comments += 1 } else { counts += 1 }
            return .accepted
        }
    }
    enum Admission { case accepted, overflow, invalid }
    enum Response { case received, duplicate, merged, analysisBusy, rejected, unknown }
    struct Discarded {
        let unknown: Int
        let unsent: Int
    }
    private(set) var pending: [Batch] = []
    private(set) var active: Batch?
    let capacity = 3

    mutating func append(_ batch: Batch) -> Admission {
        guard !batch.events.isEmpty, batch.cursor == 0 else { return .invalid }
        var bytes = 0
        var comments = 0
        var counts = 0
        for event in batch.events {
            guard event.body.count <= BatchBuilder.maxItemBytes,
                  event.body.count <= BatchBuilder.maxBytes - bytes else { return .invalid }
            bytes += event.body.count
            if event.isComment { comments += 1 } else { counts += 1 }
        }
        guard comments <= BatchBuilder.maxComments, counts <= 1 else { return .invalid }
        guard pending.count < capacity else { return .overflow }
        pending.append(batch)
        return .accepted
    }

    mutating func begin() -> Batch? {
        if active == nil, !pending.isEmpty { active = pending.removeFirst() }
        return active
    }

    mutating func complete(batchID: UUID, eventID: UUID) -> Bool {
        guard var batch = active, batch.id == batchID, batch.current?.id == eventID else { return false }
        batch.cursor += 1
        active = batch.current == nil ? nil : batch
        return true
    }

    mutating func clear() { pending.removeAll(); active = nil }

    mutating func discard(inFlight: Bool) -> Discarded {
        let activeRemaining = active?.remaining.count ?? 0
        let unknown = inFlight && activeRemaining > 0 ? 1 : 0
        let waiting = pending.reduce(0) { $0 + $1.events.count }
        let result = Discarded(unknown: unknown, unsent: activeRemaining - unknown + waiting)
        clear()
        return result
    }

    static func classify(data: Data?, statusCode: Int?, hasError: Bool) -> Response {
        guard !hasError, let statusCode else { return .unknown }
        if (400...499).contains(statusCode) { return .rejected }
        guard (200...299).contains(statusCode), let data,
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let status = object["status"] as? String else { return .unknown }
        if status == "duplicate" { return .duplicate }
        guard status == "accepted" else { return .unknown }
        if object["reason"] as? String == "duplicate_message" { return .merged }
        return object["reason"] as? String == "analysis_busy" ? .analysisBusy : .received
    }
}
