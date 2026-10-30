import Foundation

/// 仅管理本场待显示的提示；调用方在主线程持有并按实际排版选择帧。
struct LivePromptDelivery {
    /// 缺少服务端期限时仅设置本机显示上限，不代表服务端生成时间。
    static func displayExpiry(_ serverExpiry: Int64?, receivedAtMS: Int64) -> Int64 {
        serverExpiry ?? receivedAtMS + 15_000
    }

    struct Item {
        let id: String
        let text: String
        let expiresAtMS: Int64
        let priority: Int
    }

    private(set) var pending: [Item] = []
    private(set) var overflowCount = 0
    private(set) var expiredCount = 0
    private var seen: Set<String> = []
    private var lastOrdinaryFrameMS: Int64?
    private var urgentUntilMS: Int64?
    let capacity: Int
    let intervalMS: Int64

    init(capacity: Int = 16, intervalMS: Int64 = 5_000) {
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
            if item.expiresAtMS <= nowMS {
                expiredCount += 1
            } else if pending.count >= capacity {
                overflowCount += 1
                if item.priority >= 100,
                   let index = pending.lastIndex(where: { $0.priority < 100 }) {
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
        let urgent = pending.firstIndex(where: { $0.priority >= 100 })
        if urgent == nil, let until = urgentUntilMS, nowMS < until { return [] }
        if urgent == nil, let last = lastOrdinaryFrameMS, nowMS - last < intervalMS { return [] }
        if let index = urgent, index != 0 { pending.insert(pending.remove(at: index), at: 0) }
        var frame: [Item] = []
        while let first = pending.first {
            if let initial = frame.first, initial.priority >= 100 && first.priority < 100 { break }
            let candidate = frame + [first]
            if !frame.isEmpty && !fits(candidate) { break }
            frame.append(pending.removeFirst())
            if !fits(frame) { break } // 单条超长由 renderer 明确省略
        }
        if !frame.isEmpty { lastOrdinaryFrameMS = nowMS }
        if let until = frame.filter({ $0.priority >= 100 }).map(\.expiresAtMS).min() {
            urgentUntilMS = until
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
