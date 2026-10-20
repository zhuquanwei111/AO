# 屏识AI助手 iOS

最低部署版本 iOS 15。工程由 XcodeGen 生成，包含主 App 和 ReplayKit Broadcast Upload Extension。App Group、Bundle ID、扩展嵌入、Info.plist 与 entitlement 配置位于 `project.yml` 和 `Config/`。

## 当前主链路

1. 主 App 设置服务地址、识别间隔和归一化 ROI；ROI 只在主 App 内编辑。
2. 用户先在前台启动 PiP，再通过系统广播选择器启动屏幕广播并授予系统权限。
3. ReplayKit 扩展逐帧节流，在内存中转正、ROI 裁剪、降采样到最长边 640，再用 Vision 简体/繁体中文 OCR。不会缓存帧或文字历史；同一轮完成前丢弃新帧。
4. 扩展按固定契约 POST `/v1/answer`，仅 `200` 的非空回复发送到 App Group 容器内的 Unix domain socket。帧格式为长度前缀加 UTF-8 载荷；载荷先带数字 capture generation，再带 reply。主 App 只接受与当前代次一致且识别仍开启的 reply，因此暂停、停止或重启前进入有限重试队列的旧回复会被丢弃。句子和回复不写文件、UserDefaults 或日志。
5. 主 App 在 PiP 会话期间启动一个 `SAReplyChannel` listener，以 100ms 非阻塞轮询取最新回复并渲染到 sample-buffer PiP；停止/关闭 PiP 时关闭 listener、清理 socket 端点、内存槽和音频会话。

`Shared/IPC/SAReplyChannel.swift` 使用系统 AF_UNIX socket API；App 与扩展各自进程内通过 `ReplyTransportFactory` 持有单例。发送异步执行，单槽覆盖、最多三次有限重试；listener 不可达时丢弃回复。socket 文件仅是 App Group 内端点元数据，正文只在进程内存和内核 socket 缓冲中短暂存在。该实现未在本机 Windows 上编译或真机验证。

## 系统行为与边界

- PiP 启动时设置 `AVAudioSession` `.playback` / `.moviePlayback` / `.mixWithOthers`，并激活会话；关闭时停用。主 App `Info.plist` 声明 `UIBackgroundModes=audio`。这使用 PiP 正常配置，不播放静音内容、不循环音频，也不运行伪造的后台任务。
- PiP 及系统广播选择器必须由用户在前台操作。用户暂停/继续可使用 App 识别开关或系统广播控制；广播暂停/恢复会丢弃旧轮回调。关闭 PiP 会关闭 reply listener 并关闭识别开关；系统广播本身仍需从系统控件停止。
- App 后台存活、扩展和 PiP 在锁屏/系统中断时的持续行为仍需真机验证。声明 audio 后台模式并不保证进程永不挂起。
- PiP 位置没有可供 App 查询的公开 API；PiP 覆盖处不保证触摸穿透。请将 PiP 拖离 ROI，降低屏幕 OCR 识别自身回复的回声风险。
- ROI 在主 App 内编辑，不在其他 App 上留下选择层。App 不承诺全局任意悬浮窗或完全无交互边界。
- App 与 Broadcast Extension 的 `Info.plist` 都声明 `NSLocalNetworkUsageDescription` 和 `NSAllowsLocalNetworking`，供扩展进程连接本地开发服务。iPhone 必须能访问用户配置的服务地址；本机 loopback 地址不适用于实体 iPhone。生产部署应使用 HTTPS；不要将未鉴权开发服务直接暴露到公网。
- `SmokeChannels/` 与 `Shared/IPC/check_sa_reply_channel.sh` 是诊断工具，不参与产品帧/回复链路。当前实现固定为扩展内 OCR、UDS 只传 reply；不依据未执行的 shm 探针重写帧传输架构。

## Mac 构建、签名与安装

需要安装 Xcode（含 iOS SDK）和 XcodeGen。仓库目录为 `screen-assistant/ios/`。

```sh
brew install xcodegen
cd screen-assistant/ios
xcodegen generate
open ScreenAssistant.xcodeproj
```

在 Xcode 的两个 target（`ScreenAssistant` 和 `ScreenAssistantBroadcast`）中选择同一开发团队，并为 Apple Developer 账号注册/启用 App Group `group.com.screenassistant.ios`。签名配置需同时包含：

- 主 App Bundle ID：`com.screenassistant.ios`
- 扩展 Bundle ID：`com.screenassistant.ios.broadcast`
- 两个 target 的 App Group entitlement：`group.com.screenassistant.ios`

选择已连接 iPhone 和 `ScreenAssistant` scheme，运行并按系统提示授权屏幕广播。广播启动由用户通过系统选择器确认，不能由 App 静默启动。也可先做无签名构建检查：

```sh
xcodebuild -project ScreenAssistant.xcodeproj \
  -scheme ScreenAssistant \
  -destination 'generic/platform=iOS' \
  CODE_SIGNING_ALLOWED=NO build
```

生成的无签名产物不能安装到设备；设备安装须由 Xcode 使用匹配的签名证书和 provisioning profile 完成。本交付环境为 Windows，没有 Xcode、Apple 签名身份或 iPhone，因此未生成 IPA。

## 逐项交付与验证矩阵

| 项目 | 当前状态 | 证据 / 还需验证 |
|---|---|---|
| iOS 15 XcodeGen 工程、双 target、扩展嵌入、Bundle ID 与 App Group | 已实现；静态核对 | `project.yml`、`Config/*.plist`、`Config/*.entitlements`；需 Mac 生成工程并构建 |
| 中文设置、服务地址、识别间隔、ROI 编辑、识别开关 | 已实现；静态核对 | `ScreenAssistant/ContentView.swift`、`ROIEditorView.swift`、`Shared/SharedConfig.swift` |
| ReplayKit 跨 App 采集、单轮忙时丢帧、ROI、方向处理、640 长边限制 | 已实现；静态核对 | `ScreenAssistantBroadcast/SampleHandler.swift`；扩展内存上限未实测，不宣称 50MB 通过 |
| Vision 中文 OCR、HTTP 契约、网络失败跳过、下一轮续跑 | 已实现；静态核对 | `Shared/OCRService.swift`、`AnswerEndpoint.swift`；真实后端链路本次未重复请求 |
| UDS 短 reply、无正文文件邮箱、非阻塞发送/消费、listener 生命周期 | 已接线；静态核对 | `Shared/ReplyTransport.swift`、`Shared/IPC/SAReplyChannel.swift`；数字代次过滤旧重试；需在 iOS 15+ 真机验证 App Group socket 路径与收发 |
| PiP sample-buffer、iOS 15 playback delegate、PiP audio 会话启动/回收 | 已实现；静态核对 | `ScreenAssistant/PiPDisplayController.swift`、`Config/App-Info.plist`；尚未由 Xcode 编译或真机运行 |
| 暂停/继续/停止、PiP 关闭、迟到 OCR/HTTP 回调 | 已实现；未运行验证 | 广播与识别开关代次使旧回调丢弃；请求在下一帧观察到暂停后取消 |
| 网络错误后续轮、204 保留显示、回复到 PiP | 契约代码已实现；端到端未实测 | 需设备连接可达的后端并验证 200/204/错误路径 |
| PiP 后台持续更新、锁屏/中断、触摸影响、回声、30 分钟稳定性 | 待真机 | iOS 无公开 PiP 几何位置；需按产品验收清单在目标设备记录 |
| OCR ≤500ms、回复到展示 ≤200ms | 待测 | 本机无 Xcode/设备；不得以 Python/服务端契约结果代替 Swift 测量 |

## 验收清单中的真机步骤

1. 在 iOS 15+ iPhone 安装签名版，确认两个 target entitlement 和 App Group 容器一致。
2. PiP 启动后开始广播，从目标 App 识别一条句子，检查 `200 → UDS → PiP`；重复请求检查 `204` 不刷新。
3. 关闭 PiP、暂停/继续广播、停止广播、快速切换识别开关，确认停止后的迟到响应不覆盖当前内容，下一轮能恢复。
4. 断网再恢复；检查单轮失败被丢弃且后续轮继续。分别记录 OCR 耗时与 reply 到 PiP 展示耗时。
5. 在 ROI 内外点击、连续滑动、输入文本、切 App；确认除 PiP 自身系统窗口覆盖区域外交互不受区域编辑层干扰。
6. 连续运行 30 分钟并记录设备、iOS 版本、扩展峰值 footprint、可用内存、广播/PiP 生命周期和回声情况。

本机可交付结论：源码、工程配置与静态接线检查已完成；本地 Swift/Xcode 构建、iOS 真机 UDS、PiP/ReplayKit 生命周期、性能和 30 分钟稳定性均未验证。没有把缺失的设备证据标成通过。
