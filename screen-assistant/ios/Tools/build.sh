#!/usr/bin/env bash
# 屏识AI助手 iOS 构建/安装入口（Mac 专用）。本目录：screen-assistant/ios/Tools/
#
# 官方依据（命令来源，简记）：
#   - Apple《Interacting with devices using the command line》：
#       devicectl device install app --device <uuid|ecid|udid|name> <path>
#     devicectl/xcodebuild 仅随完整 Xcode 提供；devicectl 自 Xcode 15 起可用。
#   - Apple xcodebuild 命令行（man xcodebuild / TN2339）：
#       -project/-scheme/-destination/-list/-showBuildSettings；
#       无签名编译 CODE_SIGNING_ALLOWED=NO；自动签名 DEVELOPMENT_TEAM + -allowProvisioningUpdates。
#   - XcodeGen 官方 README：brew install xcodegen；在含 project.yml 的目录执行 xcodegen generate。
#
# 用法：
#   Tools/build.sh
#       环境检查 + xcodegen 生成工程 + generic iOS device 无签名真实编译（默认，Release）
#   Tools/build.sh check
#       仅环境检查（macOS / Xcode / iOS SDK / XcodeGen）
#   Tools/build.sh device --team TEAM_ID --device UDID或设备名 [--app-path 已签名.app路径] [--scheme 名称]
#       合法真机签名编译（自动签名）+ devicectl 安装（需 Xcode 15+；旧版给出 Xcode 界面 Run 路径）
#   Tools/build.sh help
#       帮助
#
# 退出码：0 成功；1 环境缺失；2 工程生成失败；3 编译失败；4 签名/安装失败；64 用法错误
# 边界：不读取 Apple 凭据，不创建/修改证书，不自动购买服务，不伪造 IPA；
#       完整原始输出（含失败）保留在系统临时目录并打印路径，不写入源码目录。
set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IOS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_YML="$IOS_DIR/project.yml"
LOG_DIR="$(mktemp -d)"
LOG_FILE="$LOG_DIR/build.log"
XCODE_VER_LINE=""; XCODE_MAJOR=""; IOS_SDK=""; XCODEGEN_VER=""
APP_TARGET=""; XCODEPROJ=""; SCHEME=""; SCHEME_OPT=""; TARGET_MODE=0; APP_PATH=""

log() { printf '%s\n' "$*" | tee -a "$LOG_FILE" >&2; }

usage() {
  cat <<'EOF'
用法：
  Tools/build.sh                  环境检查 + xcodegen 生成 + generic iOS device 无签名真实编译（默认）
  Tools/build.sh check            仅环境检查（macOS / Xcode / iOS SDK / XcodeGen）
  Tools/build.sh device --team TEAM_ID --device UDID或设备名 [--app-path 已签名.app路径] [--scheme 名称]
                                  真机签名编译（自动签名）+ devicectl 安装（Xcode 15+）
  Tools/build.sh help             本帮助
退出码：0 成功；1 环境缺失；2 生成失败；3 编译失败；4 签名/安装失败；64 用法错误
EOF
}

check_env() {
  local uname_s dev_dir
  uname_s="$(uname -s 2>/dev/null || true)"
  if [ "$uname_s" != "Darwin" ]; then
    log "[FAIL] 本脚本需要在 macOS 上运行（当前 uname=${uname_s:-未知}）。"
    log "  Windows 上可先做工程静态预检：python3 Tools/preflight.py（预检不是编译）"
    return 1
  fi
  if ! command -v xcode-select >/dev/null 2>&1; then
    log "[FAIL] 未找到 xcode-select：请安装 Xcode（App Store）或先执行 xcode-select --install。"
    return 1
  fi
  dev_dir="$(xcode-select -p 2>/dev/null || true)"
  if [ -z "$dev_dir" ]; then
    log "[FAIL] 未设置活动开发者目录（xcode-select -p 失败）。"
    log "  修复：安装完整 Xcode 后执行 sudo xcode-select -s /Applications/Xcode.app"
    return 1
  fi
  XCODE_VER_LINE="$(xcodebuild -version 2>/dev/null | head -n1 || true)"
  case "$XCODE_VER_LINE" in
    Xcode\ *) ;;
    *)
      log "[FAIL] xcodebuild -version 失败（输出：${XCODE_VER_LINE:-空}）。"
      log "  常见原因：仅装 Command Line Tools 未装完整 Xcode；或未同意许可（sudo xcodebuild -license accept）。"
      return 1
      ;;
  esac
  XCODE_MAJOR="$(printf '%s' "$XCODE_VER_LINE" | awk '{print int($2)}')"
  IOS_SDK="$(xcodebuild -showsdks 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i ~ /^iphoneos[0-9]/) {print $i; exit}}')"
  if [ -z "$IOS_SDK" ]; then
    log "[FAIL] 未检测到 iOS 设备 SDK（iphoneos*）。请在 Xcode → Settings → Platforms 安装 iOS 平台组件。"
    return 1
  fi
  if command -v xcodegen >/dev/null 2>&1; then
    XCODEGEN_VER="$(xcodegen --version 2>/dev/null || true)"
  else
    log "[FAIL] 未找到 XcodeGen（xcodegen 命令）。官方安装方式任选其一："
    log "  brew install xcodegen            # Homebrew（推荐）"
    log "  mint install yonaskolb/xcodegen  # Mint"
    log "  或 git clone https://github.com/yonaskolb/XcodeGen.git && cd XcodeGen && make install"
    return 1
  fi
  log "[OK] 环境：macOS；${XCODE_VER_LINE}；SDK ${IOS_SDK}；XcodeGen ${XCODEGEN_VER:-版本未知}"
  return 0
}

find_app_target() {
  APP_TARGET="$(awk '/^name:/{sub(/^name:[ \t]*/,"");gsub(/"/,"");print;exit}' "$PROJECT_YML")"
  [ -n "$APP_TARGET" ] || APP_TARGET="ScreenAssistant"
}

generate_project() {
  log "[1] xcodegen generate（目录：$IOS_DIR）"
  if ! (cd "$IOS_DIR" && xcodegen generate) 2>&1 | tee -a "$LOG_FILE"; then
    log "[FAIL] XcodeGen 生成失败（原始输出：$LOG_FILE）"
    return 1
  fi
  XCODEPROJ="$IOS_DIR/$APP_TARGET.xcodeproj"
  if [ ! -d "$XCODEPROJ" ]; then
    log "[FAIL] 未找到生成产物 $APP_TARGET.xcodeproj（核对 project.yml 的 name）"
    return 1
  fi
  return 0
}

resolve_scheme() {
  local list n
  list="$(xcodebuild -list -project "$XCODEPROJ" 2>/dev/null | awk '/^ *Schemes:/{f=1;next} f{ if ($0 !~ /^ /) exit; gsub(/^[ \t]+/,""); if (length($0)) print }')"
  if [ -n "$SCHEME_OPT" ]; then
    if printf '%s\n' "$list" | grep -Fxq "$SCHEME_OPT"; then
      SCHEME="$SCHEME_OPT"; return 0
    fi
    log "[FAIL] 工程中不存在 scheme：$SCHEME_OPT（可用：$(printf '%s' "$list" | tr '\n' ' ')）"
    return 1
  fi
  n="$(printf '%s' "$list" | grep -c . || true)"
  if [ "$n" -eq 1 ]; then SCHEME="$(printf '%s' "$list")"; return 0; fi
  if [ -n "$list" ] && printf '%s\n' "$list" | grep -Fxq "$APP_TARGET"; then SCHEME="$APP_TARGET"; return 0; fi
  if [ -z "$list" ]; then
    log "[WARN] 生成工程未列出 scheme，改用 legacy 路径：-target $APP_TARGET -sdk iphoneos"
    TARGET_MODE=1
    return 0
  fi
  log "[FAIL] 无法确定 scheme（可用：$(printf '%s' "$list" | tr '\n' ' ')），请用 --scheme 指定"
  return 1
}

dest_args() {
  if [ "$TARGET_MODE" -eq 1 ]; then
    printf '%s' "-target|$APP_TARGET|-sdk|iphoneos"
  else
    printf '%s' "-scheme|$SCHEME|-destination|generic/platform=iOS"
  fi
}

show_product() {
  local sel built app
  sel="$(dest_args)"
  local SEL
  IFS='|' read -r -a SEL <<< "$sel"
  built="$(xcodebuild -project "$XCODEPROJ" "${SEL[@]}" -configuration Release -showBuildSettings 2>/dev/null \
    | awk '/^[ \t]*BUILT_PRODUCTS_DIR = /{sub(/^[ \t]*BUILT_PRODUCTS_DIR = /,"");gsub(/"/,"");print;exit}')"
  app=""
  if [ -n "$built" ]; then
    app="$(ls -d "$built"/*.app 2>/dev/null | head -n1 || true)"
  fi
  if [ -n "$app" ]; then
    log "[OK] 编译成功，产品：$app"
  else
    log "[OK] 编译成功（BUILD SUCCEEDED 见 $LOG_FILE；产品目录自动解析失败，不影响编译结论）"
  fi
  APP_PATH="$app"
}

diag_build() {
  log "[FAIL] xcodebuild 编译失败。完整原始输出已保留：$LOG_FILE"
  if grep -qiE 'requires a development team|DEVELOPMENT_TEAM|No signing certificate|No profiles for|code sign' "$LOG_FILE"; then
    log "  → 疑似签名/Team 缺失："
    log "    真机安装：Tools/build.sh device --team <TEAM_ID> --device <UDID|设备名>"
    log "    （签名依赖你自己的 Apple ID/Team 与钥匙串；本工具不读取凭据、不创建证书）"
    log "    免费个人开发者 Team 可签真机，7 天有效期需重签（见 ../README.md）"
  fi
  if grep -qiE 'Unable to find a destination|available destinations' "$LOG_FILE"; then
    log "  → 疑似 destination/SDK 问题：确认 iOS 平台组件已安装（Xcode → Settings → Platforms）"
  fi
  if grep -qiE 'does not contain a scheme|does not contain a target' "$LOG_FILE"; then
    log "  → 疑似 scheme/target 问题：重跑本脚本（自动 xcodegen generate），或用 --scheme 指定"
  fi
  return 0
}

build_unsigned() {
  local sel
  sel="$(dest_args)"
  local SEL
  IFS='|' read -r -a SEL <<< "$sel"
  log "[2] xcodebuild 无签名真实编译（generic iOS device，Release）"
  log "    注意：产物为未签名 .app，仅验证真实编译；不是可安装 IPA，无法安装到真机。"
  log "    可安装 IPA 需要真实签名（见 Tools/build.sh device 或 ../README.md）。"
  if ! xcodebuild -project "$XCODEPROJ" "${SEL[@]}" -configuration Release \
      CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY= \
      build 2>&1 | tee -a "$LOG_FILE"; then
    diag_build
    return 1
  fi
  show_product
  return 0
}

device_install() {
  local team="$1" device="$2" app_override="$3"
  if ! xcrun devicectl help >/dev/null 2>&1; then
    log "[FAIL] 当前 Xcode（${XCODE_VER_LINE:-未知版本}）未提供 devicectl（Apple：Xcode 15 起才有该工具）。"
    log "  兼容路径（Xcode 界面 Run，受支持版本均可用）："
    log "    1) open \"$XCODEPROJ\""
    log "    2) 选中 ScreenAssistant target → Signing & Capabilities → 勾选你的 Team"
    log "    3) 顶部设备选择器选择真机（设备 iOS 版本需在此 Xcode 支持范围内；设备系统版本与 Xcode 版本不绑定，自行核对）"
    log "    4) Cmd+R：Xcode 自动签名并安装"
    return 1
  fi
  local app="$app_override"
  if [ -z "$app" ]; then
    log "[2] xcodebuild 真机签名编译（自动签名，Team=$team）"
    local sel
    sel="$(dest_args)"
    local SEL
    IFS='|' read -r -a SEL <<< "$sel"
    if ! xcodebuild -project "$XCODEPROJ" "${SEL[@]}" -configuration Release \
        -allowProvisioningUpdates DEVELOPMENT_TEAM="$team" CODE_SIGN_STYLE=Automatic \
        build 2>&1 | tee -a "$LOG_FILE"; then
      diag_build
      return 1
    fi
    show_product
    app="$APP_PATH"
  fi
  if [ -z "$app" ] || [ ! -e "$app" ]; then
    log "[FAIL] 未找到可安装的 .app（用 --app-path 指定，或先完成签名编译）。日志：$LOG_FILE"
    return 1
  fi
  log "[3] devicectl 安装到设备（$device）"
  if ! xcrun devicectl device install app --device "$device" "$app" 2>&1 | tee -a "$LOG_FILE"; then
    log "[FAIL] 安装失败。完整原始输出：$LOG_FILE"
    log "  常见原因：设备未连接/未信任/未配对（xcrun devicectl list devices 核对）；"
    log "  描述文件未受信（设备 设置 → 通用 → VPN 与设备管理）；设备 iOS 版本超出此 Xcode 支持范围。"
    return 1
  fi
  log "[OK] 已安装到设备。启动：xcrun devicectl device process launch --device \"$device\" <bundle-id>"
  return 0
}

main() {
  local cmd="${1:-build}"
  case "$cmd" in
    -h|--help|help)
      usage; exit 0 ;;
    check)
      check_env || exit 1
      find_app_target
      log "工程目标（来自 project.yml）：$APP_TARGET"
      exit 0 ;;
    device)
      shift
      local team="" device="" app_path=""
      while [ $# -gt 0 ]; do
        case "$1" in
          --team)     [ $# -ge 2 ] || { log "缺少 --team 的值"; usage; exit 64; }; team="$2"; shift 2 ;;
          --device)   [ $# -ge 2 ] || { log "缺少 --device 的值"; usage; exit 64; }; device="$2"; shift 2 ;;
          --app-path) [ $# -ge 2 ] || { log "缺少 --app-path 的值"; usage; exit 64; }; app_path="$2"; shift 2 ;;
          --scheme)   [ $# -ge 2 ] || { log "缺少 --scheme 的值"; usage; exit 64; }; SCHEME_OPT="$2"; shift 2 ;;
          -h|--help)  usage; exit 0 ;;
          *)          log "未知参数：$1"; usage; exit 64 ;;
        esac
      done
      [ -n "$team" ]   || { log "device 模式需要 --team <TEAM_ID>"; usage; exit 64; }
      [ -n "$device" ] || { log "device 模式需要 --device <UDID|设备名>"; usage; exit 64; }
      [ -f "$PROJECT_YML" ] || { log "[FAIL] 未找到 $PROJECT_YML"; exit 2; }
      check_env || exit 1
      find_app_target
      generate_project || exit 2
      resolve_scheme || exit 3
      device_install "$team" "$device" "$app_path" || exit 4
      log "日志：$LOG_FILE"
      exit 0 ;;
    build)
      shift
      while [ $# -gt 0 ]; do
        case "$1" in
          --scheme)   [ $# -ge 2 ] || { log "缺少 --scheme 的值"; usage; exit 64; }; SCHEME_OPT="$2"; shift 2 ;;
          -h|--help)  usage; exit 0 ;;
          *)          log "未知参数：$1"; usage; exit 64 ;;
        esac
      done
      [ -f "$PROJECT_YML" ] || { log "[FAIL] 未找到 $PROJECT_YML"; exit 2; }
      check_env || exit 1
      find_app_target
      log "工程目标（来自 project.yml）：$APP_TARGET"
      generate_project || exit 2
      resolve_scheme || exit 3
      build_unsigned || exit 3
      log "日志：$LOG_FILE"
      exit 0 ;;
    *)
      log "未知命令：$cmd"; usage; exit 64 ;;
  esac
}

main "$@"
