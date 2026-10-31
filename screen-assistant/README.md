# 屏识AI助手 V1.0

Android 客户端采集用户校准的评论和在线人数区域，使用固定版本 Paddle OCR，通过现有单条 observation DTO 上传，并轮询运营提示。画面、识别文本和回复不写入本地文件或历史记录；批次与行序仅在本机内存保留。

## 模块与当前集成状态

| 目录 | 内容 | 当前状态 |
|---|---|---|
| `android/` | Java/Kotlin、MediaProjection、Paddle OCR、HTTP、悬浮展示 | Android 采集与提示候选，设备验证范围见对应交付证据 |
| `server/` | FastAPI、Redis原子去重、天翼模型单轮回答 | 37提交`127ce11`、`e9034d1`及19180专用Compose`85a42df`已集成 |
| `ios/` | ReplayKit广播扩展、Vision中文OCR、HTTP、PiP、通知与回复IPC | `c703f2f`、PiP切片`3499e4d`、UDS无文件模块`978b374`已集成；PiP回复适配和平台实测仍未完成 |

固定契约：请求仅为`{"sentence":"..."}`；200更新回复，204保留旧回复；422/503/504和网络错误丢弃当轮，下一轮继续。服务端不保存句子或回复日志，客户端不做文本去重、排队或历史上下文。

## Android 构建与安装

本地构建环境使用JDK 17、Android SDK 35、项目Gradle Wrapper 8.14.3和AGP 8.13.2；`minSdk 29`，`targetSdk 34`。示例PowerShell：

```powershell
$env:JAVA_HOME = "D:\develop\software\JDK\JDK17"
$env:ANDROID_HOME = "C:\Users\admin\.ao\data\artifacts\multi-agent-build-38\tools\android-sdk"
cd screen-assistant\android
.\gradlew.bat :app:assembleDebug
adb install -r .\app\build\outputs\apk\debug\app-debug.apk
```

启动应用后填写同一局域网中的服务地址（模拟器访问宿主机用`http://10.0.2.2:8000`）及轮询间隔。首次使用须允许“显示在其他应用上层”、通知（Android 13+）和每次会话的系统屏幕采集授权。选区只在主动编辑时临时出现；完成或取消后移除。正常采集时回复窗口可触摸，48dp宽的手柄用于拖动；窗口 flags 为`NOT_FOCUSABLE|FLAG_SECURE|NOT_TOUCH_MODAL`。回复窗口范围内的触摸由窗口接收，不会穿透到下层直播应用；窗口外触摸可按系统规则传递。目标设备触摸行为仍需复验。

新轮仅在 create 响应中 `observation_batch_metadata` 与 `typed_prompts` 均为 JSON 布尔 true 时发送冻结的批次/行序/评论项总数；任一缺失或非法则沿用旧 DTO，恢复旧凭证也回退旧 DTO。此门控取决于实际服务已部署的能力，不能用源码候选代替线上响应。

若开场请求结果未确认，应用保留本机阻断以避免自动重复创建；服务可能已创建旧场，即使网络返回错误或读取超时也不能推定未创建。停止本机采集并核对原服务后，可在连接与识别设置中主动选择“解除本机开场限制”，阅读说明并确认。该操作仅清除本机限制，不会创建新场、不证明服务器未创建，也不会结束可能遗留的旧场；完成后须自行点击开始。已有场次凭证、未确认的停止操作或连接仍在收尾时不可解除，保存失败会继续阻断。

结束辅助会先释放本机投屏与服务，再确认原场停止。仅停止接口 HTTP 200 确认本场结束；超时或其他状态保留原场凭证及待确认标记，禁止新开，只能重试结束原场。结束确认与后台复盘数据就绪分别显示；迟到结果只刷新原场。

悬浮正文只显示有效建议，空闲或到期后收起为48dp手柄，新有效建议再展开。连接、采样上传和复盘诊断留在应用/通知中。普通提示最短阅读窗口采用8秒、本机最多显示12秒；源TTL优先，剩余不足8秒时仅按剩余合法时效显示。延长保护可能使排队提示在显示前过期。等待队列最多16项，排队不重置源TTL，显示前清理过期项，按prompt ID幂等；沿后端既有`priority>=100`紧急契约插队，不从文案或消费等级推断紧急程度。

提示时效要求`/prompts`提供同源整数`server_time_ms`与`server_processing_ms`（契约见[PR11冻结服务端说明](https://github.com/zhuquanwei111/AO/blob/90d0194c099ea5feb352c1ea55b891d89dce2e8f/screen-assistant/server/README.md)）。客户端在真实HTTP边界用包含休眠的`elapsedRealtimeNanos`测完整RTT并向上取整，扣除服务处理时长，再从源剩余时效扣传输预算和1ms余量；排队、8秒保护和12秒上限统一使用`elapsedRealtime`截止点。关屏移除窗口，亮屏先重算时效再附着。旧服务缺字段或公共时效锚非法时拒显整页；公共锚合法时，单条expires缺失、非严格整数、非正值或无法安全形成截止点只跳过该条并诊断，不清空同页合法项，合法已过期项仅跳过且不记非法。合法项截止点、原顺序、ID和优先级不变，沿用既有游标语义。App/通知显示诊断，采集、轮询和停止继续；不把设备墙钟、HTTP Date或首次`returned_at_ms`作为替代锚。该计算依赖响应未缓存及服务时钟未异常跳变。

使用步骤：

1. 点“框选识别区域”，切到目标应用，点击“框选区域”悬浮泡。
2. 拖动创建/移动区域、拖角缩放，点完成保存；取消则不改选区。
3. 点“启动”并同意系统采集授权；识别期间悬浮泡和选区编辑层均退出。
4. 暂停停止轮询但保留授权；继续复用当前投屏会话；停止会释放投屏资源，再启动需重新授权。
5. 会话中旋转屏幕会安全停止，需重新框选并启动。

## 服务端

模拟器联调专用服务使用固定宿主端口19180和Compose内部Redis（不连接宿主6379）：

```powershell
cd screen-assistant\server
$env:TIANYI_API_KEY = "你的天翼凭据"
docker compose -f docker-compose.local.yml up -d --build
Invoke-RestMethod http://127.0.0.1:19180/healthz
docker compose -f docker-compose.local.yml down
```

Android模拟器填写`http://10.0.2.2:19180`。常规本地开发仍可使用下方默认Compose的8000端口：

最快启动（Windows PowerShell，Docker Compose）：

```powershell
cd screen-assistant\server
Copy-Item .env.example .env
notepad .env   # 填 TIANYI_API_KEY=你的天翼凭据并保存
docker compose up -d --build
Invoke-RestMethod http://127.0.0.1:8000/healthz
```

Android 模拟器服务地址填`http://10.0.2.2:8000`；真机填电脑在同一局域网的地址。停止服务运行`docker compose down`。本机共享Redis 6379曾出现MISCONF写入失败；Compose会启动独立Redis，不改共享实例配置。未配置有效天翼凭据或模型调用失败时，`/v1/answer`返回503；健康检查正常不代表模型回答已打通。更多变量及非Docker运行方法见[server/README.md](server/README.md)。

## iOS 状态与缺口

39提交`3499e4d`已加入SampleBuffer PiP主展示切片（iOS 15+ API路径）和可替换`ReplyTransport`；41提交`978b374`新增App Group UDS无文件reply通道（`ios/Shared/IPC/`）。当前PiP代码默认transport仍为单槽邮箱，IPC模块尚未由39接入reply调用链，故“无文件reply→PiP显示”端到端尚未完成。PiP主App后台存活、回复帧更新、iOS 15/17分支、通知降级和零文本留存需Mac/Xcode与目标真机验证；本机Windows不能构建iOS。PiP存活30分钟、免费签名链路、中文Vision OCR耗时均未验证。详见[iOS README](ios/README.md)、[IPC模块说明](ios/Shared/IPC/README.md)与主控验收清单。

36号R调研的集成约束仍适用（详见其报告§6.1/§6.3/§6.5）：

- 首选App Group `shm_open`命名共享内存并用Darwin通知发信号，先在目标真机做短通道冒烟；41提交的UDS目前是可运行候选实现，需确认其路径、权限与生命周期后由39接线。
- 不采用文件`mmap`环宣称满足严格“不落盘”；41的UDS模块新增文件外传输代码，但两端尚未集成和真机验证。
- iOS 15/16用`AVSampleBufferDisplayLayer.enqueue(_:)`；iOS 17+才可用`sampleBufferRenderer`。当前PiP实现切片未构建验证。
- PiP后台存活30分钟、免费签名/App Group链路及中文Vision OCR耗时未实测，不以代码完成或API可用性替代。
## 本次集成验证记录

本地验收以Android 14 `sa34`模拟器为准；已实际验证ROI内外点击/滑动/键盘、选区确认与取消、暂停/继续/停止、旋转安全停止。源码修复提交`63c889c`，记录更新提交`592fc27`。

| 项目 | 结果 |
|---|---|
| Debug构建、安装、启动 | 通过；私网IPv4地址校验已修复，`http://10.0.2.2:19180`通过启动流程并出现MediaProjection授权。新APK `screen-assistant-debug-urlfix.apk`。 |
| MediaProjection、端侧OCR与选区交互 | 通过既有模拟器验收；区域选择只在编辑期间存在，确认/取消后退出。正常采集中回复窗可触摸以支持拖动，flags 为`NOT_FOCUSABLE|FLAG_SECURE|NOT_TOUCH_MODAL`；窗口范围内触摸不穿透。目标设备触摸行为需复验。 |
| Android旧客户端真实请求 | 2026-10-21 05:35:20.591Z收到HTTP 200；旧客户端未记录`X-Request-ID`。200表明服务端生成非空回复，但旧版没有客户端接收/悬浮展示证据；未重复该请求。 |
| 新客户端固定响应→悬浮窗（不调用模型） | 本地stub的HTTP 200 JSON被解析为`SUCCESS`，回复非空；主线程分发后WindowManager attach回调确认`shown=true`。本地UI截图见artifact `screen-assistant-local-reply-ui.png`。 |
| 新客户端真实服务端验证 | 2026-10-21 06:27:18Z HTTP 200，`X-Request-ID=61781edb75b049a4ab8a857f043ac46b`，端侧`SUCCESS`、非空reply长度73；WindowManager日志确认悬浮窗`shown=true`并完成attach。45核对服务端为`outcome=success`、`finish_reason=stop`，模型耗时2906ms/HTTP耗时2926ms，未记录正文。真实悬浮截图见artifact `android-real-reply-overlay.png`。 |
| 重复204、断网恢复 | 同句后续请求收到204去重；一次未及时停止导致相同画面再次得到200（`X-Request-ID=7d8184d665924950a822b3bfae41a591`，服务端确认success/stop、模型耗时2899ms/HTTP耗时2901ms）及随后204。已force-stop采集。断网恢复未验证。 |
| 回复窗触摸与拖动 | 代码启用窗口触摸并给48dp宽拖动手柄处理拖动；窗口范围内触摸由回复窗接收，不穿透到底层应用，窗口外触摸由`NOT_TOUCH_MODAL`允许传递。本轮目标设备触摸行为未复验。 |
| 系统外部撤销MediaProjection | 未验证；已覆盖应用内停止。 |
| 不落盘、厂商策略、连续30分钟 | 代码设计为仅内存处理；未做文件系统取证、真机或长时间实测。 |

服务端单次新句请求已完成HTTP 200到真实悬浮回复显示的模拟器闭环；自动采集在成功后多跑了后续周期，已停止。真实前台触摸穿透、断网恢复、系统外部撤销MediaProjection及真机验证仍未完成。
