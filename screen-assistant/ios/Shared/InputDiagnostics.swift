import Foundation

/// 测试专用数值摘要。不得添加画面、音频、OCR/回复正文或任意错误正文。
struct DiagnosticRegion: Codable, Equatable {
    var x: Double
    var y: Double
    var width: Double
    var height: Double
    static let defaultRegion = DiagnosticRegion(x: 0.05, y: 0.05, width: 0.9, height: 0.7)

    var clamped: DiagnosticRegion {
        guard [x, y, width, height].allSatisfy({ $0.isFinite }), width > 0, height > 0 else {
            return Self.defaultRegion
        }
        let left = min(max(x, 0), 0.95)
        let top = min(max(y, 0), 0.95)
        return DiagnosticRegion(x: left, y: top, width: min(max(width, 0.05), 1 - left),
                                height: min(max(height, 0.05), 1 - top))
    }
}

struct InputDiagnosticConfiguration: Codable {
    var id: String
    var enabled: Bool
    var region: DiagnosticRegion
    var endsAt: TimeInterval?
    static let disabled = InputDiagnosticConfiguration(id: "disabled", enabled: false,
                                                       region: .defaultRegion)
}

enum DiagnosticPhase: String, Codable { case running, paused, finished, completed, disabled }
enum DiagnosticSource { case video, appAudio, microphone }
enum DiagnosticFreshness { case inactive, missing, stale, invalidTimestamp, timestampStalled, fresh }

struct DiagnosticStream: Codable {
    var count = 0
    var lastArrivalAt: TimeInterval?
    var lastPTS: Double?
    private var highestPTS: Double?
    var lastProgressAt: TimeInterval?
    var latestPTSValid = false

    init() {}

    mutating func record(at now: TimeInterval, pts: Double?) {
        if count < Int.max { count += 1 }
        lastArrivalAt = now
        latestPTSValid = pts?.isFinite == true
        guard let pts, pts.isFinite else { return }
        if highestPTS == nil || pts > highestPTS! {
            lastProgressAt = now
            highestPTS = pts
        }
        lastPTS = pts
    }

    func freshness(at now: TimeInterval, active: Bool) -> DiagnosticFreshness {
        guard active else { return .inactive }
        guard let arrival = lastArrivalAt else { return .missing }
        guard now >= arrival, now - arrival <= 3 else { return .stale }
        guard latestPTSValid else { return .invalidTimestamp }
        guard let progress = lastProgressAt, now >= progress, now - progress <= 3 else {
            return .timestampStalled
        }
        return .fresh
    }
}

struct AudioDiagnosticLevel: Codable {
    let rmsDBFS: Double
    let peakDBFS: Double
    var isNearSilent: Bool { rmsDBFS <= -60 }

    init?(samples: [Double]) {
        guard !samples.isEmpty, samples.allSatisfy({ $0.isFinite }) else { return nil }
        let values = samples.map { min(abs($0), 1) }
        let rms = sqrt(values.reduce(0) { $0 + $1 * $1 } / Double(values.count))
        rmsDBFS = 20 * log10(max(rms, 0.000001))
        peakDBFS = 20 * log10(max(values.max() ?? 0, 0.000001))
    }
}

struct ImageDiagnosticEvidence: Codable {
    let measuredAt: TimeInterval
    let mean: Double
    let contrast: Double
    let darkFraction: Double
    let meanChange: Double?
    let faceCount: Int?
    var isNearBlack: Bool { mean < 0.04 && darkFraction > 0.98 }

    init?(luminances: [Double], previous: [Double]?, faceCount: Int?, at now: TimeInterval) {
        guard !luminances.isEmpty, luminances.allSatisfy({ $0.isFinite }) else { return nil }
        measuredAt = now
        let average = luminances.reduce(0, +) / Double(luminances.count)
        mean = average
        contrast = sqrt(luminances.reduce(0) { $0 + pow($1 - average, 2) }
                        / Double(luminances.count))
        darkFraction = Double(luminances.filter { $0 < 0.04 }.count) / Double(luminances.count)
        if let previous, previous.count == luminances.count {
            meanChange = zip(luminances, previous).reduce(0) { $0 + abs($1.0 - $1.1) }
                / Double(luminances.count)
        } else { meanChange = nil }
        self.faceCount = faceCount
    }
}

struct VideoDiagnosticSummary: Codable {
    var stream = DiagnosticStream()
    var width = 0
    var height = 0
    var image: ImageDiagnosticEvidence?
    var imageSamples = 0
    var faceSamples = 0
    var faceVisibleSamples = 0
    var nearBlackSamples = 0
    var lastBufferReady = false
    var issue: String?
    var analysisIssue: String?
}

struct AudioDiagnosticSummary: Codable {
    var stream = DiagnosticStream()
    var sampleRate: Double?
    var channels: Int?
    var measuredAt: TimeInterval?
    var level: AudioDiagnosticLevel?
    var measuredWindows = 0
    var nonSilentWindows = 0
    var issue: String?

    mutating func measure(_ value: AudioDiagnosticLevel, at now: TimeInterval) {
        level = value
        measuredAt = now
        if measuredWindows < Int.max { measuredWindows += 1 }
        if !value.isNearSilent, nonSilentWindows < Int.max { nonSilentWindows += 1 }
        issue = nil
    }
}

struct InputDiagnosticSnapshot: Codable {
    let requestID: String
    let segmentID: String
    var phase: DiagnosticPhase
    var updatedAt: TimeInterval
    var issue: String?
    var video = VideoDiagnosticSummary()
    var appAudio = AudioDiagnosticSummary()
    var microphone = AudioDiagnosticSummary()
}

/// 仅由扩展诊断串行队列访问；没有媒体队列或跨会话历史。
struct InputDiagnosticAccumulator {
    var snapshot: InputDiagnosticSnapshot

    init(requestID: String, phase: DiagnosticPhase, now: TimeInterval) {
        snapshot = InputDiagnosticSnapshot(requestID: requestID, segmentID: UUID().uuidString,
                                          phase: phase, updatedAt: now)
    }

    mutating func reset(requestID: String, phase: DiagnosticPhase, now: TimeInterval) {
        self = Self(requestID: requestID, phase: phase, now: now)
    }

    mutating func record(_ source: DiagnosticSource, at now: TimeInterval, pts: Double?) {
        guard snapshot.phase == .running else { return }
        switch source {
        case .video: snapshot.video.stream.record(at: now, pts: pts)
        case .appAudio: snapshot.appAudio.stream.record(at: now, pts: pts)
        case .microphone: snapshot.microphone.stream.record(at: now, pts: pts)
        }
    }

    @discardableResult
    mutating func applyImage(_ image: ImageDiagnosticEvidence, segmentID: String) -> Bool {
        guard snapshot.phase == .running, snapshot.segmentID == segmentID else { return false }
        snapshot.video.image = image
        if snapshot.video.imageSamples < Int.max { snapshot.video.imageSamples += 1 }
        if let faces = image.faceCount {
            if snapshot.video.faceSamples < Int.max { snapshot.video.faceSamples += 1 }
            if faces > 0, snapshot.video.faceVisibleSamples < Int.max { snapshot.video.faceVisibleSamples += 1 }
        }
        if image.isNearBlack, snapshot.video.nearBlackSamples < Int.max { snapshot.video.nearBlackSamples += 1 }
        return true
    }
}

/// 复用 App Group 状态通道；每秒最多保存一次数值摘要（生命周期变化除外）。
enum InputDiagnosticsStore {
    private static let configurationKey = "diagnostics.configuration.v1"
    private static let snapshotKey = "diagnostics.snapshot.v1"

    static func configuration(defaults: UserDefaults? = AppGroup.defaults) -> InputDiagnosticConfiguration {
        guard let data = defaults?.data(forKey: configurationKey),
              let value = try? JSONDecoder().decode(InputDiagnosticConfiguration.self, from: data)
        else { return .disabled }
        return value
    }

    @discardableResult
    static func configure(enabled: Bool, region: DiagnosticRegion,
                          defaults: UserDefaults? = AppGroup.defaults) -> InputDiagnosticConfiguration? {
        guard let defaults else { return nil }
        let value = InputDiagnosticConfiguration(id: UUID().uuidString, enabled: enabled,
                                                region: region.clamped,
                                                endsAt: enabled ? Date().timeIntervalSince1970 + 60 : nil)
        guard let data = try? JSONEncoder().encode(value) else { return nil }
        defaults.set(data, forKey: configurationKey)
        if !enabled { defaults.removeObject(forKey: snapshotKey) }
        return value
    }

    static func snapshot(defaults: UserDefaults? = AppGroup.defaults) -> InputDiagnosticSnapshot? {
        guard let data = defaults?.data(forKey: snapshotKey), data.count <= 4096 else { return nil }
        return try? JSONDecoder().decode(InputDiagnosticSnapshot.self, from: data)
    }

    static func publish(_ snapshot: InputDiagnosticSnapshot) {
        guard let data = try? JSONEncoder().encode(snapshot), data.count <= 4096 else { return }
        AppGroup.defaults?.set(data, forKey: snapshotKey)
    }
}
