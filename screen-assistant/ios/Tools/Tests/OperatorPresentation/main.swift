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
print("OK: locale, timezone, cross-day duration and missing versus zero")
