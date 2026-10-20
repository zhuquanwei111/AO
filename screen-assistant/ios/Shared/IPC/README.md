# SAReplyChannel

App Group UDS 通道仅传广播扩展产生的短 `reply`。画面帧、识别句子和回复正文不写入 App Group 文件、UserDefaults 或日志；socket inode 是端点元数据。当前主链路为扩展内 ROI OCR → HTTP → UDS reply → 主 App PiP，不承载画面帧。

```swift
let channel = SAReplyChannel(appGroupIdentifier: AppGroup.identifier)
try channel.startReceiving()        // 主 App 在 PiP 会话期间
channel.send(reply: reply)           // 扩展；异步、有界
let latest = channel.takeLatestReply() // 主 App；非阻塞，取走后清空
channel.stop()                       // 关闭监听、清槽并清理端点
```

`Shared/ReplyTransport.swift` 提供每进程单例 adapter。App 与扩展是独立进程，各持有自己的 channel；主 App 在 PiP 启动时开 listener，并以 100ms 轮询取回复；PiP 关闭或启动失败时 stop。扩展每轮只调用 `send(reply:)`。扩展无 listener 时固定间隔重试最多三次后丢弃；不会写通知或文件。

协议是 4 字节大端长度 + UTF-8 载荷，最大 16,384 字节。处理部分读写、短路径校验、陈旧端点清理、接收端单槽覆盖、停止和重启；sender 使用独立串行队列，采集回调不会等待网络 socket 写入。

## 依据与限制

- Apple App Groups entitlement 文档列出 UNIX domain sockets，并要求 socket 位于 App Group 容器内；36 调研 §6.5–6.9 记录机制依据与 iOS 真机验证缺口。
- 原始 AF_UNIX 使用公开 Darwin/BSD socket API。路径按 `sun_path` 的 103 字节载荷上限检查；iOS 模拟器容器路径可能过长，需在目标 iPhone 验证。
- UDS 沙箱权限、扩展可达性、挂起/恢复和 `stop()` 生命周期没有在本 Windows 环境编译或真机验证。不可把本地静态检查说成 iOS IPC 已通过。
- `check_sa_reply_channel.sh` 是 Mac 上的通道行为检查脚本；运行需 Xcode Command Line Tools：`cd screen-assistant/ios/Shared/IPC && bash check_sa_reply_channel.sh`。此环境无法运行该 Swift 检查。
