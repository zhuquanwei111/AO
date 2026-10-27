import Foundation

var failures = 0
func check(_ condition: @autoclosure () -> Bool, _ name: String) {
    let passed = condition()
    print("\(passed ? "OK" : "FAIL") \(name)")
    if !passed { failures += 1 }
}

var state = InputDiagnosticAccumulator(requestID: "request", phase: .running, now: 100)
state.record(.video, at: 100, pts: 1)
state.record(.appAudio, at: 101, pts: 2)
check(state.snapshot.video.stream.count == 1, "原始视频独立计数")
check(state.snapshot.appAudio.stream.count == 1 && state.snapshot.microphone.stream.count == 0,
      "两类音频不混淆，不依赖 OCR")
check(state.snapshot.video.stream.freshness(at: 101, active: true) == .fresh, "样本新鲜")
check(state.snapshot.video.stream.freshness(at: 105, active: true) == .stale, "无回调会过期")
state.record(.video, at: 104, pts: 1)
check(state.snapshot.video.stream.freshness(at: 104, active: true) == .timestampStalled,
      "到达计数增加不掩盖时间戳停滞")
state.record(.video, at: 105, pts: 2)
check(state.snapshot.video.stream.freshness(at: 105, active: true) == .fresh, "时间戳恢复前进")
check(state.snapshot.video.stream.freshness(at: 99, active: true) == .stale, "时钟倒退不误判新鲜")
state.record(.video, at: 110, pts: 1)
state.record(.video, at: 111, pts: 2)
check(state.snapshot.video.stream.freshness(at: 111, active: true) == .timestampStalled,
      "时间戳往返不冒充持续前进")

let oldSegment = state.snapshot.segmentID
state.reset(requestID: "request", phase: .paused, now: 106)
state.record(.video, at: 106, pts: 3)
check(state.snapshot.video.stream.count == 0, "暂停清空观测段")
check(state.snapshot.video.stream.freshness(at: 106, active: false) == .inactive, "暂停不标新鲜")
let black = ImageDiagnosticEvidence(luminances: Array(repeating: 0, count: 256),
                                    previous: nil, faceCount: 0, at: 106)!
check(!state.applyImage(black, segmentID: oldSegment), "暂停后拒绝迟到图像结果")
state.reset(requestID: "request", phase: .running, now: 107)
check(!state.applyImage(black, segmentID: oldSegment), "恢复后拒绝旧观测段结果")
check(state.snapshot.appAudio.stream.count == 0, "恢复不继承旧音频统计")
let resumedSegment = state.snapshot.segmentID
state.reset(requestID: "new-request", phase: .running, now: 108)
check(!state.applyImage(black, segmentID: resumedSegment), "区域或开关变化拒绝旧任务")
state.record(.video, at: 108, pts: nil)
check(state.snapshot.video.stream.freshness(at: 108, active: true) == .invalidTimestamp,
      "无效时间戳保留未知")
state.record(.video, at: 109, pts: 5)
check(state.snapshot.video.stream.freshness(at: 109, active: true) == .fresh, "有效时间戳可恢复")
let newSegment = state.snapshot.segmentID
check(state.applyImage(black, segmentID: newSegment), "当前观测段可接收图像证据")
check(state.snapshot.video.imageSamples == 1 && state.snapshot.video.nearBlackSamples == 1
      && state.snapshot.video.faceSamples == 1 && state.snapshot.video.faceVisibleSamples == 0,
      "区域抽样统计不把黑帧或零人脸当成功")
state.reset(requestID: "new-request", phase: .finished, now: 110)
check(!state.applyImage(black, segmentID: newSegment), "停止后拒绝旧结果")
state.reset(requestID: "new-request", phase: .running, now: 111)
check(state.snapshot.video.stream.count == 0 && state.snapshot.segmentID != newSegment,
      "重新开始建立新观测段")

check(black.isNearBlack && black.faceCount == 0, "黑帧与人脸结果分别保留")
let detail = ImageDiagnosticEvidence(luminances: [0, 1, 0, 1], previous: [0, 1, 0, 1],
                                     faceCount: nil, at: 112)!
check(!detail.isNearBlack && detail.contrast > 0.4 && detail.meanChange == 0,
      "静止有细节画面不等于黑帧")
check(detail.faceCount == nil, "缺少人脸结果不能变成无人")
check(ImageDiagnosticEvidence(luminances: [], previous: nil, faceCount: nil, at: 0) == nil,
      "空图像不伪造有效质量")
let quiet = AudioDiagnosticLevel(samples: Array(repeating: 0, count: 128))!
let tone = AudioDiagnosticLevel(samples: [0.5, -0.5, 0.5, -0.5])!
check(quiet.isNearSilent && quiet.rmsDBFS == -120, "零样本值为抽样近静音")
check(!tone.isNearSilent && abs(tone.rmsDBFS + 6.0206) < 0.001, "非静音 RMS 使用 dBFS")
check(AudioDiagnosticLevel(samples: []) == nil && AudioDiagnosticLevel(samples: [.nan]) == nil,
      "无效音频不冒充静音")

let region = DiagnosticRegion(x: -1, y: 0.9, width: 2, height: 2).clamped
check(region.x == 0 && region.width == 1 && abs(region.height - 0.1) < 0.0001,
      "区域限制在屏幕范围")
let invalidRegion = DiagnosticRegion(x: .nan, y: 0, width: 0, height: .infinity).clamped
check(invalidRegion == DiagnosticRegion.defaultRegion, "非法区域回退默认值")
let encoded = try JSONEncoder().encode(state.snapshot)
let decoded = try JSONDecoder().decode(InputDiagnosticSnapshot.self, from: encoded)
check(decoded.requestID == "new-request" && decoded.video.stream.count == 0,
      "跨进程数值摘要可往返编码")
check(encoded.count < 4096, "摘要大小有界且无媒体")
state.snapshot.phase = .completed
state.record(.video, at: 200, pts: 100)
check(state.snapshot.video.stream.count == 0, "完成的测试窗不再接受输入")
let suiteName = "input-diagnostics-test-\(UUID().uuidString)"
let defaults = UserDefaults(suiteName: suiteName)!
defer { defaults.removePersistentDomain(forName: suiteName) }
check(!InputDiagnosticsStore.configuration(defaults: defaults).enabled, "诊断默认关闭")
let saved = InputDiagnosticsStore.configure(enabled: true, region: region, defaults: defaults)!
check(InputDiagnosticsStore.configuration(defaults: defaults).id == saved.id && saved.endsAt != nil,
      "独立配置通道可往返，开启检查有结束时刻")
let disabled = InputDiagnosticsStore.configure(enabled: false, region: region, defaults: defaults)!
check(disabled.id != saved.id && disabled.endsAt == nil, "关闭检查使旧请求失效")

if failures != 0 { fatalError("\(failures) diagnostic checks failed") }
