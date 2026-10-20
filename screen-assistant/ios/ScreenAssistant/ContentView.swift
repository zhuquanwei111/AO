import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var model: AppModel

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

                Section(header: Text("通知展示"),
                        footer: Text("iOS 无跨应用悬浮窗。回复通过本地通知展示。通知正文会经过系统通知中心（系统可能保留内容），不宣称零文本系统留存。")) {
                    Toggle("通知显示回复正文", isOn: $model.notificationShowsContent)
                }

                Section(header: Text("采集"),
                        footer: Text(startGuide)) {
                    Toggle("识别开关（扩展按此决定是否处理）", isOn: $model.captureWanted)
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

    private let startGuide =
        "启动步骤：1) 打开本 App 的“识别开关”；2) 从控制中心长按屏幕录制按钮（或支持广播的入口），在列表中选择“屏识AI广播”；3) 点“开始直播”，屏幕顶部出现系统广播状态栏即开始跨应用采集；4) 停止：点状态栏或回到本 App 关闭开关后再停止广播。采集授权、状态栏提示与受保护画面限制均由系统承载，本 App 不规避。"

    private let guideText =
        "本 App 与“屏识AI广播”扩展通过 App Group 共享配置（服务地址、间隔、区域、通知设置）与控制开关；不共享、不存储识别句子与回复。识别与请求在系统广播扩展进程内完成，画面仅内存处理，不保存截图、识别内容或回复历史。"
}
