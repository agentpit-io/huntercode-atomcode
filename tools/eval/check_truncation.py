#!/usr/bin/env python3
"""扫一遍 HCA 侧的原始流，列出哪几次运行的工具返回被 AtomCode 截断过。

    python3 tools/eval/check_truncation.py [--raw docs/eval/raw]

为什么需要它：`atomcode-capabilities/src/tools/output_artifact.rs` 把超过
16 KB（`THRESHOLD_BYTES`）的工具返回换成「头 4 KB + 尾 4 KB + 一行提示」，
中间整段对模型不可见，而**两个常量都是 `const`，没有任何配置能改**（待办池
P0-11）。被截断的那几次是 A1「正文里的数字能不能在工具返回里找到」最容易
失手的地方 —— 模型看不到中间段，就可能拿记忆补上、还标成工具来源。
人工评 A1 之前先跑这个，知道该重点查哪几次。

只看 HCA 侧：截断是 AtomCode 的行为，基线侧（opencode）的 `.raw.json` 里没有
这个机制，不是同一回事，混在一张表里会让人误以为两边都有。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

MARKER = re.compile(r"output truncated — (\d+) bytes total")


def scan(sse: Path):
    """返回 (被截断次数, 工具返回总数, [被截断的 (工具名, 全长字节)])。"""
    total, cut = 0, []
    for line in sse.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            e = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if e.get("type") != "tool_result":
            continue
        total += 1
        m = MARKER.search(str(e.get("output") or ""))
        if m:
            cut.append((e.get("name") or "?", int(m.group(1))))
    return len(cut), total, cut


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=Path("docs/eval/raw"))
    args = ap.parse_args(argv)

    files = sorted(args.raw.glob("*.sse"))
    if not files:
        print(f"（{args.raw} 下没有 .sse，HCA 侧还没跑）")
        return 0

    print("| 运行 | 被截断 / 工具返回总数 | 被截断的返回（工具：全长字节） |")
    print("|---|---|---|")
    any_cut = 0
    for f in files:
        n, total, cut = scan(f)
        any_cut += n
        detail = "、".join(f"`{t}`：{b:,}" for t, b in cut) or "—"
        mark = "**" if n else ""
        print(f"| `{f.stem}` | {mark}{n} / {total}{mark} | {detail} |")
    print()
    print(f"合计 {any_cut} 次工具返回被截断（阈值 16 KB，预览头尾各 4 KB）。"
          "被截断的那几次运行要重点查 A1。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
