#!/usr/bin/env bash
# ============================================================================
# SAReplyChannel 可复现检查脚本（在 macOS 上运行；Windows 无 Swift 无法执行）
#
# 用法:  bash check_sa_reply_channel.sh
# 需要:  Xcode Command Line Tools（提供 swiftc）
#
# 内容:
#   1) 静态审计：源码不得含文件写入/持久化调用（载荷不落盘的机器可查证据）
#   2) 目录审计：IPC 目录仅一个 Swift 源文件（防误入双 target）
#   3) 编译 + 8 组行为检查（macOS 本机 AF_UNIX，等价 iOS 行为的高参考，
#      但不能替代 iOS 真机冒烟——见同目录 README）
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

echo "== SAReplyChannel 检查 =="

echo "-- 1/3 静态审计: 载荷不落盘 --"
if grep -nE '\.write\(to|FileHandle|mmap|JSONSerialization|Data\(contentsOf|UserDefaults' SAReplyChannel.swift; then
  echo "FAIL: 源码含疑似文件写入/持久化调用"; exit 1
else
  echo "PASS: 无文件写入/持久化调用"
fi

swift_files=$(ls -1 ./*.swift 2>/dev/null | wc -l | tr -d ' ')
if [ "$swift_files" != "1" ]; then
  echo "FAIL: IPC 目录应只含 1 个 Swift 源文件（Shared 会被双 target 自动编译），当前 $swift_files"
  exit 1
fi
echo "PASS: 目录仅含 SAReplyChannel.swift"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cat > "$TMP/main.swift" <<'EOF'
import Foundation

let root = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
    .appendingPathComponent("sa_reply_check_\(UUID().uuidString)", isDirectory: true)
try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)

var failed: [String] = []
func check(_ ok: Bool, _ label: String) {
    print((ok ? "PASS  " : "FAIL  ") + label)
    if !ok { failed.append(label) }
}
func waitUntil(_ seconds: Double, _ cond: () -> Bool) -> Bool {
    let deadline = Date().addingTimeInterval(seconds)
    while Date() < deadline {
        if cond() { return true }
        usleep(20_000)
    }
    return cond()
}
@discardableResult
func takeFirst(_ ch: SAReplyChannel, _ seconds: Double) -> String? {
    var got: String?
    _ = waitUntil(seconds) { got = ch.takeLatestReply(); return got != nil }
    return got
}
func isUnreachable(_ r: SAReplySendFailureReason) -> Bool {
    if case .listenerUnreachable = r { return true }
    return false
}
let socketPath = root.appendingPathComponent("sa.sock").path

// 主App角色 / 扩展角色（测试目录显式给出，等价 App Group 容器）
let app = SAReplyChannel(directory: root, socketFileName: "sa.sock")
let ext = SAReplyChannel(directory: root, socketFileName: "sa.sock")

do { try app.startReceiving(); check(true, "T0 启动监听") }
catch { check(false, "T0 启动监听: \(error)") }

// T1 基本往返
ext.send(reply: "你好，世界 hello")
check(takeFirst(app, 5) == "你好，世界 hello", "T1 基本往返")

// T2 单最新值不堆积：快速连发，最终只应剩最后一条
ext.send(reply: "一"); ext.send(reply: "二"); ext.send(reply: "三")
Thread.sleep(forTimeInterval: 0.8)
check(app.takeLatestReply() == "三", "T2 最新值覆盖")
check(app.takeLatestReply() == nil, "T2 取后清空")

// T3 UTF-8 / emoji 往返
let s3 = "中文🀄 emoji ✓ \u{1F600} 表情"
ext.send(reply: s3)
check(takeFirst(app, 5) == s3, "T3 UTF-8 往返")

// T4 超限与空串：同步置失败状态，不进入接收槽
ext.send(reply: String(repeating: "x", count: 20_000))
check(waitUntil(1) {
    ext.lastSendFailure?.reason == .payloadTooLarge(limit: SAReplyChannel.maxReplyBytes, actual: 20_000)
}, "T4 超限失败状态")
check(app.takeLatestReply() == nil, "T4 超限不进入接收槽")
ext.send(reply: "")
check(waitUntil(1) { ext.lastSendFailure?.reason == .invalidPayload }, "T4 空串失败状态")

// T5 逐字节部分写（验证部分读重组与长度前缀解析）
func sendSlow(_ text: String) {
    let fd = socket(AF_UNIX, SOCK_STREAM, 0)
    precondition(fd >= 0)
    defer { close(fd) }
    let payload = Array(text.utf8)
    var frame: [UInt8] = [
        UInt8((payload.count >> 24) & 0xFF),
        UInt8((payload.count >> 16) & 0xFF),
        UInt8((payload.count >> 8) & 0xFF),
        UInt8(payload.count & 0xFF),
    ]
    frame.append(contentsOf: payload)
    let bytes = Array(socketPath.utf8)
    var addr = sockaddr_un()
    addr.sun_family = sa_family_t(AF_UNIX)
    addr.sun_len = UInt8(2 + bytes.count)
    withUnsafeMutableBytes(of: &addr.sun_path) { $0.copyBytes(from: bytes) }
    let cr = withUnsafePointer(to: &addr) { p in
        p.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
            connect(fd, sa, socklen_t(2 + bytes.count))
        }
    }
    precondition(cr == 0, "T5 connect 失败 errno=\(errno)")
    var idx = 0
    while idx < frame.count {
        let n = frame.withUnsafeBytes { raw -> Int in
            write(fd, raw.baseAddress!.advanced(by: idx), 1)
        }
        precondition(n == 1, "T5 write 失败 errno=\(errno)")
        idx += 1
        usleep(2_000)
    }
}
let s5 = "partial write 逐字节写入验证 payload"
sendSlow(s5)
check(takeFirst(app, 5) == s5, "T5 部分写重组")

// T6 stop 清理端点/内存 + 停机后有限重试丢弃 + 重启恢复
app.stop()
check(!FileManager.default.fileExists(atPath: socketPath), "T6 stop 移除端点文件")
check(!app.isListening, "T6 状态复位")
app.takeLatestReply()
ext.send(reply: "offline")
check(waitUntil(9) { ext.lastSendFailure.map { isUnreachable($0.reason) } ?? false },
      "T6 不可达有限重试后置失败")
do { try app.startReceiving() } catch { check(false, "T6 重启监听: \(error)") }
ext.send(reply: "recovered")
check(takeFirst(app, 5) == "recovered", "T6 重启后恢复")

// T7 已有活跃监听时拒绝第二个绑定（防覆盖竞态）
do {
    let c = SAReplyChannel(directory: root, socketFileName: "sa.sock")
    try c.startReceiving()
    check(false, "T7 应拒绝第二个监听")
    c.stop()
} catch SAReplyChannelError.anotherListenerActive {
    check(true, "T7 已有活跃监听被拒绝")
} catch {
    check(false, "T7 错误类型不符: \(error)")
}

// T8 崩溃残留端点自动清理（bind 后直接 close，不 listen 不 unlink）
app.stop()
do {
    let fd = socket(AF_UNIX, SOCK_STREAM, 0)
    precondition(fd >= 0)
    let bytes = Array(socketPath.utf8)
    var addr = sockaddr_un()
    addr.sun_family = sa_family_t(AF_UNIX)
    addr.sun_len = UInt8(2 + bytes.count)
    withUnsafeMutableBytes(of: &addr.sun_path) { $0.copyBytes(from: bytes) }
    let br = withUnsafePointer(to: &addr) { p in
        p.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
            bind(fd, sa, socklen_t(2 + bytes.count))
        }
    }
    precondition(br == 0)
    close(fd)
}
do { try app.startReceiving(); check(true, "T8 残留端点自动清理") }
catch { check(false, "T8 残留清理失败: \(error)") }
ext.send(reply: "after-stale")
check(takeFirst(app, 5) == "after-stale", "T8 残留清理后可通信")

app.stop()
_ = try? FileManager.default.removeItem(at: root)

if failed.isEmpty {
    print("ALL CHECKS PASSED")
    exit(0)
} else {
    print("\(failed.count) CHECK(S) FAILED")
    exit(1)
}
EOF

echo "-- 2/3 编译 (swiftc) --"
swiftc -O SAReplyChannel.swift "$TMP/main.swift" -o "$TMP/sa_check"

echo "-- 3/3 行为检查 --"
"$TMP/sa_check"
