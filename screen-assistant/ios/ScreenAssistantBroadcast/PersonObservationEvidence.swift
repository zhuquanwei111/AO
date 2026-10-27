import Foundation

/// 数值观测，不含媒体、身份或情绪。onObservation 的接收者须保留段/时间/未知状态。
struct LivePersonObservation: Encodable {
    let source = "ios_replaykit_vision"
    let coverage = "sampled_person_roi"
    let segmentID: String
    let sequence: Int
    let observedAtMS: Int64
    let uptimeMS: Int64
    let quality: String
    let personCount: Int?
    /// 实际最强人物检测的confidence，非“人物不存在”的概率。
    let confidence: Double?
    let personBox: [Double]? // 相对本段独立人物ROI的左上归一化坐标，不是全屏框。
    let bodyVector: [Double]?
    let faceVector: [Double]?
    let headRatio: Double?
    let headConfidence: Double?
    let ptsSeconds: Double?

    enum CodingKeys: String, CodingKey {
        case source, coverage, segmentID = "segment_id", sequence, observedAtMS = "observed_at_ms"
        case uptimeMS = "uptime_ms", quality, personCount = "person_count", confidence
        case personBox = "person_box", bodyVector = "body_vector", faceVector = "face_vector"
        case headRatio = "head_ratio", headConfidence = "head_confidence", ptsSeconds = "pts_seconds"
    }
}

struct PersonHeadCandidate {
    let startedAtMS: Int64
    let observedAtMS: Int64
    let confidence: Double
    let sampleCount: Int
}

/// 旧head_down契约的保守窗口：先建立本段参考，再看相对肩宽的连续变化。
/// 纯Foundation，Apple工具链可直接编译对应行为测试；不把静态低机位当低头。
struct PersonHeadWindow {
    private var segment = ""
    private var reference: [Double] = []
    private var previous: LivePersonObservation?
    private var started: LivePersonObservation?
    private var count = 0
    private var minimumConfidence = 1.0
    private var lastPromptUptime: Int64?

    mutating func start(_ segment: String) {
        self = Self()
        self.segment = segment
    }

    private mutating func clearEvidence() {
        reference.removeAll(keepingCapacity: true)
        started = nil
        count = 0
        minimumConfidence = 1
    }

    mutating func consume(_ value: LivePersonObservation) -> PersonHeadCandidate? {
        guard value.segmentID == segment else { return nil }
        if let previous {
            guard value.sequence > previous.sequence, value.uptimeMS > previous.uptimeMS else { return nil }
            let gap = value.uptimeMS - previous.uptimeMS
            if gap > 6_500 || value.observedAtMS <= previous.observedAtMS ||
                abs((value.observedAtMS - previous.observedAtMS) - gap) > 2_000 {
                clearEvidence()
            }
        }
        previous = value
        guard value.quality == "valid", value.personCount == 1,
              let ratio = value.headRatio, ratio.isFinite, (-5...5).contains(ratio),
              let confidence = value.headConfidence, confidence.isFinite,
              (0.5...1).contains(confidence) else {
            clearEvidence()
            return nil
        }
        if reference.count < 3 {
            reference.append(ratio)
            return nil
        }
        // 参考三次必须稳定；摄像机移动/初始姿态未稳定时不建立“正常”断言。
        guard let high = reference.max(), let low = reference.min(), high - low <= 0.12 else {
            reference = [ratio]
            return nil
        }
        let baseline = reference.reduce(0, +) / Double(reference.count)
        guard baseline - ratio >= 0.22 else {
            started = nil
            count = 0
            minimumConfidence = 1
            return nil
        }
        if started == nil { started = value }
        count = min(count + 1, 100)
        minimumConfidence = min(minimumConfidence, confidence)
        guard let started, count >= 6, value.uptimeMS - started.uptimeMS >= 15_000,
              lastPromptUptime == nil || value.uptimeMS - lastPromptUptime! >= 90_000 else { return nil }
        lastPromptUptime = value.uptimeMS
        return PersonHeadCandidate(startedAtMS: started.observedAtMS, observedAtMS: value.observedAtMS,
                                   confidence: minimumConfidence, sampleCount: count)
    }
}
