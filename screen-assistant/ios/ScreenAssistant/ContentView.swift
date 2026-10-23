import SwiftUI
import ReplayKit

/// 系统“开始直播”入口（RPSystemBroadcastPickerView，官方唯一启动路径；
/// 无法程序化启动广播）。preferredExtension 绑定本 App 的广播扩展，
/// 用户点击后在系统弹窗中确认。
struct BroadcastPickerSection: UIViewRepresentable {
    static let extensionBundleID = "com.screenassistant.ios.broadcast"

    func makeUIView(context: Context) -> RPSystemBroadcastPickerView {
        let view = RPSystemBroadcastPickerView()
        view.preferredExtension = Self.extensionBundleID
        view.showsMicrophoneButton = false
        return view
    }

    func updateUIView(_ uiView: RPSystemBroadcastPickerView, context: Context) {}
}

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @ObservedObject private var pip = PiPDisplayController.shared

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
                    BroadcastPickerSection()
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
                    NavigationLink("本场事件记录（\(model.liveEvents.count)）") {
                        LiveEventListView(events: model.liveEvents)
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
        }
    }

    private var roiSummary: String {
        let r = model.roi
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
