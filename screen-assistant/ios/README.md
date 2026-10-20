# 屏识AI助手 iOS 客户端（任务 I）

iOS 15+ 跨应用屏幕中文识别 → 远端短回答 → 通知展示 的可行主链路实现。

- 范围：`screen-assistant/ios/`（本目录）。不含安卓/后端/根文档。
- 采集：ReplayKit **Broadcast Upload Extension**（系统授权、状态栏提示，不规避受保护画面限制）。
- OCR：Vision `VNRecognizeTextRequest`（accurate，zh-Hans/zh-Hant + en-US）。
- 网络：扩展进程内 `POST /v1/answer`（契约见下）。
- 展示：**PiP 悬浮条为主路径**（iOS 15 API 已核实恰好可用，真机未验；失败自动
  回落通知）；**本地通知为回退**（PRD 允许的合法降级）。

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
│   ├── ContentView.swift              # 设置/启停指引/状态/PiP 控制区
│   ├── ROIEditorView.swift            # 归一化区域编辑（预设 + 拖动/缩放）
│   ├── DarwinObserver.swift           # Darwin 通知观察（主 App 侧）
│   └── PiPDisplayController.swift     # PiP 悬浮条：帧渲染/心跳/播放代理
├── ScreenAssistantBroadcast/          # 广播上传扩展
│   └── SampleHandler.swift            # 采集→方向转正→ROI内存裁剪→OCR→POST→PiP邮箱/通知分流
└── Shared/                            # 两 target 共同编译
    ├── AppGroup.swift                 # App Group 常量 + Darwin 通知
    ├── SharedConfig.swift             # 配置读写（服务地址/间隔/超时/ROI/通知开关）
    ├── ControlState.swift             # 控制开关/轮次计数/PiP 状态与 liveness 心跳
    ├── ReplyTransport.swift           # 可替换 transport 接口 + 瞬态邮箱默认实现
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
2. 在 Apple Developer 注册 App Group（**免费个人开发者账号即可使用 App Groups**，
   R/36 核实）：Developer → Identifiers → App Groups → 注册
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
- 不持久化句子/回复：App Group 仅存配置键（`config.*`）、控制状态
  （`control.userWantsCapture`、`control.extensionRoundCounter` 计数器、
  `control.pipActive` 与 `control.pipHeartbeatAt` 时间戳心跳），
  无句子、回复或任何历史文本。回复在 PiP 路径经瞬态邮箱（读后即删、仅最新一条、
  120s 过期），通知路径不落盘。
- 不去重、不推理、不携带历史：每轮独立发送格式化后的单句。
- 不堆积请求：轮次节流基于“上一轮完成时刻”；请求在途时后续帧直接跳过。
- 空识别跳过；轮次间失败静默。
- 长句保护：超过 2000 字符按无效轮次丢弃（与契约 422 客户端侧一致）。

## 展示方案：PiP 悬浮条（主路径，工程已实现）+ 本地通知（回退）

**PiP 主路径（本轮新增，真机行为未验证）**：
扩展收到 200 回复后，若 `pipActive` 且主 App 心跳新鲜（≤10s），回复经
`ReplyTransport`（可替换接口，默认单槽瞬态邮箱）+ Darwin 信号传给主 App；
主 App `PiPDisplayController` 把文本渲染为 640×360 帧并送入
`AVSampleBufferDisplayLayer` → 系统 PiP 窗口展示。iOS 15 兼容性已核实：
`AVPictureInPictureController.ContentSource(sampleBufferDisplayLayer:playbackDelegate:)`
与 `init(contentSource:)` 均为 iOS 15.0 恰好可用；5 个 playbackDelegate 必需方法
已按 live 语义实现（时间范围无限/暂停仅记状态/跳转无操作）。

- **后台能力声明与伪保活的区分（重要，如实标注）**：按 Apple PiP 文档在
  Info.plist 声明 `UIBackgroundModes: audio`（即 Xcode 中的 "Audio, AirPlay,
  and Picture in Picture" 后台能力——R/36 核实为 PiP 支持的文档化启用方式）；
  但**代码中不激活 AVAudioSession、不播放任何音频**（无伪保活：不用静音音频
  占住后台执行权）。主 App 后台存活依赖 PiP 自身系统生命周期，该行为**需真机
  验证**；关闭 PiP 窗口即停止后台存活、广播停止则识别停止（系统限制，已写入
  界面文案）。若主 App 被挂起，扩展侧 liveness 心跳检测（2s 周期写时间戳，
  >10s 判定超时）自动把展示回落到通知，不阻塞采集/OCR/网络主链路。
  如评审认为 plist 声明本身超出授权，可删除该键：PiP 启动与帧渲染不受影响，
  仅切后台后的存活可能更早失效。
- PiP 层每 2s 重喂当前帧保持活性（纯渲染，无音频）；间隔是否足够需真机调参。
- PiP 窗口为系统窗口：用户可拖动位置；覆盖区域为系统自身交互边界，不承诺
  完全触摸穿透（对齐“采集区域不影响前台操作”验收的 iOS 口径：区域编辑只在
  主 App 预览内完成，跨 App 不留任何编辑层）；建议用户把 PiP 窗口拖离识别
  区域与常用操作区。

**通知回退路径（已实现）**：PiP 未启动、设备不支持 PiP、或主 App 心跳超时
（挂起/被杀）时，扩展在进程内经 `UNUserNotificationCenter` 发本地通知。固定
identifier 使新通知覆盖旧通知，通知中心不堆积回复历史；可在设置中关闭
“通知含正文”。

⚠️ **隐私边界（如实标注）**：
- 通知正文经过 iOS 通知系统（横幅 + 通知中心保留），系统可能保留/摘要通知
  内容——**不宣称满足“零文本系统留存”**。
- PiP 路径的回复传递使用 App Group 容器内的**单槽瞬态邮箱**：覆盖写仅保留
  最新一条（无历史），消费即删除，带 120s 过期防止旧响应回填。iOS 15 公开
  API 不存在严格“零落盘”的扩展→主 App 通道（Darwin 通知无载荷/XPC 不适用/
  shm 沙箱不可用），邮箱文件存在毫秒级落盘窗口；`ReplyTransport` 为可替换
  接口，若专项调研（36）找到无文件 IPC 可直接替换默认实现，两端调用方不变。
- 专注模式/勿扰模式下通知横幅不展示（系统行为）。

## 验收状态（如实）

| 层次 | 状态 |
|---|---|
| 代码完成 | ✅ 主 App + 广播扩展 + 共享层 + PiP 展示链路 + 可替换 transport + 工程配置 |
| 构建通过 | ❌ 未验证——本机为 Windows，无 Xcode。需在 Mac 上 `xcodegen generate` 后构建 |
| 真机行为 | ❌ 未验证（方向映射依据 Telegram/Cap 生产实现；OCR/通知/内存需真机确认） |
| PiP 链路 | ⚠️ 工程已实现（iOS 15 API 已核实恰好可用），**无音频保活下的后台存活、心跳重喂间隔、playbackDelegate 交互均需真机验证**；失败自动回落通知，不阻塞主链路 |
| 真实远端链路 | ❌ 未验证（需 server=multi-agent-build-37 起服务后联调；S 已报真实短答通过一次） |
| 性能/30 分钟稳定 | ❌ 未测量 |
| 采集区域不影响前台操作 | iOS 口径：区域编辑仅在主 App 预览内（跨 App 无编辑层）；PiP 系统窗口可拖动、覆盖处为系统交互边界（不承诺完全穿透）；通知无触摸影响。真机验收按计划矩阵执行（ROI 内外点击/滑动/输入/导航） |

关键待真机验证点：
1. Vision accurate OCR 在扩展内的内存峰值是否落入 ~50MB jetsam 硬上限
   （R/36 调研：Apple 论坛 thread/651367；切 App 瞬间系统堆 6-8 个缓冲）。
   若被杀，回退哑扩展架构（见上“关键技术决策”）。
2. `RPVideoSampleOrientationKey` 值域在本机型上的实际取值（依据 Telegram-iOS 与
   Cap 生产实现：直接映射 `CGImagePropertyOrientation(rawValue:)`）。
3. 无音频会话激活（仅 plist 能力声明）时 PiP 对主 App 的保活行为；
   2s 心跳重喂间隔是否足够；关闭 PiP 窗口后的展示回落时序（心跳超时阈值 10s）。
4. 主 App 挂起后扩展自动回落通知的时序。
5. 中文识别准确率与单轮 OCR 延迟（PRD 目标 ≤500ms，未测量；`.fast` 不支持中文）。
6. 扩展进程内 `UNUserNotificationCenter` 弹通知是否如官方文档所述可用。

## 关键技术决策：OCR 在扩展内还是主 App（R/36 调研后的取舍）

R/36 核实的硬数据：广播扩展 jetsam 内存硬上限约 **50MB**（Apple 论坛
thread/651367），且切 App 瞬间系统会堆积 6-8 个待处理缓冲。参考项目
afm-screen-translator（MIT）采用“哑扩展”架构：扩展只节流+降采样+写 App Group
共享内存环，OCR 放主 App。

**本工程选择 OCR 留在扩展内**，理由（已同步 36）：
1. **画面零落盘**：PRD 要求画面仅内存处理；哑扩展需把帧写入 mmap 环（磁盘介质），
   R/36 自己的深核已确认“不证明画面从未落盘”。本设计帧只在扩展进程内存中存在。
2. **链路不依赖主 App 存活**：主 App 挂起/被杀时（PiP 未开或被系统暂停），
   扩展仍能完成 OCR+POST+通知回退——哑扩展会把整条链路耦合到主 App 生命周期。
3. **内存工作集小**：每轮仅处理 1 帧（间隔节流），不保留任何 sampleBuffer 引用；
   CoreImage 裁剪/降采样为惰性求值，唯一一次渲染发生在 ≤1280px 的小裁剪区上；
   逐帧路径零共享状态读取（纯时间比较），共享配置每轮读一次。
4. Vision 在扩展内的峰值是否落入 50MB 预算**仍是首要真机验证项**；若实测被
   jetsam 杀，回退方案即哑扩展架构（本架构文档已备，届时需接受帧 mmap 的
   落盘边界并重新报批）。

另：Vision 在 iOS 15 的 `.fast` 档**不支持中文**（仅拉丁），`.accurate` 档支持
zh-Hans/zh-Hant——本工程固定 `.accurate`（R/36 核实，与官方“语言支持随档位/
修订变化”一致）。

## 集成说明（给安卓 owner / multi-agent-build-38）

- 本目录独立成库，合入方式：`git merge` 本 worker 分支（不跨目录复制源码）。
- 依赖远端契约：`POST /v1/answer`（见上表）+ `GET /healthz`（客户端未使用健康检查，
  联调时可用）。
- 无第三方依赖、无 SPM 包、无私有 API。
- App Group/Bundle ID 若与集成方签名体系冲突，按上文“Bundle ID 更换”说明同步修改。
