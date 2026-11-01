import SwiftUI
import ReplayKit

/// 系统“开始直播”入口（RPSystemBroadcastPickerView，官方唯一启动路径；
/// 无法程序化启动广播）。preferredExtension 绑定本 App 的广播扩展，
/// 用户点击后在系统弹窗中确认。
struct BroadcastPickerSection: UIViewRepresentable {
    static let extensionBundleID = "com.screenassistant.ios.broadcast"
    static let buttonSize: CGFloat = 50
    var showsMicrophoneButton = false

    func makeUIView(context: Context) -> RPSystemBroadcastPickerView {
        let view = RPSystemBroadcastPickerView(
            frame: CGRect(x: 0, y: 0, width: Self.buttonSize, height: Self.buttonSize)
        )
        view.tintColor = .label
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

    var body: some View {
        NavigationView {
            Form {
                Section(header: Text("本场直播辅助"),
                        footer: Text("先开始本场辅助，再按步骤启动悬浮窗和系统屏幕广播。屏幕广播需要你在系统弹窗中确认。")) {
                    TextField("抖音号（本场汇总绑定）", text: $model.douyinAccount)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .disabled(model.liveActive || model.liveStarting || model.liveStopping)
                    TextField("本场补充说明（选填）", text: $model.liveDescription)
                        .disabled(model.liveActive || model.liveStarting || model.liveStopping)
                    Button(model.liveStarting ? "正在准备…" : (model.liveStopping ? "正在结束…" : (model.liveActive ? "结束本场辅助" : "开始本场辅助"))) {
                        if model.liveActive { model.stopLiveSession() }
                        else { model.startLiveSession() }
                    }
                    .disabled(model.liveStarting || model.liveStopping || model.isolatedStopInFlight)
                    if model.liveStarting || model.liveStopping {
                        ProgressView(model.liveStarting ? "正在创建后台场次…" : "正在结束本场…")
                    }
                    Text(model.liveStatus)
                        .font(.footnote)
                        .foregroundColor(.secondary)
                    if !model.isolatedStopStatus.isEmpty {
                        Text(model.isolatedStopStatus).font(.footnote).foregroundColor(.secondary)
                    }
                    if model.isolatedStopAvailable {
                        Button("重试结束原场") { model.retryIsolatedStop() }
                            .disabled(model.isolatedStopInFlight || model.liveStopping)
                    }
                    if model.liveVerificationNeedsRetry {
                        Button("重试核验本场") { model.retryLiveVerification() }
                            .disabled(model.liveStarting || model.liveStopping || model.isolatedStopInFlight)
                    }
                    if model.liveTimingWarning {
                    Text("部分提示未能及时显示，实时辅助仍可继续。")
                            .font(.footnote)
                            .foregroundColor(.orange)
                    }
                    if model.livePriorityInvalidItems > 0 || model.liveSourcesInvalidItems > 0 || model.liveEntryInvalidItems > 0 {
                    Text("部分提示信息不完整，可能无法在悬浮窗显示。")
                            .font(.footnote)
                            .foregroundColor(.orange)
                    }
                    Label(model.serviceStatusText, systemImage: "network")
                        .font(.footnote)
                        .foregroundColor(model.serviceStatusText.contains("未收到") ||
                                         model.serviceStatusText.contains("失败") ||
                                         model.serviceStatusText.contains("未成功") ||
                                         model.serviceStatusText.contains("无效") ? .orange : .secondary)
                }

                if model.liveActive {
                    Section(header: Text("本机提示与屏幕广播"),
                            footer: Text("屏幕广播需要在系统弹窗中由你确认。")) {
                        if model.liveConnectionPending {
                            Text("同一场实时服务尚未确认，本机采集暂缓；不会自动重发后台启动或创建新场次。")
                                .font(.footnote)
                                .foregroundColor(.secondary)
                        }
                        Label("步骤 1：启动悬浮提示窗", systemImage: "1.circle")
                        Label(pipStatusText, systemImage: pipIsActive ? "pip.fill" : "pip")
                            .foregroundColor(pipStatusColor)
                        Button(pipIsActive ? "停止悬浮提示窗" : "启动悬浮提示窗") {
                            if pipIsActive { pip.stop() } else { pip.start() }
                        }
                        .disabled(!pip.isSupported)

                        Label("步骤 2：开始系统屏幕广播", systemImage: "2.circle")
                        HStack {
                            Text("点击系统广播按钮并确认")
                            Spacer()
                            BroadcastPickerSection(showsMicrophoneButton: model.audioSettings.enabled &&
                                model.audioSettings.includes("audioMic"))
                                .frame(width: BroadcastPickerSection.buttonSize,
                                       height: BroadcastPickerSection.buttonSize)
                                .disabled(model.liveConnectionPending)
                        }
                        Label(model.broadcastStatusText, systemImage: broadcastStatusIcon)
                            .font(.footnote)
                            .foregroundColor(broadcastStatusColor)
                        Text(model.observationUploadStatus)
                            .font(.footnote)
                            .foregroundColor(.secondary)
                    }

                    Section {
                        NavigationLink("当前收到的运营事件记录") {
                            LiveEventListView(events: model.liveEvents)
                        }
                    }
                }

                if model.liveStopped {
                    Section(header: Text("本场复盘"),
                            footer: Text("结束辅助不会替你关闭 iOS 系统广播；如果系统仍显示正在广播，请从系统广播状态栏停止。")) {
                        NavigationLink("场次概览与事件记录") {
                            LiveSessionOverviewView(model: model)
                        }
                    }
                }

                Section(header: Text("更多功能")) {
                    NavigationLink {
                        AdvancedSettingsView()
                    } label: {
                        Label("采集与服务设置", systemImage: "slider.horizontal.3")
                    }
                }
            }
            .navigationTitle("屏识AI助手")
        }
    }

    private var pipIsActive: Bool {
        pip.state == .running || pip.state == .starting || pip.state == .suspended
    }

    private var pipStatusText: String {
        switch pip.state {
        case .running: return "PiP 已由系统确认运行"
        case .starting: return "正在请求系统启动 PiP"
        case .suspended: return "PiP 被系统暂时暂停"
        case .failed(let reason): return "PiP 启动失败：\(reason)"
        case .unsupported: return "设备不支持 PiP"
        case .stopped: return "PiP 已停止"
        case .idle: return "PiP 未启动"
        }
    }

    private var pipStatusColor: Color {
        switch pip.state {
        case .running: return .green
        case .starting: return .blue
        case .suspended, .failed, .unsupported: return .orange
        case .idle, .stopped: return .secondary
        }
    }

    private var broadcastStatusIcon: String {
        if model.broadcastStatusText.contains("已收到") { return "checkmark.circle" }
        if model.broadcastStatusText.contains("结束") || model.broadcastStatusText.contains("暂停") { return "pause.circle" }
        if model.broadcastStatusText.contains("原因未知") { return "exclamationmark.triangle" }
        return "hourglass"
    }

    private var broadcastStatusColor: Color {
        if model.broadcastStatusText.contains("已收到") { return .green }
        if model.broadcastStatusText.contains("原因未知") { return .orange }
        return .secondary
    }
}

private struct AdvancedSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @ObservedObject private var pip = PiPDisplayController.shared
    @State private var confirmsLiveAreaPreset = false

    var body: some View {
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

                Section(header: Text("识别区域"),
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
                    LiveROIOverview(comment: model.roi, onlineCount: model.onlineCountROI,
                                    platformNotice: model.platformNoticeROI)
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
                    if let roi = model.onlineCountROI {
                        Text("在线人数：\(roiSummary(for: roi))")
                            .font(.footnote)
                            .foregroundColor(.secondary)
                        Toggle("我已确认此区域显示实时在线人数", isOn: $model.onlineCountSemanticsConfirmed)
                            .disabled(model.liveActive)
                        Text("未确认时读数保持待核验；热度、累计观看不等于在线人数。")
                            .font(.footnote)
                            .foregroundColor(.secondary)
                    }
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
                            Text("平台提醒区域（采集暂停）")
                            Spacer()
                            Text(model.platformNoticeROI == nil ? "未配置" : "已配置，采集暂停")
                                .foregroundColor(model.platformNoticeROI == nil ? .secondary : .green)
                        }
                    }
                    .disabled(model.liveActive)
                    if let roi = model.platformNoticeROI {
                        Text("平台提醒：\(roiSummary(for: roi))")
                            .font(.footnote)
                            .foregroundColor(.secondary)
                    }
                    if model.platformNoticeROI != nil {
                        Button("停用平台提醒采集", role: .destructive) { model.platformNoticeROI = nil }
                            .disabled(model.liveActive)
                    }
                }

                Section(header: Text("悬浮展示（PiP）"),
                        footer: Text(pipFooter)) {
                    if model.liveActive {
                        Text("直播辅助期间请回到首页查看 PiP 状态并使用启动控制。")
                            .font(.footnote)
                            .foregroundColor(.secondary)
                    } else if !pip.isSupported {
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
                    if model.liveActive {
                        Text("本场辅助已启用识别；系统广播状态见首页。结束后可在此单独使用旧问答采集。")
                            .font(.footnote)
                            .foregroundColor(.secondary)
                    } else {
                        Toggle("识别开关", isOn: $model.captureWanted)
                        BroadcastPickerSection(showsMicrophoneButton: model.audioSettings.enabled &&
                            model.audioSettings.includes("audioMic"))
                            .frame(width: BroadcastPickerSection.buttonSize,
                                   height: BroadcastPickerSection.buttonSize)
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
                        Text(model.audioStatus.phase == "接收系统PCM；来源需核对"
                             ? "正在接收音频，请确认声音来源" : model.audioStatus.phase).font(.footnote)
                        if model.audioStatus.issue != nil {
                            Text("音频处理暂不可用，请检查广播与音频设置。")
                                .font(.footnote).foregroundColor(.orange)
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
            .navigationTitle("采集与服务设置")
            .navigationBarTitleDisplayMode(.inline)
            .alert("应用参考直播区域预设？", isPresented: $confirmsLiveAreaPreset) {
                Button("应用并替换评论、人数、人物区域", role: .destructive) {
                    model.applyLiveAreaPreset()
                }
                Button("取消", role: .cancel) { }
            } message: {
                Text("将替换已设置的评论、人数和人物区域，平台提醒区域保持不变。请在主播界面核对后再开始采集。")
            }
    }

    private var roiSummary: String {
        roiSummary(for: model.roi)
    }

    private func roiSummary(for _: CGRect) -> String {
        "已设置，请按示意图核对"
    }

    private let roiLimitationNote =
        "以竖屏画面为参照选择需要识别的区域，开始广播后请核对画面位置。"

    private let pipFooter =
        "先启动悬浮窗，再开启系统广播并切到直播界面。请将悬浮窗移出识别区域；关闭悬浮窗后提示可能无法显示。系统可能暂停悬浮窗。"

    private let startGuide =
        "先打开识别开关并启动悬浮窗，再点击系统广播按钮确认。直播辅助请先在首页开始本场。暂停可关闭识别开关，停止广播请使用系统状态栏。"

    private let guideText =
        "请先设置识别区域，开始本场后按首页提示开启悬浮窗和系统广播。完成后结束本场，并在系统状态栏停止广播。"
}

private struct LiveSessionOverviewView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        List {
            if let summary = model.liveSummary {
                Section(header: Text("本场时间")) {
                    dateRow("开始", milliseconds: summary.startedAtMS)
                    if let stopped = summary.stoppedAtMS {
                        dateRow("结束", milliseconds: stopped)
                        row("辅助时长", OperatorPresentation.duration(start: summary.startedAtMS, end: stopped))
                    }
                }
            } else {
                Section(header: Text("本场概览")) {
                    if model.overviewLoading {
                        ProgressView("正在读取本场数据…")
                    } else {
                        Text(model.liveStatus).font(.footnote).foregroundColor(.secondary)
                        Button("重试读取后台汇总") {
                            model.refreshStoppedOverview()
                        }
                    }
                }
            }
            Section(header: Text("建议记录（\(model.liveEvents.count)条）")) {
                if model.liveEvents.isEmpty {
                    Text("暂无建议记录").foregroundColor(.secondary)
                } else {
                    ForEach(model.liveEvents) { event in
                        LiveEventRow(event: event)
                    }
                }
            }
            if let summary = model.liveSummary {
            Section(header: Text("在线人数")) {
                if let peak = summary.onlineCount.peak,
                   let value = OperatorPresentation.people(peak.nominalValue, approximate: peak.approximate) {
                    row("画面参考峰值", value)
                    Text("仅供参考，画面读数可能不完整，不代表平台全场统计。")
                        .font(.footnote).foregroundColor(.secondary)
                } else {
                    Text("暂无可用的在线人数数据").foregroundColor(.secondary)
                }
            }
            if let recap = summary.backstageRecap {
                backstageSection(recap)
            } else {
                Section(header: Text("后台复盘")) {
                    Text("暂无本场后台复盘数据").foregroundColor(.secondary)
                    Text(model.liveStatus).font(.footnote).foregroundColor(.secondary)
                }
            }
            }
        }
        .navigationTitle("本场概览")
        .task {
            model.refreshStoppedOverview()
            for _ in 0..<24 {
                guard !Task.isCancelled else { break }
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                guard !Task.isCancelled else { break }
                model.refreshStoppedOverview()
            }
        }
        .refreshable {
            model.refreshStoppedOverview()
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
    }

    @ViewBuilder
    private func backstageSection(_ recap: LiveBackstageRecap) -> some View {
        Section(header: Text("后台复盘"),
                footer: Text("后台页面读数仅供参考，不代表平台最终结算。")) {
            row("任务状态", statusText(recap))
            if let metrics = recap.metrics {
                metricRow("累计观众", value: metrics.cumulativeAudience, approximate: recap.approximateMetrics.contains("累计观众"))
                metricRow("新增粉丝", value: metrics.newFollowers, approximate: recap.approximateMetrics.contains("新增粉丝"))
                metricRow("评论人数", value: metrics.commentCount, approximate: recap.approximateMetrics.contains("评论人数"))
                metricRow("送礼人数", value: metrics.giftGivers, approximate: recap.approximateMetrics.contains("送礼人数"))
                metricRow("获得音浪", value: metrics.soundEnergy, approximate: recap.approximateMetrics.contains("获得音浪"))
            } else {
                Text("暂无本场后台复盘数据").foregroundColor(.secondary)
            }
        }
    }

    @ViewBuilder
    private func metricRow(_ name: String, value: Int?, approximate: Bool) -> some View {
        if let value {
            row(name, "\(approximate ? "约" : "")\(value)")
        } else {
            row(name, "暂无数据")
        }
    }

    private func statusText(_ recap: LiveBackstageRecap) -> String {
        switch recap.status {
        case "waiting_for_stop":
            if recap.claimedAtMS != nil { return "取数任务已领取，等待本场结果" }
            return "任务已登记，取数状态待确认"
        case "capturing": return "取数任务已领取，等待本场结果"
        case "not_connected": return "后台当前未连接，暂无本场复盘；后续状态以服务端回报为准。此状态不代表实时辅助已停用。"
        case "captured_locally":
            guard let metrics = recap.metrics,
                  metrics.cumulativeAudience != nil,
                  metrics.newFollowers != nil,
                  metrics.commentCount != nil,
                  metrics.giftGivers != nil,
                  metrics.soundEnergy != nil else {
                return "复盘读数待确认"
            }
            return "已取得五项"
        case "partial": return "部分缺失"
        case "needs_input": return "需要处理"
        case "failed": return "失败"
        default: return "任务状态待确认"
        }
    }

    private func dateRow(_ title: String, milliseconds: Int64) -> some View {
        row(title, OperatorPresentation.date(milliseconds))
    }

    private func row(_ title: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title)
            Spacer(minLength: 12)
            Text(value).multilineTextAlignment(.trailing).foregroundColor(.secondary)
        }
    }

}

private struct LiveEventListView: View {
    let events: [LivePrompt]

    var body: some View {
        List(events) { event in
            LiveEventRow(event: event)
        }
        .navigationTitle("本场建议记录")
    }
}

private struct LiveEventRow: View {
    let event: LivePrompt

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(OperatorPresentation.date(event.startAtMS)).font(.caption).foregroundColor(.secondary)
            Text(LivePromptDelivery.promptLabel(event.promptType)).font(.caption)
            Text(event.displayText).font(.body)
            if event.promptType == "comment_reply", let reply = event.replyTo {
                Text("原评论：\(reply.nickname.flatMap { $0.isEmpty ? nil : "\($0)：" } ?? "")\(reply.text)")
                    .font(.footnote).foregroundColor(.secondary)
            }
        }
        .padding(.vertical, 4)
    }
}
