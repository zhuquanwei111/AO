import Foundation

/// 面向运营的时间与人数格式；缺失读数不转换为零。
enum OperatorPresentation {
    static func observationUploadDescription(value: (String) -> Int) -> String {
        let unsent = ["limit_comment", "limit_count", "bytes_comment", "bytes_count",
                      "oversize_comment", "oversize_count", "invalid_comment", "invalid_count",
                      "overflow_comment", "overflow_count"]
            .reduce(0) { $0 + value($1) }
        var messages: [String] = []
        if unsent > 0 || value("rejected") > 0 { messages.append("部分画面信息未送达") }
        if value("unknown") > 0 { messages.append("部分发送结果未确认") }
        if !messages.isEmpty { return messages.joined(separator: "；") + "。" }
        if value("received") > 0 || value("duplicate") > 0 || value("merged") > 0 {
            return "已收到部分画面信息；提示以实际收到的建议为准。"
        }
        return "暂未收到画面信息。"
    }

    static func date(_ milliseconds: Int64, locale: Locale = .autoupdatingCurrent,
                     timeZone: TimeZone = .autoupdatingCurrent) -> String {
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.timeZone = timeZone
        formatter.dateStyle = .short
        formatter.timeStyle = .short
        return formatter.string(from: Date(timeIntervalSince1970: Double(milliseconds) / 1000))
    }

    static func duration(start: Int64, end: Int64) -> String {
        let (difference, overflow) = end.subtractingReportingOverflow(start)
        guard !overflow, difference >= 0 else { return "时长待确认" }
        let seconds = difference / 1000
        let hours = seconds / 3600
        let minutes = (seconds % 3600) / 60
        let remaining = seconds % 60
        if hours > 0 { return "\(hours)小时\(minutes)分" }
        if minutes > 0 { return "\(minutes)分\(remaining)秒" }
        return "\(remaining)秒"
    }

    static func people(_ value: Double?, approximate: Bool,
                       locale: Locale = .autoupdatingCurrent) -> String? {
        guard let value, value.isFinite else { return nil }
        let formatter = NumberFormatter()
        formatter.locale = locale
        formatter.numberStyle = .decimal
        formatter.maximumFractionDigits = 0
        formatter.minimumFractionDigits = 0
        guard let number = formatter.string(from: NSNumber(value: value)) else { return nil }
        return "\(approximate ? "约" : "")\(number) 人"
    }
}
