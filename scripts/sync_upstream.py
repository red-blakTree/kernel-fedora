#!/usr/bin/env python3
"""检测 zen-kernel 最新正式版本，更新五个 spec（按 Copr 工程分目录）的版本宏与三份 config。

- 只认 tag 形如 v7.2.4-zen2 且带 linux-<tag>.patch.zst 附件的正式发布，
  lqx 系列（v7.2.4-lqx4）会被忽略。
- config 来自 Arch Linux linux-zen 官方打包仓库，作为 Fedora 适配前的基线。

用法：
    python3 scripts/sync_upstream.py

脚本只改文件，不提交、不构建；是否发生变化用 `git diff` 判断。
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPECS = [
    REPO_ROOT / "linux-zen-fedora" / "kernel-zen.spec",
    REPO_ROOT / "linux-zen-fedora" / "kernel-zen-v3.spec",
    REPO_ROOT / "linux-power" / "kernel-power.spec",
    REPO_ROOT / "linux-power" / "kernel-power-v3.spec",
    REPO_ROOT / "linux-power-lto" / "kernel-power-lto.spec",
]

# config 是 spec 的 Source2，rpkg 按 spec 所在目录解析它，所以每个工程目录各放一份；
# 由本脚本一起刷新，三份内容必须完全一致。
CONFIGS = [
    REPO_ROOT / "linux-zen-fedora" / "config",
    REPO_ROOT / "linux-power" / "config",
    REPO_ROOT / "linux-power-lto" / "config",
]

RELEASES_URL = "https://api.github.com/repos/zen-kernel/zen-kernel/releases?per_page=30"
ARCH_CONFIG_URL = (
    "https://gitlab.archlinux.org/archlinux/packaging/packages/linux-zen"
    "/-/raw/main/config.x86_64"
)
TAG_RE = re.compile(
    r"^v(?P<major>\d+)\.(?P<minor>\d+)\.(?P<stable>\d+)-zen(?P<zenrel>\d+)$"
)


def fetch(url: str, timeout: int = 60) -> bytes:
    headers = {"User-Agent": "zen-kernel-fedora-sync"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def latest_zen_release() -> tuple[str, dict[str, str]]:
    releases = json.loads(fetch(RELEASES_URL))
    for release in releases:
        tag = release.get("tag_name", "")
        if release.get("prerelease") or release.get("draft"):
            continue
        match = TAG_RE.match(tag)
        if not match:
            continue
        asset = f"linux-{tag}.patch.zst"
        if any(a.get("name") == asset for a in release.get("assets", [])):
            return tag, match.groupdict()
    raise RuntimeError("未找到带 linux-<tag>.patch.zst 附件的 zen 正式发布")


def set_macro(text: str, name: str, value: str, spec_name: str) -> str:
    pattern = rf"(?m)^(%global\s+{re.escape(name)}\s+)\S+$"
    text, count = re.subn(pattern, rf"\g<1>{value}", text)
    if count != 1:
        raise RuntimeError(f"{spec_name} 中应恰好有一处 '%global {name}'，实际 {count} 处")
    return text


def main() -> int:
    tag, parts = latest_zen_release()
    version = f"{parts['major']}.{parts['minor']}.{parts['stable']}"

    for spec_path in SPECS:
        spec = spec_path.read_text(encoding="utf-8")
        spec = set_macro(spec, "_majver", parts["major"], spec_path.name)
        spec = set_macro(spec, "_basekver", f"{parts['major']}.{parts['minor']}", spec_path.name)
        spec = set_macro(spec, "_stablekver", parts["stable"], spec_path.name)
        spec = set_macro(spec, "_zenrel", parts["zenrel"], spec_path.name)
        spec_path.write_text(spec, encoding="utf-8")

    # Arch 的 config 与内核版本可能短期错位，spec 里的 olddefconfig 会补齐；
    # 因此这里拿不到 config 只告警、不中断版本更新。
    try:
        config = fetch(ARCH_CONFIG_URL)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"WARNING: 拉取 Arch config 失败（保持仓库内原文件）：{exc}", file=sys.stderr)
    else:
        if any(path.read_bytes() != config for path in CONFIGS):
            for path in CONFIGS:
                path.write_bytes(config)
            print("config 已更新（三个工程目录同步写入，来自 Arch linux-zen）")

    print(f"tag={tag} version={version} zenrel={parts['zenrel']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
