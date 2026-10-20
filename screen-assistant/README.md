# 屏识AI助手 V1.0

Android 客户端按设定间隔采集屏幕区域，进行端上中文 OCR，并把单条文本发送到远端 `/v1/answer`；只把新回复显示在不抢焦点的悬浮条。画面、识别文本和回复不写入本地文件或历史记录。

## 模块与当前集成状态

| 目录 | 内容 | 当前状态 |
|---|---|---|
| `android/` | Java、MediaProjection、ML Kit Chinese OCR、HTTP、悬浮展示 | 38提交已集成；40负责本地集成和设备验证 |
| `server/` | FastAPI、Redis原子去重、天翼模型单轮回答 | 37提交`127ce11`及Redis说明修正`e9034d1`已集成 |
| `ios/` | ReplayKit广播扩展、Vision中文OCR、HTTP、本地通知 | 39提交`c703f2f`已集成；PiP尚未实现，不能视为iOS交付完成 |

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

39当前已集成代码包含ReplayKit上传扩展、ROI编辑、Vision中文OCR、HTTP及本地通知，但未实现回复视频帧、SampleBuffer PiP、跨进程不落盘新回复通道，也未验证主App挂起时持续识别和回复更新。通知降级不满足PiP主体验，且通知正文可能由系统保留。工程需Mac/Xcode生成并签名构建；无Mac/Xcode和真机时构建、广播、真实远端、性能及30分钟稳定性均不能标通过。详见[iOS README](ios/README.md)与主控验收清单。

36号R调研对后续实现的集成约束（详见其报告§6.1/§6.3/§6.5）：

- 主通道优先验证App Group命名空间下的`shm_open`命名共享内存（如`group.xxx/frames`），用Darwin通知只发信号；先在目标真机做短通道冒烟（约30行C/Swift级别）。调研依据为Apple App Groups IPC说明及iPhoneOS `shm_open(2)`无可见文件系统入口说明；当前集成代码尚未实现/验证此通道。
- 若目标机`shm_open`冒烟失败，再评估UDS流式传帧；socket路径长度与App Group容器路径需实测。参考仓库样例无LICENSE，只可参考设计，不复制代码。
- 不采用afm式App Group文件`mmap`环来宣称满足严格“不落盘”：文件覆盖写和结束时unlink不能证明内容从未写入存储。只有用户明确放宽该要求时才重新评估。
- PiP帧展示需按系统版本分支：iOS 15/16使用`AVSampleBufferDisplayLayer.enqueue(_:)`；iOS 17+才可用`sampleBufferRenderer`。支持API不代表当前工程已实现或验证PiP。
- PiP后台存活30分钟、免费个人签名/App Group链路及中文Vision OCR耗时均未实测；逐项留待Mac与目标真机验证，不以参考实现或API可用性代替实测。先验证无文件帧通道，再接OCR和PiP。

## 本次集成验证记录

本地验收以Android 14 `sa34`模拟器为准；行为证据来自ADB UI层级、Activity/Service状态和系统窗口旋转状态。测试选区为左25%、上30%、宽49%、高27%。APK对应源码功能提交`383a3ba`（此后仅有文档提交）。

| 项目 | 结果 |
|---|---|
| Debug构建、安装、启动 | 通过；APK路径见交付记录 |
| MediaProjection授权、前台采集服务、端侧OCR流水线 | 通过；日志显示OCR process succeeded。中文句子准确率未单独验证 |
| ROI内点击与滑动 | 通过；采集时点按Settings的Connected devices成功打开子页，列表手势使搜索结果滚动；CaptureService保持运行 |
| ROI外点击、键盘与输入 | 通过；ROI外Settings搜索框获得焦点，软键盘显示并输入battery，搜索结果更新 |
| 区域确认与取消 | 通过；两种操作后选择层/RegionSelectorService退出，确认后的ROI在应用状态与偏好中可见 |
| 暂停、继续、停止 | 通过；状态依次更新为已暂停、采集中、未运行；停止后CaptureService消失 |
| 屏幕旋转 | 通过安全停止检查；模拟器转为横屏`ROTATION_270`后客户端显示未运行。旋转后的恢复需用户重新启动，未完整验再次授权启动 |
| 系统外部撤销MediaProjection | 未验证；Android 14模拟器通知栏显示应用自有采集通知，没有可用的系统停止投屏控件；应用内停止已覆盖 |
| OCR→真实后端HTTP 200→回复悬浮条 | 待37服务就绪；之前本地服务可接收请求但返回503，不能算通过。当前`19180`与`8000`均未监听 |
| 同句HTTP 204、断网恢复 | 未验证；依赖37服务返回有效模型答复后做最少必要调用 |
| 悬浮回复期间底层点击穿透 | 未验证；正常采集期间选区层已确认退出，尚无真实回复条可测底层交互 |
| 不落盘、厂商真机策略、连续30分钟 | 代码设计声明不落盘；本轮未做文件系统取证、真机或长时间实测 |

本轮已验证ROI内外常规触摸不被采集层拦截；回复悬浮条触摸策略需待真实回复可见后补验。中文OCR文本内容、HTTP200、204与网络恢复不因OCR流水线执行日志而视为通过。
