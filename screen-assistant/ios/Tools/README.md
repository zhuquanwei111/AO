# iOS 构建工具（Tools/）

屏识AI助手 iOS 工程在 Mac 上的**最薄构建/安装入口**：环境检查 → XcodeGen 生成工程 →
一条命令真实编译（默认无签名）→ 需要时最短合法真机签名安装。工程本身与手动流程见
`../README.md`（本目录不改动工程配置）。

## 文件

| 文件 | 用途 |
|---|---|
| `build.sh` | Mac 一键脚本：环境检查 / 生成工程 / 无签名编译 / 真机签名+安装 |
| `preflight.py` | 工程结构预检（Python 标准库，Windows 也能跑）：**静态检查，不是 Swift 编译** |

## 环境要求（Mac）

| 项 | 要求 | 缺失时的处理 |
|---|---|---|
| macOS | 必须（脚本检查 `uname`） | Windows 只能跑 `preflight.py` 预检 |
| 完整 Xcode | 必须（xcodebuild/devicectl 仅随 Xcode 提供，Apple 官方口径） | 脚本给出 `xcode-select` 修复提示 |
| iOS 设备 SDK | 必须（`xcodebuild -showsdks` 检测 `iphoneos*`，用真实安装版本） | Xcode → Settings → Platforms 安装 |
| XcodeGen | 必须 | 脚本给出官方安装步骤（首选 `brew install xcodegen`；备选 Mint / `make install`） |

## 快速开始

```bash
cd screen-assistant/ios
Tools/build.sh            # 环境检查 + xcodegen generate + generic iOS device 无签名真实编译
```

产物：`Release-iphoneos/ScreenAssistant.app`（脚本末尾打印确切路径）。
**这是未签名 .app，仅验证真实编译；不是可安装 IPA，无法装到真机。**
可安装 IPA 需要真实签名——本工具不伪造、不制作假 IPA。

## 真机签名 + 安装（合法最短入口）

前置（均为用户侧操作，本工具不读取 Apple 凭据、不创建/修改证书、不自动购买服务）：
1. Xcode 已登录你的 Apple ID（Settings → Accounts）。
2. App Group 已注册并加入两个 App ID（免费个人账号可用，步骤见 `../README.md`）。
3. 真机已连接并信任。

```bash
Tools/build.sh device --team <TEAM_ID> --device <UDID或设备名>
# 查 UDID：xcrun devicectl list devices
```

流程：`xcodegen generate` → `xcodebuild ... -allowProvisioningUpdates DEVELOPMENT_TEAM=<TEAM>` 自动签名
（免费 Personal Team 签名 7 天有效，过期重跑即可）→ `xcrun devicectl device install app` 安装。
已用 Xcode 签好并想直接安装：`Tools/build.sh device --team <TEAM> --device <UDID> --app-path <已签名.app路径>`。

**devicectl 版本边界**：Apple 官方文档明确 devicectl 仅随完整 Xcode 提供，Xcode 15 起才有。
脚本会实际探测；不可用时打印 Xcode 界面 Run 路径（打开工程 → 勾 Team → 选真机 → Cmd+R）。
设备 iOS 版本支持范围由 Xcode 版本决定，两者不绑定，请以 Xcode 的设备支持页为准，脚本不做假设。

## 退出码与失败定位

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 环境缺失（非 macOS / 无 Xcode / 无 iOS SDK / 无 XcodeGen） |
| 2 | XcodeGen 生成工程失败 |
| 3 | xcodebuild 编译失败 |
| 4 | 真机签名或安装失败 |
| 64 | 用法错误 |

失败时完整原始输出**始终保留**在系统临时目录（路径打印在输出里，形如 `/var/folders/…/build.log`），
不写入源码目录；同时按日志关键词给出定位提示（签名/Team 缺失、destination/SDK 缺失、scheme 问题、
设备未配对、描述文件未受信等）。

## 工程预检（可选，Windows 可跑）

```bash
python3 Tools/preflight.py                # 默认检查上级 ios 目录；路径可含中文/空格
python3 Tools/preflight.py --ios-dir "C:\带 空格\screen-assistant\ios"
```

检查项：project.yml 可解析、target 源码目录存在且含 .swift、INFOPLIST_FILE / entitlements
存在且 XML 可解析、CFBundleIdentifier 与 PRODUCT_BUNDLE_IDENTIFIER 一致、广播扩展
NSExtensionPointIdentifier 正确、扩展 bundle id 以主 App bundle id 为前缀、App Group
在两个 entitlements 与 `Shared/AppGroup.swift` 间一致、部署目标 ≥ iOS 15、无绝对/越界路径。
输出 `OK/INFO/WARN/FAIL` 与汇总；有 FAIL 时退出码 1。

**边界（如实）**：预检是工程结构静态检查，不是 Swift 编译，不能替代 `xcodebuild`
真实构建，更不能当作真机构建或真机行为通过的依据。

## 官方依据（简记）

- Apple《Interacting with devices using the command line》：`devicectl device install app
  --device <uuid|ecid|udid|name> <path>`；devicectl/simctl/xcodebuild 仅随完整 Xcode 提供。
- Apple xcodebuild 命令行（`man xcodebuild` / TN2339）：`-project/-scheme/-destination/
  -list/-showBuildSettings`；`CODE_SIGNING_ALLOWED=NO` 无签名编译；`DEVELOPMENT_TEAM` +
  `-allowProvisioningUpdates` 自动签名。
- XcodeGen 官方 README：`brew install xcodegen`；在含 `project.yml` 目录执行
  `xcodegen generate`，按 spec 的 `name` 生成 `.xcodeproj`。

## 状态（如实标注）

- ✅ **Windows 已实测**：
  - `preflight.py` 对当前工程全部通过（FAIL=0 WARN=0，退出码 0）；对坏配置（缺 name、
    缺 source 目录、带 BOM 的 project.yml）正确给出 FAIL 与退出码 1；中文/空格路径可用。
  - `build.sh` 通过 `bash -n` 语法检查（Git for Windows bash）；非 Mac 路径实测：
    `help`→0、未知命令/缺参数→64、Windows 上 `build`/`check` 命中 uname 检查→1 并给出预检指引。
- ❌ **未在 Mac 实测**：`check` / 默认编译 / `device` 三条路径均未真实执行（本机为
  Windows，无 Xcode）。首次在 Mac 使用请先跑 `Tools/build.sh check` 核对环境。
- 预检通过 ≠ 编译通过；编译通过 ≠ 真机行为验证。真机验收项见 `../README.md`。
