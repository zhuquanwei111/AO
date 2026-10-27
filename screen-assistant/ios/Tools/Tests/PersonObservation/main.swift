import Foundation

// Mac上从ios目录运行：swiftc ScreenAssistantBroadcast/PersonObservationEvidence.swift
// Tools/Tests/PersonObservation/main.swift -o /tmp/person-test && /tmp/person-test
// 本测试只验证生产窗口逻辑；不把fixture视为Vision真人准确率。
func observation(_ i: Int, segment: String = "a", ratio: Double? = 0.8,
                 quality: String = "valid", confidence: Double? = 0.8) -> LivePersonObservation {
    LivePersonObservation(segmentID: segment, sequence: i, observedAtMS: Int64(1_000 + i * 3_000),
                          uptimeMS: Int64(i * 3_000), quality: quality, personCount: 1,
                          confidence: 0.8, personBox: nil, bodyVector: nil, faceVector: nil,
                          headRatio: ratio, headConfidence: confidence, ptsSeconds: Double(i * 3))
}

var window = PersonHeadWindow()
window.start("a")
for i in 0..<3 { assert(window.consume(observation(i)) == nil) }
for i in 3..<8 { assert(window.consume(observation(i, ratio: 0.5)) == nil) }
let down = window.consume(observation(8, ratio: 0.5, confidence: 0.6))!
assert(down.sampleCount == 6 && down.startedAtMS == 10_000 && down.observedAtMS == 25_000)
assert(down.confidence == 0.6)
for i in 9..<35 { assert(window.consume(observation(i, ratio: 0.5)) == nil) }
assert(window.consume(observation(35, ratio: 0.5, confidence: 0.49)) == nil)

// 固定低机位/最初就低头：只建立本段参考，不从绝对几何强行判异常。
window.start("a")
for i in 0..<50 { assert(window.consume(observation(i, ratio: 0.02)) == nil) }

for issue in ["near_black", "frozen_or_still", "low_detail", "scene_change", "occluded", "unknown"] {
    window.start("a")
    for i in 0..<3 { _ = window.consume(observation(i)) }
    for i in 3..<8 { _ = window.consume(observation(i, ratio: 0.5)) }
    assert(window.consume(observation(8, ratio: 0.5, quality: issue)) == nil)
    for i in 9..<20 { assert(window.consume(observation(i, ratio: 0.5)) == nil) }
}

// 无有效关键点/时序缺口/旧代次不串成新的持续低头。
window.start("b")
for i in 0..<20 { assert(window.consume(observation(i, segment: "a", ratio: 0.1)) == nil) }
for i in 0..<3 { _ = window.consume(observation(i, segment: "b")) }
_ = window.consume(observation(3, segment: "b", ratio: nil))
for i in 4..<12 { assert(window.consume(observation(i, segment: "b", ratio: 0.5)) == nil) }
window.start("a")
for i in 0..<3 { _ = window.consume(observation(i)) }
for i in 3..<8 { _ = window.consume(observation(i, ratio: 0.5)) }
for i in 12..<24 { assert(window.consume(observation(i, ratio: 0.5)) == nil) }

let encoded = try! JSONEncoder().encode(observation(1))
let payload = try! JSONSerialization.jsonObject(with: encoded) as! [String: Any]
assert(payload["segment_id"] as? String == "a")
assert(payload["source"] as? String == "ios_replaykit_vision")
assert(payload["coverage"] as? String == "sampled_person_roi")
assert(payload["person_box"] == nil && payload["face_vector"] == nil)
print("OK: PersonHeadWindow continuity, baseline, confidence, cooldown, invalid/old segments and Encodable")
