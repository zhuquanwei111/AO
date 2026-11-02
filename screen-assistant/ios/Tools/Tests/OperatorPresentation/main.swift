import Foundation

let locale = Locale(identifier: "en_US_POSIX")
let utc = TimeZone(secondsFromGMT: 0)!
let east = TimeZone(secondsFromGMT: 8 * 3600)!
let start: Int64 = 1_704_151_800_000 // 2024-01-01 23:30 UTC
let utcTime = OperatorPresentation.date(start, locale: locale, timeZone: utc)
let eastTime = OperatorPresentation.date(start, locale: locale, timeZone: east)
assert(utcTime.contains("1/1/24"))
assert(eastTime.contains("1/2/24"))
assert(OperatorPresentation.duration(start: start, end: start + 7_200_000) == "2小时0分")
assert(OperatorPresentation.duration(start: start, end: start - 1) == "时长待确认")
assert(OperatorPresentation.people(nil, approximate: false, locale: locale) == nil)
assert(OperatorPresentation.people(0, approximate: false, locale: locale) == "0 人")
assert(OperatorPresentation.people(1200, approximate: true, locale: locale) == "约1,200 人")
assert(OperatorPresentation.people(.nan, approximate: false, locale: locale) == nil)
func upload(_ counts: [String: Int]) -> String {
    OperatorPresentation.observationUploadDescription { counts[$0, default: 0] }
}
let pastUnsent = "本机历史：部分识别内容未发送。"
assert(upload(["cancel_unsent": 1, "received": 1]) == pastUnsent)
assert(upload(["expired": 1]) == pastUnsent)
assert(upload(["cancel_unsent": 1, "expired": 1]) == pastUnsent)
assert(upload(["expired": 1, "unknown": 1]) == "本机历史：曾有发送结果未确认；部分识别内容未发送。")
assert(upload(["waiting_batches": 1]) == "暂未收到画面信息。")
assert(upload(["overflow_batch": 1, "overflow_comment": 1]) == "本机历史：曾有画面信息未送达。")
assert(upload(["oversize_comment": 1]) == "本机历史：曾有画面信息未送达。")
assert(upload(["unknown": 1]) == "本机历史：曾有发送结果未确认。")
assert(upload(["rejected": 1]) == "本机历史：曾有画面信息未送达。")
assert(upload(["received": 1]) == "本机历史：曾收到过画面信息。")
assert(OperatorPresentation.personROISummary(configured: true) == "已设置，请按示意图核对")
assert(OperatorPresentation.personROISummary(configured: false) == "未单独设置")
print("OK: locale, timezone, cross-day duration and missing versus zero")
