#!/usr/bin/env python3
"""按题把 `score.py report` 的结果摊成一张表 —— I1 报告的分题表由它生成，不手抄。

    python3 tools/eval/i1_byq.py docs/eval/c1/scores-summary.json --title "B 线"

`score.py report` 只打两行合计，看不出是哪一道题拉开的差距。这里按题取**中位数**
（与 D 维度同一个统计口径，不受单次异常值带偏），并标出每一题两边的差。
"""
from __future__ import annotations
import argparse, json, statistics, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from questions import ALL_QUESTIONS  # noqa: E402

QIDS = [q["id"] for q in ALL_QUESTIONS]


def med(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.median(xs), 1) if xs else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("summary", type=Path)
    ap.add_argument("--title", default="")
    ap.add_argument("--json-out", type=Path, default=None)
    a = ap.parse_args(argv)
    rows = json.loads(a.summary.read_text(encoding="utf-8"))["rows"]
    by = {}
    for r in rows:
        by.setdefault((r["question"], r["side"]), []).append(r)
    out = {}
    print(f"\n### {a.title} · 分题得分（每格取该题 3 次运行的中位数）\n")
    print("| 题 | A 准确性 (25) | B 工具 (25) | C 规范 (25) | D 步数耗时 (25) | 总分 (100) |")
    print("|---|---|---|---|---|---|")
    for q in QIDS:
        cells = {}
        for side in ("atomcode", "opencode"):
            rs = by.get((q, side)) or []
            if not rs:
                continue
            cells[side] = {k: med([x.get(k) for x in rs]) for k in ("A", "B", "C", "D", "total")}
        if not cells:
            continue
        out[q] = cells
        def fmt(k):
            a_ = (cells.get("atomcode") or {}).get(k)
            b_ = (cells.get("opencode") or {}).get(k)
            if a_ is None or b_ is None:
                return f"{a_ if a_ is not None else '—'} / {b_ if b_ is not None else '—'}"
            mark = "" if abs(a_ - b_) < 0.05 else ("**+%.1f**" % (a_ - b_) if a_ > b_ else "%.1f" % (a_ - b_))
            return f"{a_} / {b_} {mark}".strip()
        print(f"| {q} | {fmt('A')} | {fmt('B')} | {fmt('C')} | {fmt('D')} | {fmt('total')} |")
    print("\n（每格是「HCA / 社区版」，第三个数是差；差为正 = HCA 高）")
    if a.json_out:
        a.json_out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写 {a.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
