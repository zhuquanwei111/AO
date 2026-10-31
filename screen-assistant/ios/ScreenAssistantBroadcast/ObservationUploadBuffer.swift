import Foundation

/// 由调用方串行访问，仅存编码后的单项载荷和采集代次。
struct ObservationUploadBuffer {
    struct Item {
        let id: UUID
        let body: Data
        let generation: Int
        let sessionID: String
        let token: String
        let baseURL: URL
        let isComment: Bool
    }
    enum Admission { case accepted, overflow, oversize }
    enum Response { case received, analysisBusy, failed, other }
    private(set) var pending: [Item] = []
    private(set) var active: Item?
    private(set) var overflowCount = 0
    private(set) var oversizeCount = 0
    let capacity = 3
    let maxBytes = 65_536

    mutating func append(_ item: Item) -> Admission {
        guard item.body.count <= maxBytes else { oversizeCount += 1; return .oversize }
        guard pending.count < capacity else { overflowCount += 1; return .overflow }
        pending.append(item)
        return .accepted
    }

    mutating func begin() -> Item? {
        guard active == nil, !pending.isEmpty else { return nil }
        let item = pending.removeFirst()
        active = item
        return item
    }

    mutating func complete(_ id: UUID) -> Bool {
        guard active?.id == id else { return false }
        active = nil
        return true
    }

    mutating func clear() { pending.removeAll(); active = nil }

    static func classify(data: Data?, statusCode: Int?, hasError: Bool) -> Response {
        guard !hasError, let statusCode, (200...299).contains(statusCode) else { return .failed }
        guard statusCode == 200, let data,
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              object["status"] as? String == "accepted" else { return .other }
        return object["reason"] as? String == "analysis_busy" ? .analysisBusy : .received
    }
}
