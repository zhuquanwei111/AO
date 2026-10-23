import SwiftUI
import ReplayKit

/// 系统“开始直播”入口（RPSystemBroadcastPickerView，官方唯一启动路径；
/// 无法程序化启动广播）。preferredExtension 绑定本 App 的广播扩展，
/// 用户点击后在系统弹窗中确认。
struct BroadcastPickerSection: UIViewRepresentable {
    static let extensionBundleID = "com.screenassistant.ios.broadcast"
    var showsMicrophoneButton = false

    func makeUIView(context: Context) -> RPSystemBroadcastPickerView {
        let view = RPSystemBroadcastPickerView()
        view.preferredExtension = Self.extensionBundleID
        view.showsMicrophoneButton = showsMicrophoneButton
        return view
    }

    func updateUIView(_ uiView: RPSystemBroadcastPickerView, context: Context) {
        uiView.showsMicrophoneButton = showsMicrophoneButton
    }
}

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @ObservedObject private var pip = PiPDisplayController.shared
    @State private var confirmsLiveAreaPreset = false

    var body: some View {
        NavigationView {
            Form {
                Section(header: Text("服务端")) {
                    TextField("http://192.168.1.10:8000", text: $model.serverURLString)
                        .keyboardType(.URL)
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                        .disabled(model.liveActive)
                    if !model.serverURLString.isEmpty && !model.serverURLIsValid {
                        Label("地址无效：需以 http:// 或 https:// 开头", systemImage: "exclamationmark.triangle.fill")
                            .foregroundColor(.orange)
                            .font(.footnote)
                    }
                    Stepper(value: $model.requestTimeout, in: 2...30, step: 1) {
                        Text("请求超时：\(Int(model.requestTimeout)) 秒")
                    }
                }

                Section(header: Text("识别间隔")) {
                    Stepper(value: $model.intervalSeconds, in: 1...60, step: 1) {
                        Text("每 \(Int(model.intervalSeconds)) 秒识别一次")
                    }
                }

                Section(header: Text("识别区域（归一化）"),
                        footer: Text(roiLimitationNote)) {
                    NavigationLink("编辑识别区域") {
                        ROIEditorView(roi: $model.roi)
                    }
                    Text(roiSummary)
                        .font(.footnote)
                        .foregroundColor(.secondary)
                }

                Section(header: Text("直播数据识别区域"),
                        footer: Text("仅识别你在主播界面选定的裁剪区域。未保存区域时停用对应来源；请勿把热度或累计观看选作在线人数。")) {
                    Button("应用参考直播区域预设…") {
                        confirmsLiveAreaPreset = true
                    }
                    .disabled(model.liveActive)

                    NavigationLink("编辑主播人物区域") {
                        ROIEditorView(roi: $model.personROI,
                                      title: "编辑主播人物区域",
                                      showsPresets: false)
                    }
                    .disabled(model.liveActive)

                    Text("当前人物区域：\(roiSummary(for: model.personROI))")
                        .font(.footnote)
                        .foregroundColor(.secondary)

                    NavigationLink {
                        OptionalROIEditorView(title: "选择在线人数区域", currentROI: model.onlineCountROI) {
                            model.onlineCountROI = $0
                        }
                    } label: {
                        HStack {
                            Text("在线人数区域")
                            Spacer()
                            Text(model.onlineCountROI == nil ? "未配置" : "已配置")
                                .foregroundColor(model.onlineCountROI == nil ? .secondary : .green)
                        }
                    }
                    .disabled(model.liveActive)
                    if model.onlineCountROI != nil {
                        Button("停用在线人数采集", role: .destructive) { model.onlineCountROI = nil }
                            .disabled(model.liveActive)
                    }

                    NavigationLink {
                        OptionalROIEditorView(title: "选择平台提醒区域", currentROI: model.platformNoticeROI) {
                            model.platformNoticeROI = $0
                        }
                    } label: {
                        HStack {
                            Text("平台提醒区域")
                            Spacer()
                            Text(model.platformNoticeROI == nil ? "未配置" : "已配置")
                                .foregroundColor(model.platformNoticeROI == nil ? .secondary : .green)
                        }
                    }
                    .disabled(model.liveActive)
                    if model.platformNoticeROI != nil {
                        Button("停用平台提醒采集", role: .destructive) { model.platformNoticeROI = nil }
                            .disabled(model.liveActive)
                    }
                }

                Section(header: Text("悬浮展示（PiP）"),
                        footer: Text(pipFooter)) {
                    if !pip.isSupported {
                        Label("本设备不支持画中画", systemImage: "exclamationmark.triangle")
                            .foregroundColor(.orange)
                    } else {
                        switch pip.state {
                        case .running:
                            Label("PiP 运行中", systemImage: "pip.fill")
                                .foregroundColor(.green)
                        case .starting:
                            Label("正在启动 PiP…", systemImage: "pip")
                                .foregroundColor(.blue)
                        case .suspended:
                            Label("PiP 被系统暂时暂停", systemImage: "pause.circle")
                                .foregroundColor(.orange)
                        case .failed(let reason):
                            Label("PiP 失败：\(reason)", systemImage: "xmark.circle")
                                .foregroundColor(.red)
                                .font(.footnote)
                        default:
                            Label("未启动", systemImage: "pip")
                                .foregroundColor(.secondary)
                        }
                        if let t = pip.lastReplyAt {
                            Text("最近回复：\(t, style: .time)")
                                .font(.footnote)
                                .foregroundColor(.secondary)
                        }
                        let pipIsActive = pip.state == .running || pip.state == .starting || pip.state == .suspended
                        Button(pipIsActive ? "停止悬浮条" : "启动悬浮条（PiP）") {
                            if pipIsActive {
                                pip.stop()
                            } else {
                                pip.start()
                            }
                        }
                    }
                }

                Section(header: Text("采集"),
                        footer: Text(startGuide)) {
                    Toggle("识别开关（扩展按此决定是否处理）", isOn: $model.captureWanted)
                    BroadcastPickerSection(showsMicrophoneButton: model.audioSettings.enabled &&
                        model.audioSettings.includes("audioMic"))
                    HStack {
                        Text("已处理轮次")
                        Spacer()
                        Text("\(model.roundCounter)")
                            .foregroundColor(.secondary)
                        if let t = model.lastUpdated {
                            Text("（\(t, style: .time)）")
                                .foregroundColor(.secondary)
                        }
                    }
                }

                Section(header: Text("语音辅助（待设备验证）"),
                        footer: Text("仅处理系统已经交付的音轨。麦克风由你在系统广播面板手动选择；显示按钮不会自动开启麦克风。先用唯一短词核对音轨和原直播收音；音乐、他人声或来源不明时不要确认主播声。")) {
                    Toggle("允许连续音频分析（默认关闭）", isOn: $model.audioSettings.enabled)
                    if model.audioSettings.enabled {
                        Picker("分析音轨", selection: $model.audioSettings.source) {
                            Text("应用音轨 audioApp").tag("audioApp")
                            Text("麦克风音轨 audioMic").tag("audioMic")
                            Text("两轨分别分析").tag("both")
                        }.disabled(model.liveActive)
                        if model.audioSettings.includes("audioApp") {
                            Toggle("短词核对：应用音轨仅含主播声", isOn: $model.audioSettings.appHostConfirmed)
                        }
                        if model.audioSettings.includes("audioMic") {
                            Toggle("短词核对：麦克风轨仅含主播声", isOn: $model.audioSettings.micHostConfirmed)
                        }
                        Text("未确认时转写仅作参考，不生成语速或停顿结论。")
                            .font(.footnote).foregroundColor(.secondary)
                        Text(model.audioStatus.phase).font(.footnote)
                        Text("应用块 \(model.audioStatus.copiedApp) · 麦克风块 \(model.audioStatus.copiedMic) · 已送包 \(model.audioStatus.uploaded) · 丢弃 \(model.audioStatus.dropped)")
                            .font(.caption).foregroundColor(.secondary)
                        if let issue = model.audioStatus.issue {
                            Text(issue).font(.footnote).foregroundColor(.orange)
                        }
                    }
                }

                Section(header: Text("直播运营"),
                        footer: Text("手动开始/结束本场辅助。评论仅来自 ReplayKit 当前可见 OCR 样本；不代表公屏全量。运营提示使用现有 PiP。")) {
                    TextField("本场补充说明（选填）", text: $model.liveDescription)
                    Button(model.liveActive ? "结束本场辅助" : "开始本场辅助") {
                        if model.liveActive { model.stopLiveSession() }
                        else { model.startLiveSession() }
                    }
                    Text(model.liveStatus)
                        .font(.footnote)
                        .foregroundColor(.secondary)
                    if let summary = model.liveSummary {
                        NavigationLink("本场概览与事件记录") {
                            LiveSessionOverviewView(summary: summary, events: model.liveEvents)
                        }
                    } else {
                        NavigationLink("当前收到的事件记录") {
                            LiveEventListView(events: model.liveEvents)
                        }
                    }
                }

                Section(header: Text("测试工具")) {
                    NavigationLink("输入检查（测试）") {
                        InputDiagnosticsView()
                    }
                }

                Section(header: Text("使用指引")) {
                    Text(guideText)
                        .font(.footnote)
                }
            }
            .navigationTitle("屏识AI助手")
            .alert("应用参考直播区域预设？", isPresented: $confirmsLiveAreaPreset) {
                Button("应用并替换评论、人数、人物区域", role: .destructive) {
                    model.applyLiveAreaPreset()
                }
                Button("取消", role: .cancel) { }
            } message: {
                Text("将设置评论 x=0.03 y=0.69 w=0.82 h=0.25、人数 x=0.80 y=0.055 w=0.10 h=0.045、人物 x=0 y=0.14 w=1 h=0.80。已有这三类自定义框会被替换；平台提醒区域保持原样（默认未配置）。请在主播界面核对后再开始采集。")
            }
        }
    }

    private var roiSummary: String {
        roiSummary(for: model.roi)
    }

    private func roiSummary(for r: CGRect) -> String {
        return String(format: "x=%.2f  y=%.2f  宽=%.2f  高=%.2f", r.minX, r.minY, r.width, r.height)
    }

    private let roiLimitationNote =
        "iOS 公开 API 无法在其他 App 上叠加任意可拖矩形选择。识别区域在此编辑：以竖屏满屏为参照的归一化矩形，广播期间扩展按同一比例裁剪当前屏幕内容。"

    private let pipFooter =
        "主展示路径：在前台启动 PiP，再开广播并切到目标应用；主 App listener 随 PiP 启停，回复通过 App Group UDS 短通道到 PiP。PiP 启动时使用系统要求的 playback 音频会话并混音，不播放静音内容；后台实际存活仍需真机验证，系统仍可能挂起进程或暂时暂停 PiP。关闭 PiP 后扩展无法连接时会有限重试并丢弃回复，不写文件或转通知。广播停止则停止识别。广播采集整个屏幕（含 PiP），请把 PiP 拖离识别区域以降低回声风险；公开 API 无法读取 PiP 位置或保证覆盖区域触摸穿透。"

    private let startGuide =
        "启动步骤：1) 打开识别开关；2) 在前台启动 PiP（建立短回复 listener）；3) 点“开始直播”并在系统弹窗确认；4) 切到目标应用。暂停/继续可切换识别开关或使用系统广播控制；停止广播由系统状态栏操作。关闭 PiP 会停止 listener，通道不可达时回复丢弃。采集授权、状态栏提示与受保护画面限制均由系统承载；广播不能由 App 静默启动。"

    private let guideText =
        "本 App 与“屏识AI广播”扩展通过 App Group 共享配置与控制开关，并通过 UDS 传递短回复；句子和回复正文仅在进程内存及 socket 内核缓冲短暂存在，不写文件或偏好设置。识别与请求在广播扩展进程内完成，画面仅内存处理。"
}

private struct LiveSessionOverviewView: View {
    let summary: LiveSessionSummary
    let events: [LivePrompt]

    var body: some View {
        List {
            Section(header: Text("辅助场次")) {
                dateRow("开始", milliseconds: summary.startedAtMS)
                if let stopped = summary.stoppedAtMS {
                    dateRow("结束", milliseconds: stopped)
                    row("辅助时长", duration(stopped - summary.startedAtMS))
                }
            }
            Section(header: Text("采样在线人数"), footer: Text("仅表示本辅助场次采样到的屏幕读数，不代表抖音完整统计。")) {
                if let peak = summary.onlineCount.peak {
                    row("采样峰值", "\(peak.approximate ? "约" : "")\(number(peak.nominalValue, maximumFractionDigits: 0)) 人")
                    row("峰值屏幕原文", peak.rawText)
                    row("显示单位", peak.displayUnit)
                } else {
                    row("采样峰值", "未知（没有有效读数）")
                }
                if let average = summary.onlineCount.timeWeightedAverage {
                    row("有效时段平均", "\(average.approximate ? "约" : "")\(number(average.nominalValue, maximumFractionDigits: 1)) 人")
                } else {
                    row("有效时段平均", "未知（没有有效连续采样时段）")
                }
                if let recorded = summary.onlineCount.coverage.recordedSamples {
                    let valid = summary.onlineCount.coverage.validValueSamples ?? 0
                    row("有效采样覆盖", "\(valid) / \(recorded) 条；加权时段 \(duration(summary.onlineCount.coverage.coveredDurationMS ?? 0))")
                }
                let coverage = summary.onlineCount.coverage
                if let gaps = coverage.uncoveredIntervals, gaps > 0 {
                    row("采样断档", "\(gaps) 段；最长 \(duration(coverage.longestUncoveredIntervalMS ?? 0))")
                }
                if let before = coverage.uncoveredBeforeFirstValidMS, before > 0 {
                    row("首个有效读数前", "未覆盖 \(duration(before))")
                }
                if let after = coverage.uncoveredAfterLastValidMS, after > 0 {
                    row("末个有效读数后", "未覆盖 \(duration(after))")
                }
                if let issues = coverage.sampleIssues, !issues.isEmpty {
                    let labels = issues.sorted { $0.key < $1.key }.map { "\(issueName($0.key)) \($0.value)" }
                    row("缺失／无效样本", labels.joined(separator: "、"))
                }
            }
            Section(header: Text("事件记录（\(events.count)条）")) {
                if events.isEmpty {
                    Text("本场没有已保存的运营事件")
                        .foregroundColor(.secondary)
                } else {
                    ForEach(events) { event in
                        VStack(alignment: .leading, spacing: 6) {
                            HStack(spacing: 4) {
                                Text(Date(timeIntervalSince1970: Double(event.startAtMS) / 1000), style: .time)
                                if event.endAtMS > event.startAtMS {
                                    Text("–")
                                    Text(Date(timeIntervalSince1970: Double(event.endAtMS) / 1000), style: .time)
                                }
                            }
                            .font(.caption)
                            .foregroundColor(.secondary)
                            Text(event.phenomenon).font(.subheadline)
                            Text(event.suggestion).font(.body)
                        }
                        .padding(.vertical, 4)
                    }
                }
            }
        }
        .navigationTitle("本场概览")
    }

    private func dateRow(_ title: String, milliseconds: Int64) -> some View {
        row(title, Date(timeIntervalSince1970: Double(milliseconds) / 1000).formatted(date: .abbreviated, time: .shortened))
    }

    private func row(_ title: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title)
            Spacer(minLength: 12)
            Text(value).multilineTextAlignment(.trailing).foregroundColor(.secondary)
        }
    }

    private func number(_ value: Double, maximumFractionDigits: Int) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.maximumFractionDigits = maximumFractionDigits
        formatter.minimumFractionDigits = 0
        return formatter.string(from: NSNumber(value: value)) ?? String(value)
    }

    private func duration(_ milliseconds: Int64) -> String {
        let seconds = max(0, milliseconds / 1000)
        let hours = seconds / 3600
        let minutes = (seconds % 3600) / 60
        let remaining = seconds % 60
        if hours > 0 { return "\(hours)小时\(minutes)分" }
        if minutes > 0 { return "\(minutes)分\(remaining)秒" }
        return "\(remaining)秒"
    }

    private func issueName(_ reason: String) -> String {
        switch reason {
        case "source_missing": return "未读到"
        case "count_unreadable": return "数字无法解析"
        case "field_semantics_unconfirmed": return "字段未确认"
        case "late": return "过期"
        case "unreliable", "source_unreliable": return "不可靠"
        default: return reason
        }
    }
}

private struct LiveEventListView: View {
    let events: [LivePrompt]

    var body: some View {
        List(events) { event in
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 4) {
                    Text(Date(timeIntervalSince1970: Double(event.startAtMS) / 1000), style: .time)
                    if event.endAtMS > event.startAtMS {
                        Text("–")
                        Text(Date(timeIntervalSince1970: Double(event.endAtMS) / 1000), style: .time)
                    }
                }
                .font(.caption)
                .foregroundColor(.secondary)
                Text(event.phenomenon).font(.subheadline)
                Text(event.suggestion).font(.body)
            }
            .padding(.vertical, 4)
        }
        .navigationTitle("本场事件记录")
    }
}
