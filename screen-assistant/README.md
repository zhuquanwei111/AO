# 屏识AI助手 V1.0

Android 客户端按设定间隔采集屏幕区域，进行端上中文 OCR，并把单条文本发送到远端 `/v1/answer`；只把新回复显示在不抢焦点的悬浮条。画面、识别文本和回复不写入本地文件或历史记录。

## 模块与当前集成状态

| 目录 | 内容 | 当前状态 |
|---|---|---|
| `android/` | Java、MediaProjection、ML Kit Chinese OCR、HTTP、悬浮展示 | 38提交已集成；40负责本地集成和设备验证 |
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

启动应用后填写同一局域网中的服务地址（模拟器访问宿主机用`http://10.0.2.2:8000`）及轮询间隔。首次使用须允许“显示在其他应用上层”、通知（Android 13+）和每次会话的系统屏幕采集授权。选区只在主动编辑时临时出现；完成或取消后移除。正常采集时回复窗口标记`NOT_FOCUSABLE|NOT_TOUCHABLE`，当前模拟器确认采集运行期间没有残留选区层；特定厂商系统与真机触摸穿透仍需目标设备复验。

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

本地验收以Android 14 `sa34`模拟器为准。旧的ROI内外点击/滑动/键盘、选区确认取消、暂停/继续/停止与旋转结果保留；本次修复提交`63c889c`，新APK见交付附件。

| 项目 | 结果 |
|---|---|
| Debug构建、安装、启动 | 通过；新APK `screen-assistant-debug-63c889c.apk`，使用隔离SDK/Gradle构建。 |
| Android真实服务端请求 | 2026-10-21 05:35:20.591Z本轮OCR收到HTTP 200；旧客户端没有记录`X-Request-ID`，无法从访问日志恢复该响应头。服务端200表明回复非空，但当时没有客户端非空回复/悬浮显示证据；未重发真实OCR。 |
| Android响应解析与悬浮分支（本地固定响应，不调用模型） | 本地stub返回200 JSON及request ID；客户端记录`SUCCESS`、回复长度，主线程分发后WindowManager窗口触发attached且shown=true。新悬浮层使用`NOT_FOCUSABLE|NOT_TOUCHABLE|NOT_TOUCH_MODAL`。截图见本机artifact `screen-assistant-local-reply-ui.png`。 |
| 回复窗前台点击穿透 | 窗口触摸/焦点标志已核对，前台Settings仍为topResumed；没有在覆盖窗口范围内另做实体点击，厂商系统与真机仍需复验。 |
| 实际服务端HTTP 200→悬浮回复、同句204、断网恢复 | 未通过；真实200发生在旧版且没有保存客户端request ID或悬浮可见证据；等待45通知现行入口可做一次受控真实验证。 |
| 不落盘、厂商真机策略、连续30分钟 | 代码设计声明不落盘；本轮未做文件系统取证、真机或长时间实测。 |

后端健康检查通过不等同于模型答复打通。模拟器真实服务端OCR只确认一条HTTP 200；本地固定响应仅验证Android解析与WindowManager挂载，不计入真实端到端通过。
