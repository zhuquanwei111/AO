import Foundation

/// 只存显式选项和数值状态；不存PCM或转写。
struct AudioCaptureSettings: Codable, Equatable {
    var id = UUID().uuidString.lowercased()
    var enabled = false
    var source = "audioApp"
    var appHostConfirmed = false
    var micHostConfirmed = false

    func includes(_ track: String) -> Bool { source == "both" || source == track }
    func hostConfirmed(_ track: String) -> Bool {
        track == "audioMic" ? micHostConfirmed : appHostConfirmed
    }
    static func load() -> Self {
        guard let data = AppGroup.defaults?.data(forKey: "audio.settings"),
              let value = try? JSONDecoder().decode(Self.self, from: data) else { return Self() }
        return value
    }
    static func save(_ value: Self) {
        var updated = value
        updated.id = UUID().uuidString.lowercased()
        AppGroup.defaults?.set(try? JSONEncoder().encode(updated), forKey: "audio.settings")
    }
}

struct AudioCaptureStatus: Codable {
    var phase = "未启用"
    var copiedApp = 0
    var copiedMic = 0
    var dropped = 0
    var uploaded = 0
    var issue: String?

    static func load() -> Self {
        guard let data = AppGroup.defaults?.data(forKey: "audio.status"),
              let value = try? JSONDecoder().decode(Self.self, from: data) else { return Self() }
        return value
    }
    func save() {
        AppGroup.defaults?.set(try? JSONEncoder().encode(self), forKey: "audio.status")
    }
}
