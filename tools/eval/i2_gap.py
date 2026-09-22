#!/usr/bin/env python3
"""I2 · 逐题追差距 —— 一道题在各个批次里到底差在哪。

    python3 tools/eval/i2_gap.py docs/eval/i2 --batches baseline-b,opt-b,opt-fork-b

对每道题、每个批次，并排给出 HCA 与社区版的：
调用次数 / 轮数 / 墙钟 / 每轮平均模型时间 / 工具总耗时 / 调用的工具名。

**「每轮平均模型时间」是这里的重点**：墙钟 = 轮数 × 每轮模型时间 + 工具时间。
（工具那一项取**并集**：并行调用时加法会超过墙钟，这一列就会出负数。⚠ = 那个批次
没有瀑布表、只能退回加法，那一格的「每轮模型」不可用。）
步数优化动的是前一个因子，引擎优化动的是后一个。分不开这两者，就说不清
一道题还差的那几秒该找谁要。

来源全部是原始记录（`<case>.json` 的 `calls` / `rounds` / `wall_ms`
与工具自报的 `duration_ms`），不推算；缺哪份写 `—`。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from questions import QUESTIONS  # noqa: E402
from eval_opencode import flatten as oc_flatten, waterfall as oc_waterfall  # noqa: E402

QIDS = [q["id"] for q in QUESTIONS]


def med(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return statistics.median(xs) if xs else None


def collect(batch: Path, qid: str, side: str) -> dict | None:
    runs = []
    for f in sorted(batch.glob(f"{qid}-{side}-r*.json")):
        if f.name.endswith(".raw.json"):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        # 社区版那一侧：有 raw.json 就**现场重算**瀑布。json 里存的那份可能是旧口径
        # （`tool_ms` 用加法，并行调用会算重），现场算才能和 HCA 侧同口径 ——
        # 与 i2_twosided.py 的做法一致。
        if side == "opencode":
            raw = f.with_name(f.name.replace(".json", ".raw.json"))
            if raw.is_file():
                w = oc_waterfall(oc_flatten(json.loads(raw.read_text(encoding="utf-8"))
                                            .get("messages") or []))
                if w:
                    d = {**d, "waterfall": w}
        runs.append(d)
    if not runs:
        return None
    # **工具耗时取并集，不是把各次自报耗时加起来。** 并行调用时加法会超过墙钟，
    # 于是下面的「每轮模型 =（墙钟 − 工具）/ 轮数」会算出**负数**（A 线 q1 上实测
    # 出过 −14.9 s）。和 eval_opencode 那边同一个坑（见那份文件里 union_ms 的注释）。
    # 并集取自瀑布表的 totals.tool_ms；老批次没有瀑布时退回加法并在输出里标 ⚠。
    tool_ms, tool_approx = [], False
    for d in runs:
        tot = (d.get("waterfall") or {}).get("totals") or {}
        t = tot.get("tool_ms")
        # `tool_sum_ms` 是并集口径那一版探针才写的字段 —— 没有它就说明这份记录里的
        # `tool_ms` 是**加法**（老探针 / 老 eval_opencode），并行调用时会超过墙钟。
        old_probe = "tool_sum_ms" not in tot
        if t is None:
            t = sum(c.get("duration_ms") or 0 for c in (d.get("calls") or []))
            old_probe = True
        if old_probe and len(d.get("calls") or []) > 1:
            tool_approx = True
        tool_ms.append(t)
    rounds = [d.get("rounds") for d in runs]
    wall = [d.get("wall_ms") for d in runs]
    per_round = []
    if not tool_approx:        # 加法口径下这一列必然偏小、甚至为负 —— 不出这个数
        for d, t in zip(runs, tool_ms):
            r = d.get("rounds") or 0
            w = d.get("wall_ms")
            if r and w is not None:
                per_round.append((w - t) / r)
    names = []
    for d in runs:
        names.append([c.get("tool") or c.get("name") for c in (d.get("calls") or [])])
    # 工具名按「出现次数最多的那一次运行」展示，避免 3 次拼成一锅
    names.sort(key=len)
    return {
        "n": len(runs),
        "calls": med([len(x) for x in names]),
        "rounds": med(rounds),
        "wall": med(wall),
        "tool_ms": med(tool_ms),
        "per_round": med(per_round),
        "tool_approx": tool_approx,
        "sample": names[len(names) // 2],
    }


def fmt(v, unit=""):
    if v is None:
        return "—"
    if unit == "s":
        return f"{v / 1000:.1f}"
    return f"{v:g}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path, nargs="?", default=Path("docs/eval/i2"))
    ap.add_argument("--batches", default="baseline-b,opt-b,opt-fork-b")
    ap.add_argument("--only", default="")
    args = ap.parse_args(argv)
    batches = [b.strip() for b in args.batches.split(",") if b.strip()]

    for qid in QIDS:
        if args.only and args.only not in qid:
            continue
        print(f"\n### {qid}\n")
        print("| 批次 | 边 | 调用 | 轮数 | 墙钟(s) | 工具合计(s) | 每轮模型(s) | n |")
        print("|---|---|---|---|---|---|---|---|")
        for b in batches:
            d = args.root / b
            if not d.is_dir():
                continue
            for side, label in (("atomcode", "HCA"), ("opencode", "社区版")):
                m = collect(d, qid, side)
                if not m:
                    continue
                print(f"| {b} | {label} | {fmt(m['calls'])} | {fmt(m['rounds'])} | "
                      f"{fmt(m['wall'], 's')} | {fmt(m['tool_ms'], 's')}"
                      f"{' ⚠' if m.get('tool_approx') else ''} | "
                      f"{fmt(m['per_round'], 's')} | {m['n']} |")
        # 工具序列（取中位那一次）
        for b in batches:
            d = args.root / b
            if not d.is_dir():
                continue
            m = collect(d, qid, "atomcode")
            if m:
                print(f"\n`{b}` HCA 那一次调的是：{m['sample'] or '（一次都没调）'}")
        m = None
        for b in batches:
            d = args.root / b
            if d.is_dir():
                m = collect(d, qid, "opencode") or m
        if m:
            print(f"社区版：{m['sample'] or '（一次都没调）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
