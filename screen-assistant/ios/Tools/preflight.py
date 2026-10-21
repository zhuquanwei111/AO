#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""屏识AI助手 iOS 工程预检（Python 标准库实现）。

做什么：静态检查 XcodeGen project.yml 及其引用是否结构完整、引用一致——
target 源码路径、App Group（entitlements 与 Shared/AppGroup.swift 常量对照）、
bundle 引用（bundle id 格式与扩展前缀关系）、plist 位置与文件存在等。

不做什么：这是工程结构预检，不是 Swift 编译；不能替代 xcodebuild 真实构建，
更不能当作真机构建/真机行为通过的依据。真实编译请用同目录 build.sh（需 Mac）。

用法：
    python3 Tools/preflight.py [--ios-dir <screen-assistant/ios 路径>]
    --ios-dir 缺省为本脚本上一级目录（即 ios/）；路径可含中文与空格。

退出码：0 = 无 FAIL（可能有 WARN）；1 = 存在 FAIL（工程无法可靠生成/构建）。
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]*$")
GROUP_RE = re.compile(r'"(group\.[A-Za-z0-9.\-]+)"')

RESULTS = []


def ok(msg):
    RESULTS.append(("OK", msg))


def info(msg):
    RESULTS.append(("INFO", msg))


def warn(msg):
    RESULTS.append(("WARN", msg))


def fail(msg):
    RESULTS.append(("FAIL", msg))


def _strip_comment(line):
    """仅剥离 " #" 起始的行内注释（保守；本工程配置值不含该序列）。"""
    idx = line.find(" #")
    return line[:idx] if idx >= 0 else line


def _scalar(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def parse_simple_yaml(text):
    """极简 YAML 子集解析：缩进映射/列表与标量，仅适用于 project.yml 这类配置。

    不支持锚点、多文档、行内嵌套 flow（{}/[]）；结构不符抛 ValueError。
    """
    root = {}
    # 栈元素: [indent, container, parent_container, key_in_parent]
    stack = [[-1, root, None, None]]
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        line = _strip_comment(raw.rstrip())
        indent = len(line) - len(line.lstrip(" "))
        line = line.strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        top = stack[-1]
        container, parent, key = top[1], top[2], top[3]
        if line.startswith("- "):
            item = line[2:].strip()
            if not isinstance(container, list):
                if parent is None or key is None:
                    raise ValueError("根级列表不支持: %s" % line)
                new_list = []
                parent[key] = new_list
                top[1] = new_list
                container = new_list
            if ":" in item and not item.startswith(("'", '"')):
                k, _, v = item.partition(":")
                sub = {_scalar(k): _scalar(v)}
                container.append(sub)
                stack.append([indent, sub, container, len(container) - 1])
            else:
                container.append(_scalar(item))
        elif line.endswith(":"):
            k = _scalar(line[:-1])
            if isinstance(container, dict):
                child = {}
                container[k] = child
                stack.append([indent, child, container, k])
            else:
                raise ValueError("映射键出现在列表上下文: %s" % line)
        else:
            k, _, v = line.partition(":")
            if v == "":
                raise ValueError("无法解析行: %s" % line)
            if isinstance(container, dict):
                container[_scalar(k)] = _scalar(v)
            else:
                raise ValueError("标量出现在列表上下文: %s" % line)
    return root


def parse_plist_dict(path):
    """解析 plist XML 顶层 dict，返回 dict Element；解析失败抛异常。"""
    d = ET.parse(str(path)).getroot().find("dict")
    if d is None:
        raise ValueError("plist 顶层不是 dict")
    return d


def dict_child(d, key):
    children = list(d) if d is not None else []
    i = 0
    while i < len(children):
        el = children[i]
        if el.tag == "key" and (el.text or "").strip() == key and i + 1 < len(children):
            return children[i + 1]
        i += 1
    return None


def dict_value_text(d, key):
    el = dict_child(d, key)
    return (el.text or "").strip() if el is not None else ""


def plist_string_array(el):
    if el is None or el.tag != "array":
        return []
    return [(c.text or "").strip() for c in el if c.tag == "string"]


def check_sources(tname, tdef, ios):
    src_dirs = []
    for s in tdef.get("sources") or []:
        p = s.get("path") if isinstance(s, dict) else s
        if not p:
            warn("target %s：sources 存在空 path 项" % tname)
            continue
        sp = Path(p)
        if sp.is_absolute() or ".." in sp.parts or "\\" in p:
            warn("target %s：source 应为工程内相对路径：%s" % (tname, p))
        d = ios / sp
        if not d.is_dir():
            fail("target %s：source 目录不存在：%s" % (tname, p))
        else:
            n = sum(1 for _ in d.rglob("*.swift"))
            if n == 0:
                warn("target %s：source 目录无 .swift 文件（若为资源目录请忽略）：%s" % (tname, p))
            else:
                ok("target %s：source %s（%d 个 .swift）" % (tname, p, n))
            src_dirs.append(d.resolve())
    return src_dirs


def check_target(tname, tdef, ios):
    ttype = tdef.get("type")
    platform = tdef.get("platform")
    if ttype in ("application", "app-extension"):
        info("target %s：type=%s platform=%s" % (tname, ttype, platform))
    else:
        warn("target %s：type=%s 非常规（application/app-extension 之外）" % (tname, ttype))
    if platform != "iOS":
        warn("target %s：platform=%s ≠ iOS" % (tname, platform))

    src_dirs = check_sources(tname, tdef, ios)

    settings = tdef.get("settings") or {}
    base = settings.get("base") or {} if isinstance(settings, dict) else {}
    if not isinstance(base, dict):
        base = {}

    # bundle 引用
    bid = base.get("PRODUCT_BUNDLE_IDENTIFIER")
    if bid:
        if BUNDLE_ID_RE.match(str(bid)):
            ok("target %s：PRODUCT_BUNDLE_IDENTIFIER=%s" % (tname, bid))
        else:
            fail("target %s：bundle id 格式非法：%r" % (tname, bid))
    else:
        warn("target %s：未显式设置 PRODUCT_BUNDLE_IDENTIFIER（将由 bundleIdPrefix 推导）" % tname)

    # plist 位置与内容
    plist_rel = base.get("INFOPLIST_FILE")
    if plist_rel:
        sp = Path(str(plist_rel))
        pf = ios / sp
        if sp.is_absolute() or ".." in sp.parts or "\\" in str(plist_rel):
            warn("target %s：INFOPLIST_FILE 应为工程内相对路径：%s" % (tname, plist_rel))
        if not pf.is_file():
            fail("target %s：INFOPLIST_FILE 不存在：%s" % (tname, plist_rel))
        else:
            ok("target %s：INFOPLIST_FILE 存在：%s" % (tname, plist_rel))
            resolved = str(pf.resolve())
            for d in src_dirs:
                if resolved.startswith(str(d) + "/") or resolved == str(d):
                    warn("target %s：INFOPLIST_FILE 位于 source 目录内，可能被重复打包：%s" % (tname, plist_rel))
                    break
            try:
                root_d = parse_plist_dict(pf)
                cfid = dict_value_text(root_d, "CFBundleIdentifier")
                if cfid and cfid not in ("$(PRODUCT_BUNDLE_IDENTIFIER)", str(bid or "")):
                    warn("target %s：CFBundleIdentifier=%r 与 PRODUCT_BUNDLE_IDENTIFIER 不一致" % (tname, cfid))
                if ttype == "app-extension":
                    ext = dict_child(root_d, "NSExtension")
                    pt = dict_value_text(ext, "NSExtensionPointIdentifier")
                    if pt == "com.apple.broadcast-services-upload":
                        ok("target %s：广播扩展 NSExtensionPointIdentifier 正确" % tname)
                    else:
                        warn("target %s：app-extension 的广播上传 NSExtensionPointIdentifier 缺失或不符（实际：%r）"
                             % (tname, pt))
            except Exception as e:
                fail("target %s：plist 解析失败 %s：%s" % (tname, plist_rel, e))
    else:
        warn("target %s：settings.base 未设置 INFOPLIST_FILE（将使用 XcodeGen 生成 plist）" % tname)

    # entitlements / App Group
    groups = []
    ent_rel = base.get("CODE_SIGN_ENTITLEMENTS")
    if ent_rel:
        ef = ios / Path(str(ent_rel))
        if not ef.is_file():
            fail("target %s：CODE_SIGN_ENTITLEMENTS 不存在：%s" % (tname, ent_rel))
        else:
            try:
                root_d = parse_plist_dict(ef)
                groups = plist_string_array(dict_child(root_d, "com.apple.security.application-groups"))
                if groups:
                    ok("target %s：App Group：%s" % (tname, ", ".join(groups)))
                else:
                    warn("target %s：entitlements 未配置 application-groups" % tname)
            except Exception as e:
                fail("target %s：entitlements 解析失败 %s：%s" % (tname, ent_rel, e))
    return ttype, bid, groups


def run_checks(ios):
    yml = ios / "project.yml"
    if not yml.is_file():
        fail("project.yml 不存在：%s" % yml)
        return
    try:
        spec = parse_simple_yaml(yml.read_text(encoding="utf-8-sig"))
    except Exception as e:
        fail("project.yml 解析失败：%s" % e)
        return

    name = spec.get("name")
    if not name:
        fail("project.yml 缺少顶层 name（.xcodeproj 无法命名）")
        return
    ok("工程名：%s（生成 %s.xcodeproj，产物不入库）" % (name, name))

    options = spec.get("options") or {}
    dt = (options.get("deploymentTarget") or {}).get("iOS") if isinstance(options, dict) else None
    if dt is None:
        warn("options.deploymentTarget.iOS 未设置")
    else:
        try:
            v = float(str(dt))
            if v >= 15:
                info("部署目标 iOS %s（满足 PRD 要求 iOS >= 15）" % dt)
            else:
                warn("部署目标 iOS %s 低于 PRD 要求的 iOS 15" % dt)
        except ValueError:
            warn("deploymentTarget.iOS 非数值：%r" % (dt,))

    targets = spec.get("targets") or {}
    if not targets:
        fail("project.yml 无 targets")
        return

    app_bundle_id = None
    groups_by_target = {}
    types_by_target = {}
    for tname, tdef in targets.items():
        if not isinstance(tdef, dict):
            fail("target %s 定义不是映射" % tname)
            continue
        ttype, bid, groups = check_target(tname, tdef, ios)
        types_by_target[tname] = ttype
        groups_by_target[tname] = groups
        if ttype == "application" and bid:
            app_bundle_id = bid

    # 扩展 bundle id 必须以主 App bundle id 为前缀（Apple 要求）
    if app_bundle_id:
        for tname, ttype in types_by_target.items():
            if ttype != "app-extension":
                continue
            tdef = targets.get(tname) or {}
            settings = tdef.get("settings") or {} if isinstance(tdef, dict) else {}
            base = settings.get("base") or {} if isinstance(settings, dict) else {}
            eb = base.get("PRODUCT_BUNDLE_IDENTIFIER") if isinstance(base, dict) else None
            if eb and not str(eb).startswith(app_bundle_id + "."):
                fail("扩展 %s 的 bundle id %s 未以主 App %s 为前缀（Apple 要求扩展 bundle id 以主 App bundle id 开头）"
                     % (tname, eb, app_bundle_id))

    # App Group：扩展引用的 group 必须同时配置在主 App entitlements
    app_groups = set()
    for tname, ttype in types_by_target.items():
        if ttype == "application":
            app_groups |= set(groups_by_target.get(tname, []))
    for tname, ttype in types_by_target.items():
        if ttype != "app-extension":
            continue
        missing = set(groups_by_target.get(tname, [])) - app_groups
        if missing:
            fail("扩展 %s 的 App Group 未同时配置在主 App entitlements：%s"
                 % (tname, ", ".join(sorted(missing))))

    # Shared/AppGroup.swift 常量对照（静态扫描；代码若动态构造 group 则仅为参考）
    ag = ios / "Shared" / "AppGroup.swift"
    if ag.is_file():
        swift_groups = set(GROUP_RE.findall(ag.read_text(encoding="utf-8", errors="replace")))
        all_ent = set()
        for v in groups_by_target.values():
            all_ent |= set(v)
        if swift_groups and all_ent and swift_groups != all_ent:
            warn("Shared/AppGroup.swift 的 group 常量 %s 与 entitlements %s 不一致（需人工确认）"
                 % (sorted(swift_groups), sorted(all_ent)))
        elif swift_groups and all_ent:
            ok("App Group 常量与 entitlements 一致：%s" % ", ".join(sorted(swift_groups)))


def main():
    ap = argparse.ArgumentParser(description="iOS 工程结构预检（静态检查，非编译）")
    ap.add_argument("--ios-dir", default=str(Path(__file__).resolve().parent.parent),
                    help="screen-assistant/ios 目录（缺省为本脚本上一级）")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=== iOS 工程预检（静态结构检查：不是 Swift 编译，不能替代 xcodebuild 真实构建/真机验证）===")
    run_checks(Path(args.ios_dir))
    n_fail = sum(1 for lv, _ in RESULTS if lv == "FAIL")
    n_warn = sum(1 for lv, _ in RESULTS if lv == "WARN")
    for lv, msg in RESULTS:
        print("[%s] %s" % (lv, msg))
    print("=== 预检结果：FAIL=%d WARN=%d ===" % (n_fail, n_warn))
    if n_fail:
        print("存在 FAIL：请先修复工程配置（预检不等于编译通过；真实编译用 Tools/build.sh）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
