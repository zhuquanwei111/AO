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

/// UIKit 视图的 SwiftUI 包装（冒烟切片用）。
struct UIKitHostView<T: UIView>: UIViewControllerRepresentable {
    let title: String
    let make: () -> T

    func makeUIViewController(context: Context) -> UIViewController {
        let vc = UIViewController()
        vc.title = title
        let view = make()
        view.translatesAutoresizingMaskIntoConstraints = false
        vc.view.addSubview(view)
        NSLayoutConstraint.activate([
            view.topAnchor.constraint(equalTo: vc.view.safeAreaLayoutGuide.topAnchor),
            view.leadingAnchor.constraint(equalTo: vc.view.leadingAnchor),
            view.trailingAnchor.constraint(equalTo: vc.view.trailingAnchor),
            view.bottomAnchor.constraint(equalTo: vc.view.bottomAnchor)
        ])
        return vc
    }

    func updateUIViewController(_ uiViewController: UIViewController, context: Context) {}
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

                Section(header: Text("通知回退展示"),
                        footer: Text("通知为回退路径：PiP 未启动或主 App 挂起（心跳超时）时，回复自动改经本地通知展示。通知正文会经过系统通知中心（系统可能保留内容），不宣称零文本系统留存。")) {
                    Toggle("通知显示回复正文", isOn: $model.notificationShowsContent)
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
                        Button(pip.state == .running ? "停止悬浮条" : "启动悬浮条（PiP）") {
                            if pip.state == .running {
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

                Section(header: Text("通道冒烟（真机验证切片 1）"),
                        footer: Text(smokeFooter)) {
                    Toggle("shm 冒烟模式（扩展仅写序列号，暂停识别）", isOn: $model.shmSmokeMode)
                    NavigationLink("shm_open 进程内冒烟") {
                        UIKitHostView(title: "shm_open 冒烟") { ShmSmokeTestView(frame: .zero) }
                    }
                    NavigationLink("UDS 流式通道冒烟") {
                        UIKitHostView(title: "UDS 冒烟") { UDSSmokeTestView(frame: .zero) }
                    }
                    NavigationLink("shm 跨进程探针（扩展→App）") {
                        UIKitHostView(title: "跨进程 shm 探针") { ShmCrossProcessProbeView(frame: .zero) }
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
        "推荐主展示路径：先启动悬浮条（PiP），再开广播，最后切到目标应用——新回复渲染进系统 PiP 窗口。系统限制（如实）：PiP 必须在前台启动；关闭 PiP 窗口即停止本 App 后台存活，展示自动回落通知；广播停止则识别停止。重要：广播采集整个屏幕（含本悬浮窗），请把 PiP 窗口拖离识别区域，否则可能识别到自己的回复。PiP 窗口可拖动，覆盖区域为系统窗口交互边界（不承诺完全触摸穿透）。PiP 后台存活能力默认未启用（涉及无声音频会话授权，见 README 决策记录）；当前后台展示由通知承接。PiP 链路工程已实现，真机行为待验证。"

    private let startGuide =
        "启动步骤：1) 打开“识别开关”；2) 点“启动悬浮条（PiP）”（须在前台，PiP 先于广播启动）；3) 点上方“开始直播”按钮（或从控制中心长按屏幕录制按钮，选择“屏识AI广播”）；4) 在系统弹窗中点“开始直播”，屏幕顶部出现系统广播状态栏即开始跨应用采集；5) 切到目标应用；6) 停止：点状态栏停止广播（广播停止=轮询停止，系统限制），或回本 App 停止 PiP 后停止广播。采集授权、状态栏提示与受保护画面限制均由系统承载，本 App 不规避；广播无法程序化启动或后台静默启动。"

    private let smokeFooter =
        "R/36 指定的第一个真机验证切片。shm_open 冒烟：Apple iPhoneOS man 页明文 iOS 实现'文件系统无可见条目'（真无文件后备），但需真机确认（SO 11589105 称可用，2014 年有 ENOENT 前科）。跨进程探针：开启'shm 冒烟模式'并启动广播后，此页每 2s 读取扩展写入的序列号，验证扩展→App 通道是否成立；结论决定主通道架构（shm/UDS/维持扩展内 OCR）。"

    private let guideText =
        "本 App 与“屏识AI广播”扩展通过 App Group 共享配置（服务地址、间隔、区域、通知设置）与控制开关；不共享、不存储识别句子与回复。识别与请求在系统广播扩展进程内完成，画面仅内存处理，不保存截图、识别内容或回复历史。"
}
