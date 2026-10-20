# 屏识AI助手（screen-assistant）

个人自用：在 Android 上框选一块屏幕区域，按设定间隔对该区域做中文 OCR，把识别到的句子发给远端 `/v1/answer`，回复以不抢焦点的横向悬浮条展示。画面仅在内存处理，不保存任何截图/视频/识别内容/回复。

完整产品要求与固定 HTTP 契约见执行计划（AO 编排产物）：
`POST /v1/answer` 请求体仅 `{"sentence": "..."}`；200=`{"status":"ok","reply":"..."}`，204=重复（客户端保留展示），422=无效输入，503/504=远端不可用；`GET /healthz` 健康。

## 模块归属

| 目录 | 内容 | Owner |
|---|---|---|
| `android/` | Android 客户端（Java，零 androidx，ML Kit 中文 OCR bundled 16.0.1） | 任务A（multi-agent-build-38） |
| `server/` | 远端服务（FastAPI + Redis 去重 + OpenAI 兼容模型） | 任务S（multi-agent-build-37），本地分支合入 |
| `ios/` | iOS 客户端（后续任务，尚未建） | 任务I |

集成方式：各 worker 在自己分支本地提交，安卓 owner 用本地 git 合入，保留原项目文件；不做远端 push。

## Android 客户端

技术要点（依据官方文档逐条核实，详见安卓调研文档）：

- **采集**：`MediaProjection` 整屏采集（系统 API 边界：跨应用不存在“只采集 ROI”，必须整 display 建 `VirtualDisplay` + `ImageReader`，帧在**内存中**按框选区域裁切，处理后即回收，绝不落盘）。
- **Android 14+ 合规**：每次启动会话都重新弹 `createScreenCaptureIntent()` 授权；先 `startForeground(FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)` 再 `getMediaProjection()`；同一 projection 实例只 `createVirtualDisplay()` 一次——**暂停=保留 VirtualDisplay 仅停轮询，继续=复用**；`onStop` 回调里全量回收（含用户从状态栏停止投屏的场景）。
- **OCR**：ML Kit v2 中文（`text-recognition-chinese:16.0.1`，bundled 模型端上推理）。
- **网络**：`HttpURLConnection` + `org.json`，单在途请求，超时连接5s/读取10s；204 保留展示，422/5xx/网络异常本轮静默丢弃；客户端不做文本去重/历史/队列。
- **展示**：横向悬浮条 `TYPE_APPLICATION_OVERLAY` + `FLAG_NOT_FOCUSABLE|FLAG_NOT_TOUCHABLE`（不抢焦点、点透），6 秒自动隐藏；FGS 通知显示运行状态，系统另有 MediaProjection 常驻标识。

### 构建环境（本机实际路径，全部隔离安装，不改系统设置）

- JDK：`D:\develop\software\JDK\JDK17`（17.0.9，已存在）
- Android SDK：`C:\Users\admin\.ao\data\artifacts\multi-agent-build-38\tools\android-sdk`（platform-tools + platforms;android-35 + build-tools;35.0.0，`local.properties` 已指向，git 忽略）
- Gradle：项目内 wrapper 8.14.3 + AGP 8.13.2；compileSdk 35 / targetSdk 34 / minSdk 29

```powershell
$env:JAVA_HOME = "D:\develop\software\JDK\JDK17"
cd screen-assistant\android
.\gradlew.bat :app:assembleDebug
# 产物：app\build\outputs\apk\debug\app-debug.apk
```

### 使用步骤（真机）

1. 安装 debug APK，打开“屏识AI助手”。
2. 填服务地址（如 `http://192.168.1.10:8000`，须与手机同一局域网；模拟器访问宿主机用 `http://10.0.2.2:8000`）与轮询间隔（默认5秒）。
3. 首次点“启动”/“框选识别区域”会要求**悬浮窗权限**（设置→应用→屏识AI助手→显示在其他应用上层）与**通知权限**（Android 13+）。
4. “框选识别区域”→自动退到后台→出现“框选区域”悬浮泡（拖动可移，长按退出）→切到目标应用→点悬浮泡→拖动建框、框内拖动移动、四角缩放→完成。
5. 点“启动”→系统弹屏幕采集授权→允许→自动回到目标应用开始轮询；回复以横向悬浮条出现。
6. 暂停=保留授权停轮询；停止=全部回收，再次启动需重新授权。
7. 屏幕旋转会安全停止会话（Android 14 禁止同实例二次建 VirtualDisplay，V1 不做旋转续采），重新框选+启动即可。

### 已知边界（如实声明，不冒称）

- 暂停期间系统投屏标识仍在（projection 未断开所致，符合“后台显示系统状态标识”）。
- 无新帧的静态画面跳过本轮（内容未变，服务端 24h 去重兜底）。
- 会话中途旋转不续采（设计取舍，见上）。
- 30 分钟稳定性与真机行为未经真实设备验证前不标通过；构建通过≠真机验收。

## 服务端（任务S，待其本地分支合入后此处补运行说明）

等待 multi-agent-build-37 的 `screen-assistant/server/` 提交；契约以上方固定 HTTP 契约为准。
