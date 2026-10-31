import Foundation

/// 仅管理本场待显示的提示；调用方在主线程持有并按实际排版选择帧。
struct LivePromptDelivery {
    /// 截止点使用本机连续单调毫秒；无法验证时间锚时返回 nil。
    static func displayExpiry(_ serverExpiry: Int64?, serverTimeMS: Int64?, processingMS: Int64?,
                              startNS: UInt64?, endNS: UInt64?) -> Int64? {
        guard let expiry = serverExpiry, let server = serverTimeMS, let processing = processingMS,
              let start = startNS, let end = endNS, expiry >= 0, server >= 0,
              processing >= 0, end >= start else { return nil }
        let elapsed = end - start
        let rounded = elapsed / 1_000_000 + (elapsed % 1_000_000 == 0 ? 0 : 1)
        guard rounded <= UInt64(Int64.max), end / 1_000_000 <= UInt64(Int64.max) else { return nil }
        let rtt = Int64(rounded)
        guard processing <= rtt else { return nil }
        let remaining = max(0, expiry - min(expiry, server) - (rtt - processing) - 1)
        let (deadline, overflow) = Int64(end / 1_000_000).addingReportingOverflow(remaining)
        return overflow ? nil : deadline
    }

    struct Item {
        let id: String
        let text: String
        let expiresAtMS: Int64
        let priority: Int?
        let timingInvalid: Bool
        let priorityInvalid: Bool

        init(id: String, text: String, expiresAtMS: Int64, priority: Int?,
             timingInvalid: Bool = false, priorityInvalid: Bool = false) {
            self.id = id
            self.text = text
            self.expiresAtMS = expiresAtMS
            self.priority = priority
            self.timingInvalid = timingInvalid
            self.priorityInvalid = priorityInvalid
        }
    }

    private(set) var pending: [Item] = []
    private(set) var overflowCount = 0
    private(set) var expiredCount = 0
    private var seen: Set<String> = []
    private var lastOrdinaryFrameMS: Int64?
    private var urgentUntilMS: Int64?
    let capacity: Int
    let intervalMS: Int64

    init(capacity: Int = 16, intervalMS: Int64 = 8_000) {
        self.capacity = capacity
        self.intervalMS = intervalMS
    }

    static func unexpired(_ items: [Item], nowMS: Int64) -> [Item] {
        items.filter { $0.expiresAtMS > nowMS }
    }

    mutating func receive(_ items: [Item], nowMS: Int64) -> [Item] {
        var fresh: [Item] = []
        expire(nowMS: nowMS)
        for item in items where seen.insert(item.id).inserted {
            fresh.append(item)
            if item.timingInvalid || item.priorityInvalid || item.priority == nil { continue }
            if item.expiresAtMS <= nowMS {
                expiredCount += 1
            } else if pending.count >= capacity {
                overflowCount += 1
                if let priority = item.priority, priority >= 100,
                   let index = pending.lastIndex(where: { ($0.priority ?? Int.min) < 100 }) {
                    pending.remove(at: index)
                    pending.append(item)
                }
            } else {
                pending.append(item)
            }
        }
        return fresh
    }

    mutating func take(nowMS: Int64, fits: ([Item]) -> Bool) -> [Item] {
        expire(nowMS: nowMS)
        guard !pending.isEmpty else { return [] }
        let urgent = pending.firstIndex(where: { ($0.priority ?? Int.min) >= 100 })
        if urgent == nil, let until = urgentUntilMS, nowMS < until { return [] }
        if urgent == nil, let last = lastOrdinaryFrameMS, nowMS - last < intervalMS { return [] }
        if let index = urgent, index != 0 { pending.insert(pending.remove(at: index), at: 0) }
        var frame: [Item] = []
        while let first = pending.first {
            if let initial = frame.first, (initial.priority ?? Int.min) >= 100 && (first.priority ?? Int.min) < 100 { break }
            let candidate = frame + [first]
            if !frame.isEmpty && !fits(candidate) { break }
            frame.append(pending.removeFirst())
            if !fits(frame) { break } // 单条超长由 renderer 明确省略
        }
        if !frame.isEmpty { lastOrdinaryFrameMS = nowMS }
        if let until = frame.filter({ ($0.priority ?? Int.min) >= 100 }).map(\.expiresAtMS).min() {
            urgentUntilMS = min(until, nowMS + intervalMS)
        }
        return frame
    }

    mutating func clear() {
        pending.removeAll()
        seen.removeAll()
        lastOrdinaryFrameMS = nil
        urgentUntilMS = nil
    }

    private mutating func expire(nowMS: Int64) {
        let before = pending.count
        pending.removeAll { $0.expiresAtMS <= nowMS }
        expiredCount += before - pending.count
    }
}
