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
4. 真机运行（**广播扩展在模拟器不可用，必须真机**——OpenTok/AWS 官方样例一致
   声明，R/36 核实）。
5. 免费个人开发者账号（Personal Team）签名链（R/36 H1，真机待验）：
   两个 App ID + App Groups 能力 + 7 天有效期描述文件（过期需重签）。
6. 首次运行到真机后，按 App 内“使用指引”启动一次广播。

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
商业线索（仅可行性，不证架构）：App Store 上架应用 PiP Screen Translate
（id6756233631）采用同链路组合（广播+端侧 OCR+云端 HTTP+PiP，R/36 核实）。
**afm-screen-translator 定位（R/36 第三轮更正）**：仅 4 commits、要求 iOS 26，
是**架构参考而非成熟产品**；降 target 到 iOS 15 属 API 推断，未整链实测；
App Store 文案不能证明内部架构；性能/内存按系统版本与机型区分验证。
扩展收到 200 回复后，若 `pipActive` 且主 App 心跳新鲜（≤10s），回复经
`ReplyTransport`（可替换接口，默认单槽瞬态邮箱）+ Darwin 信号传给主 App；
主 App `PiPDisplayController` 把文本渲染为 640×360 帧并送入
`AVSampleBufferDisplayLayer` → 系统 PiP 窗口展示。iOS 15 兼容性已核实：
`AVPictureInPictureController.ContentSource(sampleBufferDisplayLayer:playbackDelegate:)`
与 `init(contentSource:)` 均为 iOS 15.0 恰好可用；5 个 playbackDelegate 必需方法
已按 live 语义实现（时间范围无限/暂停仅记状态/跳转无操作）。

- **后台存活机制与授权决策（重要，如实）**：R/36 第三轮口径更正后——
  “PiP 激活 + `UIBackgroundModes=audio` + 活跃 `.playback` 会话 = 主 App
  后台不挂起”是 **afm 参考经验 + 官方 audio 模式语义的推断，非 Apple 明文
  保证**（R/36 明确）；静音循环播放保活被禁止。与用户“禁止无关后台音频
  伪保活”禁令的边界重叠，**已提请主控/用户裁决**。工程实现：
  `PiPDisplayController.usesSilentPlaybackSessionForPiP` 常量门控，
  **默认 false（完全遵守禁令）**；获批后改为 true 即启用（无声、不播放
  任何内容），其余代码不变。30 分钟稳定性属真机项（R/36 H2）。
  Info.plist 的 `UIBackgroundModes: audio` 为 Apple PiP 文档要求的能力声明。
- 关闭 PiP 窗口 = 数秒内主 App 挂起（OCR/HTTP/展示停，如实标注“关窗即暂停”）；
  广播停止 = 识别停止（系统限制）。均已写入界面文案。
- **回声风险（R/36 指出）**：广播采集整个屏幕，含本 App 的 PiP 窗口——PiP
  覆盖识别区域时 OCR 可能读到自己的回复形成回环。公开 API 无法读取 PiP
  窗口位置，几何剔除不可行；缓解 = 界面文案要求用户把 PiP 拖离识别区域，
  真机验收清单含“PiP 位置不遮挡 ROI”检查项。
- PiP 层每 2s 重喂当前帧保持活性（纯渲染）；间隔是否足够需真机调参。
  `AVSampleBufferDisplayLayer.enqueue` 自 iOS 17 标记弃用（R/36 O8），本工程
  目标 iOS 15 使用 enqueue（15/16 唯一路径）；17+ 的
  AVSampleBufferVideoRenderer 迁移待真机验证后处理。
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
1. **通道冒烟（R/36 指定的第一个切片，已随 App 交付）**：shm_open 跨进程
   （扩展→App）、UDS 容器内 socket、shm 进程内——结果决定主通道架构
   （见“真机验证切片 1”节）。
2. Vision accurate OCR 在扩展内的内存峰值是否落入 ~50MB jetsam 硬上限
   （R/36：Apple 论坛 thread/651367；扩展内 Vision 无实测估 30–50MB）。
   若被杀且 shm/UDS 冒烟通过，切换哑扩展+通道架构（OCR 已抽为可迁移组件）。
3. `RPVideoSampleOrientationKey` 值域在本机型上的实际取值（依据 Telegram-iOS 与
   Cap 生产实现：直接映射 `CGImagePropertyOrientation(rawValue:)`）。
4. PiP 三要素组合（R/36 H2，**推断非 Apple 保证**）：PiP 激活 + audio 后台
   能力 + 活跃无声 `.playback` 会话 → 主 App 30 分钟不被系统回收（**该会话
   默认关闭待授权**）；关闭 PiP 窗口后的挂起时序与通知回落（心跳阈值 10s）；
   2s 心跳重喂间隔是否足够。
5. 回声验证：PiP 窗口拖离识别区域后连续识别，确认无“识别到自己回复”回环
   （R/36 指出广播含自家 PiP 窗口；公开 API 无窗口位置，无法几何剔除）。
6. 主 App 挂起后扩展自动回落通知的时序。
7. 中文识别准确率与单轮 OCR 延迟（PRD 目标 ≤500ms，未测量；`.fast` 不支持中文）；
   端到端延迟（采集→OCR→HTTP→展示，PRD 目标展示 ≤200ms）。
8. 扩展进程内 `UNUserNotificationCenter` 弹通知是否如官方文档所述可用。
9. 流程顺序（R/36 参考实现建议）：先开 PiP 再开广播；真机确认该顺序下
   broadcast 起播与 PiP detach 均正常。

## 关键技术决策：OCR 在扩展内还是主 App（R/36 三轮调研后的现状）

R/36 硬数据：广播扩展 jetsam 内存硬上限约 **50MB**（Apple 论坛
thread/651367），切 App 瞬间系统堆积 6-8 个待处理缓冲；扩展内 Vision 无实测
估计 30–50MB 逼近上限。

**当前实现 = OCR 在扩展内**（已交付，风险已量化，作为可用基线）：
每轮仅 1 帧、不保留任何 sampleBuffer 引用、≤1280px 小图单次渲染、逐帧零共享
状态读取。理由：哑扩展方案此前受制于“帧写 mmap 环 = 画面落盘”。

**R/36 第三轮更正后的推荐主通道（待真机冒烟验证后切换）**：
Apple iPhoneOS `shm_open` man 页明文 iOS 实现 "no visible entry in the file
system" = **真无文件后备**（与文件 mmap 环本质不同），且官方 App Groups
entitlement 页列 POSIX 共享内存（命名 `<group id>/<name>`）为认可 IPC。
故推荐架构：哑扩展（节流+降采样）→ shm_open 帧通道 + Darwin 信号 → 主 App
（OCR→HTTP→PiP）。**切换前置条件**：shm 跨进程冒烟通过（见下节切片 1）；
冒烟失败备选 UDS 流式通道（内核中转不落盘）；两通道皆败再评估扩展内 OCR
（当前基线继续可用）。

为此已把 OCR 抽为架构无关组件 `Shared/OCRService.swift`（两头可迁移，
切换时无需重写），`ReplyTransport` 可替换接口同样为通道切换预留。

另：Vision 在 iOS 15 的 `.fast` 档**不支持中文**（仅拉丁），`.accurate` 档支持
zh-Hans/zh-Hant——本工程固定 `.accurate`（R/36 核实，与官方“语言支持随档位/
修订变化”一致）。

## 真机验证切片 1：通道冒烟（R/36 指定，随 App 交付）

App 内“通道冒烟”区提供三个一键测试（真机运行，结果就地显示）：

| 切片 | 验证内容 | 通过判据 |
|---|---|---|
| shm_open 进程内冒烟 | shm_open 在目标机进程内可用（命名 `/<group id>/smoke`、mode 0600 必填） | 写→读回校验一致 |
| UDS 流式通道冒烟 | App Group 容器内 unix socket（短路径 `s.s`）bind/connect/send/recv | 接收校验一致 |
| **shm 跨进程探针**（关键） | 开启“shm 冒烟模式”+ 启动广播后，扩展每轮向 `/<group id>/xproc` 写递增序列号，主 App 每 2s 轮询 | 序列号持续递增 |

冒烟结果决定主通道架构走向；请把三个切片的屏幕结果回传给本 worker 或 36。
注：R/36 提示 SO 11589105 称 shm_open 真机可用，但 2014 年有 ENOENT 前科——
探针切片就是为此准备的实证。

## 集成说明（给安卓 owner / multi-agent-build-38）

- 本目录独立成库，合入方式：`git merge` 本 worker 分支（不跨目录复制源码）。
- 依赖远端契约：`POST /v1/answer`（见上表）+ `GET /healthz`（客户端未使用健康检查，
  联调时可用）。
- 无第三方依赖、无 SPM 包、无私有 API。
- App Group/Bundle ID 若与集成方签名体系冲突，按上文“Bundle ID 更换”说明同步修改。
