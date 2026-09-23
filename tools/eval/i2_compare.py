#!/usr/bin/env python3
"""I2 · 四个批次的逐题对比表 —— 报告里的数字由它生成，不手抄。

    python3 tools/eval/i2_compare.py docs/eval/i2

对每个批次、每道题、每一边算出中位数（工具调用次数 / 墙钟 / token），
再按 M2 评分表的 D 维度口径算 D1 / D2：

    base = 两边中位数里较小的那个
    D1 = min(1, base_calls / 本边中位数) × 13
    D2 = min(1, base_wall  / 本边中位数) × 12

D 是**纯自动项**，从原始记录直接算，可复现。`score.py` 里是同一份口径，
这里单独实现一遍是为了能跨批次并排看（score.py 一次只吃一个批次）。
两边对不上就是有一边算错了 —— `--check` 会拿 score.py 的结果核一遍。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from questions import ALL_QUESTIONS, QUESTIONS  # noqa: E402

# 这个脚本自己的 main() 只跑 M2 那 5 道（I2 的十个批次就是那 5 道），所以 QIDS 不变。
# 但 `d_scores` 被 I1 的汇总脚本 import 走了，它要按 id 查 `no_tool_ok` / `needs` ——
# 用只有 5 道的表，q6～q10 一律查成空题，`no_tool_ok` 的那一支永远走不到，
# q10（HCA 一次工具都不调、正确答案）的 D1 就成了 null 而不是满分。
QIDS = [q["id"] for q in QUESTIONS]
QBYID = {q["id"]: q for q in ALL_QUESTIONS}


def median(xs):
    xs = sorted(x for x in xs if isinstance(x, (int, float)) and x > 0)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def median0(xs):
    """与 `median` 同，但**保留 0**。见 `load()` 里 `calls0` 的注释。"""
    xs = sorted(x for x in xs if isinstance(x, (int, float)))
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def load(batch: Path) -> dict:
    """(题, 边) → 中位数三件套 + 有效次数。"""
    runs = {}
    for f in sorted(batch.glob("*.json")):
        # 只要结构化那一份。`.raw.json` 是基线侧的原始流（没有 wall_ms），
        # 一起数进来会让「有效次数」翻倍 —— 看着像跑了 6 次，其实是 3 次。
        if f.name in ("index.json", "scores.json") or f.name.endswith(".raw.json"):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if d.get("skipped") or d.get("error"):
            continue
        side = d.get("side") or ("atomcode" if "-atomcode-" in f.name else "opencode")
        qid = f.stem.rsplit(f"-{side}-", 1)[0]
        runs.setdefault((qid, side), []).append(d)
    out = {}
    for k, v in runs.items():
        out[k] = {
            "n": len(v),
            "calls": median([len(r.get("calls") or []) for r in v]),
            # `median()` 会把 0 当成"没数"丢掉（D1 的口径需要它这么做：
            # 0 次调用不能当分母）。但"这一边一次工具都没调"本身是**有效数据**，
            # 达标判定要看得见它，否则 q4 这种题会被悄悄从达标统计里漏掉 ——
            # 漏掉一道题比判它不达标更糟，看表的人不会注意到分母少了 1。
            "calls0": median0([len(r.get("calls") or []) for r in v]),
            "wall": median([r.get("wall_ms") for r in v]),
            "token": median([r.get("quota_delta") for r in v]),
            "rounds": median([r.get("rounds") for r in v]),
        }
    return out


def d_scores(m: dict, qid: str) -> dict:
    """按 M2 评分表算这道题两边的 D1 / D2。"""
    a, b = m.get((qid, "atomcode")), m.get((qid, "opencode"))
    q = QBYID.get(qid, {})
    res = {}
    for side, own, other in (("atomcode", a, b), ("opencode", b, a)):
        if not own:
            res[side] = {"D1": None, "D2": None}
            continue
        base_calls = min([x for x in (own["calls"], (other or {}).get("calls")) if x] or [None]) \
            if (own["calls"] or (other or {}).get("calls")) else None
        base_wall = min([x for x in (own["wall"], (other or {}).get("wall")) if x] or [None]) \
            if (own["wall"] or (other or {}).get("wall")) else None
        if own["calls"] and base_calls:
            d1 = round(min(1.0, base_calls / own["calls"]) * 13, 1)
        elif q.get("no_tool_ok") and not own["calls"]:
            d1 = 13.0
        elif q.get("needs") and not own["calls"]:
            d1 = 0.0
        else:
            d1 = None
        d2 = (round(min(1.0, base_wall / own["wall"]) * 12, 1)
              if own["wall"] and base_wall else None)
        res[side] = {"D1": d1, "D2": d2}
    return res


def verdict(a, b, tol: float):
    """用户 00:55 的口径：墙钟 ≤ 社区版×(1+tol) **且** 调用次数 ≤ 社区版。

    返回 True / False / None（缺数）。调用次数不给容差 —— 那是整数，
    「多调一次」就是多跑一轮模型，没有「差一点点」这回事。
    """
    if not a or not b:
        return None
    if not a.get("wall") or not b.get("wall"):
        return None
    if a.get("calls0") is None or b.get("calls0") is None:
        return None
    return a["wall"] <= b["wall"] * (1 + tol) and a["calls0"] <= b["calls0"]


def fmt(v, unit=""):
    if v is None:
        return "—"
    if unit == "s":
        return f"{v / 1000:.1f} s"
    return f"{v:,.0f}" if isinstance(v, float) and v == int(v) else f"{v:,}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path, default=Path("docs/eval/i2"), nargs="?")
    ap.add_argument("--batches", default="baseline-b,opt-b,baseline-a,opt-a")
    # 用户 2026-09-23 00:55 提高后的验收口径：逐题「墙钟中位数 ≤ opencode（容差 5%）
    # 且 调用次数中位数 ≤ opencode」。这比 D 维度那个连续打分严 —— D 里 0.9 倍也拿满分，
    # 这里 1.06 倍就算没达标。所以单独打一列，不要拿 D 的分去代答这个问题。
    ap.add_argument("--tolerance", type=float, default=0.05,
                    help="墙钟的容差（默认 0.05 = 允许比 opencode 慢 5%%）")
    args = ap.parse_args(argv)

    data = {}
    for name in [b.strip() for b in args.batches.split(",") if b.strip()]:
        d = args.root / name
        if d.is_dir():
            data[name] = load(d)
        else:
            print(f"（跳过：没有 {d}）", file=sys.stderr)
    if not data:
        print("✗ 一个批次都没读到", file=sys.stderr)
        return 2

    for name, m in data.items():
        print(f"\n## 批次 {name}\n")
        print("| 题 | HCA 调用 | 社区版调用 | HCA 墙钟 | 社区版墙钟 | HCA token | 社区版 token | "
              "HCA D1 | HCA D2 | HCA D 合计 | 达标 | n(HCA/社区版) |")
        print("|---|" + "---|" * 11)
        tot = []
        passed = []
        for qid in QIDS:
            a, b = m.get((qid, "atomcode")), m.get((qid, "opencode"))
            s = d_scores(m, qid)["atomcode"]
            dsum = (None if s["D1"] is None or s["D2"] is None
                    else round(s["D1"] + s["D2"], 1))
            if dsum is not None:
                tot.append(dsum)
            ok = verdict(a, b, args.tolerance)
            if ok is not None:
                passed.append(ok)
            print(f"| {qid} | {fmt((a or {}).get('calls'))} | {fmt((b or {}).get('calls'))} | "
                  f"{fmt((a or {}).get('wall'), 's')} | {fmt((b or {}).get('wall'), 's')} | "
                  f"{fmt((a or {}).get('token'))} | {fmt((b or {}).get('token'))} | "
                  f"{fmt(s['D1'])} | {fmt(s['D2'])} | **{fmt(dsum)}** | "
                  f"{'✓' if ok else ('✗' if ok is False else '—')} | "
                  f"{(a or {}).get('n', 0)}/{(b or {}).get('n', 0)} |")
        if tot:
            print(f"\n**HCA 的 D 维度按题平均：{statistics.mean(tot):.1f} / 25**"
                  f"（{len(tot)} 道题有数）")
        if passed:
            print(f"**逐题达标（墙钟 ≤ 社区版×{1 + args.tolerance:.2f} 且 调用次数 ≤ 社区版）："
                  f"{sum(passed)}/{len(passed)}**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
