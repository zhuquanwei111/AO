import SwiftUI
import Combine

/// 仅用于真机输入核验；不向正常 PiP 提示流注入任何诊断文本。
struct InputDiagnosticsView: View {
    @State private var configuration = InputDiagnosticsStore.configuration()
    @State private var snapshot: InputDiagnosticSnapshot?
    @State private var now = Date().timeIntervalSince1970
    @State private var sharingError: String?
    private let timer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        Form {
            Section(header: Text("输入检查（测试）"), footer: Text(
                "只检查系统已交付的输入，不开启相机或麦克风、不播放声音。仅保存本段数值摘要，不保存画面、声音或识别正文。")) {
                NavigationLink("选择人物区域（独立于OCR）") {
                    DiagnosticRegionEditor(region: configuration.region) { region in
                        configure(enabled: configuration.enabled, region: region)
                    }
                }
                Button(configuration.enabled ? "重新开始60秒检查" : "开始60秒检查") {
                    configure(enabled: true, region: configuration.region)
                }
                Button("关闭并清空检查") {
                    configure(enabled: false, region: configuration.region)
                }
                Text(statusText)
                if let sharingError { Text(sharingError).foregroundColor(.red) }
            }

            if let value = snapshot {
                Section(header: Text("原始视频（不依赖OCR）")) {
                    streamRows(value.video.stream, reference: referenceTime(value))
                    row("原始尺寸", "\(value.video.width) × \(value.video.height)")
                    if let issue = value.video.issue { Text("视频检查：\(issue)").foregroundColor(.orange) }
                    if let issue = value.video.analysisIssue { Text("区域检查：\(issue)").foregroundColor(.orange) }
                }
                Section(header: Text("人物区域抽样"), footer: Text(
                    "近黑、低细节不等于平台拦截；人脸未检出不等于离镜。低变化无法区分静态场景和冻结；请对照低头、抬头、离镜动作及观众端实况。")) {
                    row("本段区域抽样／近黑", "\(value.video.imageSamples) / \(value.video.nearBlackSamples)")
                    row("人脸判定窗口／检出", "\(value.video.faceSamples) / \(value.video.faceVisibleSamples)")
                    if !value.video.lastBufferReady {
                        Text("当前视频缓冲不可用，人物画面未知")
                    } else if let image = value.video.image {
                        if referenceTime(value) - image.measuredAt > 3 {
                            Text("区域抽样已过期，人物画面未知")
                        } else {
                            Text(image.isNearBlack ? "区域近黑（估计），需人工核对" :
                                 image.contrast < 0.02 ? "区域细节较少，遮挡／静态仍未知" : "区域有亮度与细节线索")
                            if let count = image.faceCount {
                                Text(count > 0 ? "检测到人脸：\(count)（仍需动作对照）" : "未检出人脸：人物是否可见仍未知")
                            } else { Text("人脸结果未知") }
                            row("亮度／对比度", String(format: "%.3f / %.3f", image.mean, image.contrast))
                            row("近黑比例", String(format: "%.0f%%", image.darkFraction * 100))
                            if let change = image.meanChange {
                                row("相邻区域变化", String(format: "%.3f（非冻结判定）", change))
                            }
                        }
                    } else { Text("尚无有效区域抽样，不能判断人物输入") }
                }
                audioSection("App音频 audioApp", summary: value.appAudio, reference: referenceTime(value))
                audioSection("麦克风音频 audioMic", summary: value.microphone, reference: referenceTime(value))
                Section(header: Text("本段结果线索")) {
                    Text(resultText(value))
                    row("摘要时刻", Date(timeIntervalSince1970: value.updatedAt).formatted(date: .omitted, time: .standard))
                    row("观测段", String(value.segmentID.prefix(8)))
                    if let issue = value.issue { Text("输入检查：\(issue)") }
                }
            }

            Section(header: Text("最短真机步骤")) {
                Text("1. 在屏识前台启动PiP与系统屏识广播；选择覆盖主播预览的人物区域。可关闭识别开关来核对诊断不依赖OCR。")
                Text("2. 开始60秒检查，切到抖音原生摄像头开播。说一个测试词，低头后抬头、短暂离镜，让观众发普通评论。由观众确认画面和声音。")
                Text("3. 在抖音前台等测试窗结束再回屏识查看冻结摘要；不要先停止广播。提前返回会采到屏识界面。未检测到或过期的项目保留未知。必要时分别做正对镜头、离镜的两次检查对照。")
                Text("测试不会改变系统麦克风开关。无音频回调或近静音不证明直播本身无声；诊断不会提示你开启另一套录音。")
                Text("暂停、恢复、重新开始或保存人物区域会进入新观测段；保存区域会重新计时。关闭测试不停止广播或OCR。")
            }
        }
        .navigationTitle("输入检查")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear(perform: refresh)
        .onReceive(timer) { _ in refresh() }
    }

    private func configure(enabled: Bool, region: DiagnosticRegion) {
        guard let saved = InputDiagnosticsStore.configure(enabled: enabled, region: region) else {
            sharingError = "共享状态不可用，请核对App Group配置。"
            return
        }
        configuration = saved
        snapshot = nil
        sharingError = nil
    }

    private func refresh() {
        now = Date().timeIntervalSince1970
        configuration = InputDiagnosticsStore.configuration()
        let latest = InputDiagnosticsStore.snapshot()
        snapshot = configuration.enabled && latest?.requestID == configuration.id ? latest : nil
    }

    private var statusText: String {
        guard configuration.enabled else { return "检查已关闭" }
        guard let snapshot else { return "等待扩展确认新检查；请确认系统广播已启动。" }
        switch snapshot.phase {
        case .disabled: return "扩展检查已关闭"
        case .paused: return "系统广播已暂停；恢复后重新统计本段。"
        case .finished: return "广播已结束；以下为末段摘要，不是当前输入。"
        case .completed: return "60秒检查已结束；以下摘要已冻结。"
        case .running:
            if now - snapshot.updatedAt > 3 || now < snapshot.updatedAt {
                return "摘要已过期，广播或共享通道可能中断，实际输入未知。"
            }
            if let end = configuration.endsAt {
                let remaining = max(0, Int(ceil(end - now)))
                return remaining > 0 ? "检查中，剩余\(remaining)秒；到时冻结。" : "测试窗已结束，等待扩展确认；末次状态可能过期。"
            }
            return "检查中"
        }
    }

    private func referenceTime(_ value: InputDiagnosticSnapshot) -> TimeInterval {
        value.phase == .completed || value.phase == .finished ? value.updatedAt : now
    }

    @ViewBuilder
    private func streamRows(_ stream: DiagnosticStream, reference: TimeInterval) -> some View {
        row("本段回调数", "\(stream.count)")
        Text((snapshot?.phase == .completed ? "窗口结束时：" : "") +
             freshnessText(stream.freshness(at: reference,
                                           active: snapshot?.phase == .running || snapshot?.phase == .completed)))
        if let pts = stream.lastPTS { row("末次PTS（秒）", String(format: "%.3f", pts)) }
    }

    private func audioSection(_ title: String, summary: AudioDiagnosticSummary,
                              reference: TimeInterval) -> some View {
        Section(header: Text(title), footer: Text("每半秒抽样至多1024值；非静音可能是音乐或其他声音，不代表已经取得主播人声。")) {
            streamRows(summary.stream, reference: reference)
            if let rate = summary.sampleRate, let channels = summary.channels {
                row("格式", String(format: "%.0f Hz / %d声道", rate, channels))
            }
            if let level = summary.level, let measured = summary.measuredAt, reference - measured <= 3 {
                Text(level.isNearSilent ? "本次抽样近静音（≤ -60 dBFS）" : "本次抽样有非静音能量")
                row("RMS／峰值", String(format: "%.1f / %.1f dBFS", level.rmsDBFS, level.peakDBFS))
            } else { Text("当前音频强弱未知／抽样过期") }
            row("抽样／非静音窗口", "\(summary.measuredWindows) / \(summary.nonSilentWindows)")
            if let issue = summary.issue { Text("音频检查：\(issue)").foregroundColor(.orange) }
        }
    }

    private func freshnessText(_ value: DiagnosticFreshness) -> String {
        switch value {
        case .inactive: return "未活动"
        case .missing: return "尚未收到此类回调（不等于静音／黑屏）"
        case .stale: return "回调已过期／时钟变化，当前输入未知"
        case .invalidTimestamp: return "时间戳无效，不能确认新鲜度"
        case .timestampStalled: return "有回调，但时间戳没有持续前进"
        case .fresh: return "回调近期到达且时间戳有前进（不等于内容有效）"
        }
    }

    private func resultText(_ value: InputDiagnosticSnapshot) -> String {
        let fresh = value.video.stream.freshness(at: referenceTime(value), active: value.phase != .paused)
        return fresh == .fresh && value.video.lastBufferReady && value.video.issue == nil
            ? "视频有更新线索；是否为真实主播画面仍须人工动作对照。两类音频分别查看，未运行真机验收不记通过。"
            : "尚不能确认有效新鲜视频；先检查系统广播、区域和各项错误，不将缺失当黑屏或零音量。"
    }

    private func row(_ name: String, _ value: String) -> some View {
        HStack { Text(name); Spacer(); Text(value).foregroundColor(.secondary).multilineTextAlignment(.trailing) }
    }
}

private struct DiagnosticRegionEditor: View {
    @Environment(\.dismiss) private var dismiss
    @State private var rect: CGRect
    let onSave: (DiagnosticRegion) -> Void

    init(region: DiagnosticRegion, onSave: @escaping (DiagnosticRegion) -> Void) {
        let value = region.clamped
        _rect = State(initialValue: CGRect(x: value.x, y: value.y, width: value.width, height: value.height))
        self.onSave = onSave
    }

    var body: some View {
        ROIEditorView(roi: $rect, title: "编辑人物区域")
            .toolbar {
                ToolbarItem(placement: .bottomBar) {
                    Button("保存人物区域") {
                        onSave(DiagnosticRegion(x: Double(rect.minX), y: Double(rect.minY),
                                                width: Double(rect.width), height: Double(rect.height)))
                        dismiss()
                    }
                }
            }
    }
}
