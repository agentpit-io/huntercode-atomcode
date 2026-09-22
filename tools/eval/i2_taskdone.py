#!/usr/bin/env python3
"""I2 · q2 的对照组里，哪几次**真的做了这道题** —— 以及把它们单独拿出来比。

    python3 tools/eval/i2_taskdone.py docs/eval/i2

背景在报告 §1.6b：q2 是「复核我持仓里 601088 的投资论点」，论点原文只在工作区的
`theses/601088.md` 里。社区版有 `read` / `glob` / `grep`，文件也在它自己的工作区里
（`up-baseline.sh` 铺的），但它**多数时候不去读**，直接回一句「你没存买入理由，
请贴给我」就结束 —— 那一次的墙钟只有 8～17 秒，而 D 维度只比速度、不问题做没做。

判据只有一条，**看调用，不看正文**（正文要人读，人读就没法复现）：

    这一次运行调了至少一个能拿到论点原文的工具 ——
    读文件的 `read` / `read_file` / `open_file`，
    或 HCA 的组合工具 `mcp__hcapack__thesis_evidence`（它的返回里带论点原文）

为什么这一条够用：论点原文只在工作区的文件里，**社区版的 6 个 MCP 里没有一个工具
会把它返回回来** —— 不读文件就不可能拿到「我当初写下的买入理由」。
HCA 的组合工具是唯一的例外（它替模型去读了那个文件），所以单列在 `PACK_TOOLS`。
反过来说，读了文件也不保证答得好 —— 这一条只分「有没有开始做」，
A2 那几分仍然由人判（`manual-scores.json`）。

⚠️ 第二张表把各批次混在一起取中位数，只用来回答「社区版做这道题时要多久」。
**逐题正式判定仍然看 `i2_compare.py` 的按批次中位数** —— 跨批次混算会把
不同阶段的 HCA 拌在一起。

输出两张表：
  1. 逐次运行：读没读文件 / 墙钟 / 调用数 / 正文字数；
  2. 两种口径下的中位数对比：**全部运行**（用户口径，报告的正式判定用这个）
     与**只看读了文件的那些次**（像不像同一件事，参考）。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

READ_TOOLS = {"read", "read_file", "open_file", "Read", "view"}
# HCA 的组合工具 `thesis_evidence` 的返回里**就带论点原文**（它自己去读那个文件），
# 所以走组合工具那条路时不会出现 read_file —— 不把它算进来，HCA 的 B 线会被
# 误判成「没做这道题」，而它的正文里逐条判了四选一。判据要认路径，不能只认工具名。
PACK_TOOLS = {"mcp__hcapack__thesis_evidence"}
DEFAULT_BATCHES = ["baseline-b", "baseline-a", "opt-b", "opt-a",
                   "opt-fork-b", "opt-fork-a", "opt2-fork-b", "opt2-fork-a",
                   "opt3-fork-b", "opt3-fork-a"]


def tools_of(rec: dict) -> list[str]:
    return [c.get("tool") or "" for c in (rec.get("calls") or [])]


def read_workspace_file(rec: dict) -> bool:
    return any(t in PACK_TOOLS or t.split("__")[-1] in READ_TOOLS or t in READ_TOOLS
               for t in tools_of(rec))


def median(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.median(xs), 1) if xs else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--question", default="q2-thesis-review")
    ap.add_argument("--batches", default="")
    a = ap.parse_args(argv)
    batches = [b for b in (a.batches.split(",") if a.batches else DEFAULT_BATCHES)
               if (a.root / b).is_dir()]

    print(f"## {a.question} · 每一次运行读没读工作区文件\n")
    print("| 批次 | 遍 | 边 | 读了论点文件 | 墙钟 | 调用 | 正文字数 |")
    print("|---|---|---|---|---|---|---|")
    rows = []
    for b in batches:
        for f in sorted((a.root / b).glob(f"{a.question}-*-r*.json")):
            if f.name.endswith(".raw.json"):
                continue
            rec = json.loads(f.read_text(encoding="utf-8"))
            side = rec.get("side") or ""
            rep = f.stem.rsplit("-r", 1)[-1]
            did = read_workspace_file(rec)
            wall = rec.get("wall_ms")
            rows.append((b, rep, side, did, wall, len(rec.get("calls") or []),
                         rec.get("text_len")))
            print(f"| `{b}` | r{rep} | {'HCA' if side == 'atomcode' else '社区版'} "
                  f"| {'✅' if did else '**✗**'} | {wall / 1000:.1f} s | "
                  f"{len(rec.get('calls') or [])} | {rec.get('text_len')} |")

    print(f"\n## 两种口径下的中位数\n")
    print("| 边 | 口径 | n | 墙钟中位 | 调用中位 | 字数中位 |")
    print("|---|---|---|---|---|---|")
    for side, label in (("atomcode", "HCA"), ("opencode", "社区版")):
        sub = [r for r in rows if r[2] == side]
        did = [r for r in sub if r[3]]
        for tag, group in (("全部运行（用户口径）", sub), ("只看读了论点文件的", did)):
            if not group:
                print(f"| {label} | {tag} | 0 | — | — | — |")
                continue
            print(f"| {label} | {tag} | {len(group)} | "
                  f"{median([r[4] for r in group]) / 1000:.1f} s | "
                  f"{median([r[5] for r in group])} | {median([r[6] for r in group])} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
