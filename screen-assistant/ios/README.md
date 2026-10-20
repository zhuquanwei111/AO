# 屏识AI助手 iOS 客户端（任务 I）

iOS 15+ 跨应用屏幕中文识别 → 远端短回答 → 通知展示 的可行主链路实现。

- 范围：`screen-assistant/ios/`（本目录）。不含安卓/后端/根文档。
- 采集：ReplayKit **Broadcast Upload Extension**（系统授权、状态栏提示，不规避受保护画面限制）。
- OCR：Vision `VNRecognizeTextRequest`（accurate，zh-Hans/zh-Hant + en-US）。
- 网络：扩展进程内 `POST /v1/answer`（契约见下）。
- 展示：**本地通知**（PRD 允许的合法降级）；PiP 未实现（见“边界与差异”）。

## 目录结构

```
ios/
├── project.yml                        # XcodeGen 工程描述（生成 ScreenAssistant.xcodeproj）
├── Config/                            # 工程配置（不参与编译/打包）
│   ├── App-Info.plist
│   ├── App.entitlements               # App Group
│   ├── Broadcast-Info.plist           # com.apple.broadcast-services-upload
│   └── Broadcast.entitlements
├── ScreenAssistant/                   # 主 App（SwiftUI，iOS 15+）
│   ├── ScreenAssistantApp.swift       # 入口 + 配置模型 + 通知权限请求
│   ├── ContentView.swift              # 设置/启停指引/状态
│   └── ROIEditorView.swift            # 归一化区域编辑（预设 + 拖动/缩放）
├── ScreenAssistantBroadcast/          # 广播上传扩展
│   └── SampleHandler.swift            # 采集→方向转正→ROI内存裁剪→OCR→POST→通知
└── Shared/                            # 两 target 共同编译
    ├── AppGroup.swift                 # App Group 常量 + Darwin 通知
    ├── SharedConfig.swift             # 配置读写（服务地址/间隔/超时/ROI/通知开关）
    ├── ControlState.swift             # 控制开关 + 轮次计数（仅状态，无文本）
    ├── SentenceFormatter.swift        # 基础格式化（空白合并/行拼接）
    └── AnswerEndpoint.swift           # 固定 HTTP 契约客户端
```

## 在 Mac 上构建（需 Xcode 14+）

本工程以 **XcodeGen** 描述（`project.yml`），仓库不含 `.xcodeproj`（生成产物不入库）。

```bash
brew install xcodegen
cd screen-assistant/ios
xcodegen generate      # 生成 ScreenAssistant.xcodeproj
open ScreenAssistant.xcodeproj
```

Xcode 内：

1. 选择 `ScreenAssistant` target → Signing & Capabilities → 勾选你的 Team。
2. **注册 App Group**：Apple Developer → Identifiers → App Groups → 注册
   `group.com.screenassistant.ios`，并把它加入主 App 与扩展两个 App ID 的能力里
   （或在 Xcode 中对两个 target 各添加 "App Groups" capability，值保持一致）。
3. 真机运行（广播扩展无法在模拟器完整验证，需真机）。
4. 首次运行到真机后，按 App 内“使用指引”启动一次广播。

> Bundle ID 若需更换：同步修改 `project.yml` 两处 `PRODUCT_BUNDLE_IDENTIFIER`、
> `Shared/AppGroup.swift` 的 `identifier`、两份 entitlements 中的 group 值，
> 以及 Developer 后台注册。

## 与远端的固定 HTTP 契约

| 项 | 值 |
|---|---|
| 请求 | `POST {serverBaseURL}/v1/answer`，`Content-Type: application/json`，体 `{"sentence": "..."}` |
| 200 | `{"status":"ok","reply":"非空"}` → 弹通知展示 reply |
| 204 | 重复 → **不刷新**（不发新通知） |
| 422 / 503 / 504 / 网络错误/超时 | 本轮静默丢弃，下一轮继续；不弹窗、不重试、不排队 |

服务基地址在主 App 设置（如 `http://192.168.1.10:8000`）。Info.plist 已放开
ATS（`NSAllowsArbitraryLoads`）以允许个人局域网 http 服务；如使用 https 可自行收紧。

## 客户端行为约束（PRD 对齐）

- 画面仅内存处理：`processSampleBuffer` 内完成方向转正、ROI 裁剪与下采样，
  sampleBuffer 引用在方法返回后即失效（官方要求），不落盘任何截图/视频。
- 不持久化句子/回复：App Group 仅存配置键（`config.*`）与控制状态
  （`control.userWantsCapture`、`control.extensionRoundCounter` 计数器），
  无句子、回复或任何历史文本。
- 不去重、不推理、不携带历史：每轮独立发送格式化后的单句。
- 不堆积请求：轮次节流基于“上一轮完成时刻”；请求在途时后续帧直接跳过。
- 空识别跳过；轮次间失败静默。
- 长句保护：超过 2000 字符按无效轮次丢弃（与契约 422 客户端侧一致）。

## 展示方案：通知（降级）与 PiP（未实现差异项）

**通知主链路（已实现）**：扩展在收到 200 回复后，经 `UNUserNotificationCenter`
发本地通知。固定 identifier 使新通知覆盖旧通知，通知中心不堆积回复历史；
可在设置中关闭“通知含正文”（此时通知只提示有新回复）。

⚠️ **通知隐私边界（如实标注）**：
- 通知正文经过 iOS 通知系统，会出现在横幅与通知中心，系统对通知有保留与
  摘要机制；**不宣称满足“零文本系统留存”**。
- 专注模式/勿扰模式下横幅不展示（系统行为）。
- 通知权限未授予时链路静默降级：采集/OCR/网络照常，仅无展示。

**PiP 差异项（未实现，不冒充）**：PRD 的“不抢焦点横向悬浮条”在 iOS 上的合法
路径是 PiP（`AVPictureInPictureController` + `AVSampleBufferDisplayLayer`，
iOS 15+ 可用），但需要：把回复渲染为视频帧、扩展→主 App 的前后台数据通道、
音频会话保活。该链路未实现、未验证，列为后续增量；不阻塞本主链路。
同理，全局悬浮窗、无限后台在 iOS 公开 API 中不存在，不宣称。

## 验收状态（如实）

| 层次 | 状态 |
|---|---|
| 代码完成 | ✅ 主 App + 广播扩展 + 共享层 + 工程配置 |
| 构建通过 | ❌ 未验证——本机为 Windows，无 Xcode。需在 Mac 上 `xcodegen generate` 后构建 |
| 真机行为 | ❌ 未验证（方向映射依据 Telegram/Cap 生产实现；OCR/通知/内存需真机确认） |
| 真实远端链路 | ❌ 未验证（需 server=multi-agent-build-37 起服务后联调） |
| 性能/30 分钟稳定 | ❌ 未测量 |

关键待真机验证点：
1. `RPVideoSampleOrientationKey` 值域在本机型上的实际取值（依据 Telegram-iOS 与
   Cap 生产实现：直接映射 `CGImagePropertyOrientation(rawValue:)`）。
2. 扩展内 accurate OCR 的内存峰值（裁剪后最长边限制 1280px 已做保护）。
3. 广播扩展进程内 `UNUserNotificationCenter` 弹通知是否如官方文档所述可用
   （官方声明支持 app extension；未找到广播扩展先例，需实测）。
4. 中文识别准确率与单轮 OCR 延迟（PRD 目标 ≤500ms，未测量）。

## 集成说明（给安卓 owner / multi-agent-build-38）

- 本目录独立成库，合入方式：`git merge` 本 worker 分支（不跨目录复制源码）。
- 依赖远端契约：`POST /v1/answer`（见上表）+ `GET /healthz`（客户端未使用健康检查，
  联调时可用）。
- 无第三方依赖、无 SPM 包、无私有 API。
- App Group/Bundle ID 若与集成方签名体系冲突，按上文“Bundle ID 更换”说明同步修改。
