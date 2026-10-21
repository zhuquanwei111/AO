# Mac 装机指南（用户本人逐步操作）

适用基线：macOS Sequoia 15.8.1 + Xcode 26.3 + iPhone 11（iOS 26.6.1，当前试验基线）；
通用步骤适用同类组合。按顺序执行，每步给出验证命令与期望结果。
工具行为细节（退出码、失败定位、官方依据）见 [README.md](README.md)，本文不重复。

## 1. 安装 Xcode 26.3（本机 macOS Sequoia 15.8.1）

1. 到 Apple Developer 下载页（developer.apple.com/download/all/，需登录你的 Apple ID）
   搜索 **Xcode 26.3**，下载 `.xip` 并解压安装到「应用程序」。
   （App Store 渠道在本系统上可能只提供更高版本，需 macOS Tahoe 26.2+，见下。）
2. 首次启动：同意许可协议，等待组件安装完成。
3. Xcode → Settings → Components：确认 iOS 平台组件已下载（`build.sh` 会检查
   `iphoneos` SDK，缺失会明确报错）。
4. Xcode → Settings → Locations：Command Line Tools 指向该 Xcode。
5. 终端验证：

```bash
xcodebuild -version    # 期望：Xcode 26.3
```

版本说明（如实）：
- Xcode 26.4–26.6 需要 macOS **Tahoe 26.2+**；本机在 Tahoe 兼容列表，可按需升级，
  **非必须**——Xcode 26.3 为本机对 iOS 26.6.1 装机的当前基线。
- **不推荐 Xcode 16.4 作 iOS 26.6.1 真机调试基线**：其 SDK 仅到 iOS 18.5，
  无法识别 iOS 26.6.1 设备。
- 版本与系统要求以 Apple 官方支持页为准（见文末「官方依据」）。

## 2. 安装 XcodeGen（Homebrew）

```bash
brew install xcodegen
xcodegen --version      # 期望输出版本号
```

未装 Homebrew 先装 Homebrew（官方脚本）；也可跳过本步——`build.sh` 检测不到
XcodeGen 时会打印官方安装步骤后退出（退出码 1）。

## 3. 克隆仓库并切分支

```bash
git clone https://github.com/zhuquanwei111/AO.git   # 私有仓库，使用你自己的 GitHub 凭据
cd AO/screen-assistant/ios
git checkout ao/multi-agent-build-44/root
```

命令与路径支持中文和空格；以下命令都在 `screen-assistant/ios` 目录下执行。

## 4. 工程预检

```bash
python3 Tools/preflight.py
```

期望末行：`预检结果：FAIL=0`。有 FAIL 时按 `[FAIL]` 行修复后重跑。
注意：预检是工程结构静态检查，**不是 Swift 编译**。

## 5. 无签名编译验证（不是可安装 IPA）

```bash
Tools/build.sh
```

期望顺序：`[OK] 环境…` → `[1] xcodegen generate` → `[2] xcodebuild 无签名真实编译`
→ `[OK] 编译成功，产品：…/*.app`。
该产物仅验证真实编译，**不能安装到真机**（无签名 IPA 才能装，本工具不伪造 IPA）。
失败时看 `[FAIL]` 行与输出中的日志文件路径；退出码 0/1/2/3/4/64 的含义与修复方向见
[README.md](README.md)「退出码与失败定位」。

## 6. Xcode 登录 Apple ID，获取 TEAM_ID

1. Xcode → Settings → Accounts → 左下 **+** → 登录你的 Apple ID
   （免费 Personal Team 即可，不需要付费开发者计划）。
2. TEAM_ID：Accounts 页选中该 Apple ID → 右侧 **Membership details** 中的
   Team ID；或第一次签名报错时按报错提示里的 Team 选择。
3. 免费账号限制见第 9 步。

## 7. iPhone 开启开发者模式

iPhone（iOS 26.6.1，路径 iOS 26 与此前一致）：**设置 → 隐私与安全性 → 开发者模式 → 打开 → 重启**。
不开启则无法安装/运行开发者签名的 App。

## 8. 真机签名装机

```bash
xcrun devicectl list devices                          # 连接 iPhone（解锁、点"信任"），记 UDID；也可直接用设备名
Tools/build.sh device --team <TEAM_ID> --device <UDID或设备名>
```

脚本实际执行：环境检查 → `xcodegen generate` → `xcodebuild` 自动签名编译
（`-allowProvisioningUpdates DEVELOPMENT_TEAM=<TEAM_ID>`，需要你的 Apple ID 已在
Xcode 登录；本工具不读取 Apple 凭据）→ `devicectl device install app` 安装。

- 失败时脚本保留完整原始日志（路径在输出中）并按日志关键词给出定位提示；
  签名类失败先核对第 6 步登录与 TEAM_ID。
- App Group / provisioning 相关报错：按 [../README.md](../README.md)
  「Mac 构建、签名与安装」一节注册/启用 App Group 后重试。
- 已用 Xcode 签好、只想安装：加 `--app-path <已签名.app路径>`；多 scheme 时可加
  `--scheme`。详见 [README.md](README.md)「真机签名 + 安装（合法最短入口）」。

## 9. 首次安装后：信任开发者证书

iPhone：**设置 → 通用 → VPN 与设备管理** → 选中你的开发者证书/开发者 App → **信任**。
不信任则 App 启动被系统阻止。

## 10. 免费账号限制（如实）

- 免费 Personal Team：描述文件 **7 天过期**（App 提示不受信任/无法启动时，重跑第 8 步
  重签即可）；最多注册 **3 台设备**；每台设备同时最多 **3 个** 免费签名 App；
  每 7 天最多 **10 个** 新 App ID。超限按提示清理后重试。
- App Group capability 能否随免费 Team 原样签出**以实际签名结果为准**（工程预检不保真，
  本仓库也未实测过该组合）；签名阶段报 App Group / provisioning 错误时按
  [../README.md](../README.md) 处理后重试。

## 11. 常见失败速查

| 现象 | 处理 |
|---|---|
| 退出码 1（环境缺失）/ 2（生成失败）/ 3（编译失败）/ 4（签名或安装失败）/ 64（用法错误） | 按 [README.md](README.md)「退出码与失败定位」对照修复 |
| `devicectl` 不可用（Xcode 15 之前） | 脚本会自动打印 Xcode 界面 Run 路径：勾 Team → 选真机 → Cmd+R |
| scheme 不确定 / 生成失败 | 重跑命令（会自动 `xcodegen generate`），或 `--scheme` 指定 |
| 需要完整错误信息 | 每次运行输出末行「日志：<路径>」（系统临时目录，不进源码） |

## 12. 官方依据

- Xcode 版本与系统要求：https://developer.apple.com/support/xcode/
- Xcode 26.6 release notes：https://developer.apple.com/documentation/xcode-release-notes/xcode-26_6-release-notes
- iOS & iPadOS 26.6 release notes：https://developer.apple.com/documentation/ios-ipados-release-notes/ios-ipados-26_6-release-notes
- 关于开发者模式（Apple 支持）：https://support.apple.com/en-us/122867
- 关于开发者账户：https://developer.apple.com/help/account/basics/about-your-developer-account
