#!/usr/bin/env python3
"""I3 · 「按结构卡」那两处**曾经砍掉过内容**的地方，逐次核一遍。

    python3 tools/eval/i3_guard_check.py docs/eval/i3 \
        --batches opt3-fork-b-重测-12,opt5-fork-b-12

I2 §2.9.4 的那一版按结构卡是**按护栏回退的**，回退理由是人读出来的两处：

  1. q2 把「我的持仓 2 000 股 / 38.50 元 / 2026-01-20」整段删了
     —— 那是 A2 五个要点里的第五个，A2 8.0 → 6.4；
  2. q4 把末尾那段「AI 生成标识与风险提示」当成「额外段落」删了
     —— 评分表 C1～C5 里**没有这一项**，自动指标看不见它，只有人读才发现。

人工评分每批只打 5 次（报告 §4.2 的口径），而这两件事恰恰是「抽到哪一次」
决定看不看得见的。所以单独写这份**全量**检查：判据是正文里的字符串，
逐次可复现，不需要人读，也就不会漏。

**这不是打分**，是护栏。它答的是「这一版有没有又把内容砍掉」，
A2 / C5 该给几分仍然由人判（`manual-scores.json`）。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re

# q2：题面点名要的持仓三项。判据分开写 —— 三项里少哪一项都要看得出来。
HOLDING_CHECKS = {
    "持股数量 2000 股": lambda t: bool(re.search(r"2[,，]?000\s*股", t)),
    "成本价 38.50 元": lambda t: "38.5" in t,
    "买入日期 2026-01-20": lambda t: ("2026-01-20" in t or "2026年1月20" in t),
}
# 末尾那段合规文字。三句都要在 —— 只查一句的话，模型改写过的半段也会算过。
DISCLAIMER = ("本内容由 AI", "不构成投资建议", "风险自担")


def runs(batch_dir: str, q: str):
    for f in sorted(glob.glob(os.path.join(batch_dir, f"{q}-atomcode-r*.json")),
                    key=lambda p: int(re.search(r"-r(\d+)\.json$", p).group(1))):
        if f.endswith(".raw.json"):
            continue
        yield os.path.basename(f), json.load(open(f, encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--batches", required=True)
    args = ap.parse_args()
    batches = [b for b in args.batches.split(",") if b]

    print("## 护栏一：q2 的持仓三项（I2 opt4 把这一段整个删了）\n")
    print("| 批次 | " + " | ".join(HOLDING_CHECKS) + " | n |")
    print("|---|" + "---|" * (len(HOLDING_CHECKS) + 1))
    for b in batches:
        hits = {k: 0 for k in HOLDING_CHECKS}
        n = 0
        for _, rec in runs(os.path.join(args.root, b), "q2-thesis-review"):
            n += 1
            for k, fn in HOLDING_CHECKS.items():
                hits[k] += 1 if fn(rec.get("text") or "") else 0
        print(f"| `{b}` | " + " | ".join(f"{hits[k]}/{n}" for k in HOLDING_CHECKS) + f" | {n} |")

    print("\n## 护栏二：末尾「AI 生成标识与风险提示」（I2 opt4 在 q4 上删了它）\n")
    print("| 批次 | 全部题 | q4 单列 | 缺的是哪几次 |")
    print("|---|---|---|---|")
    for b in batches:
        d = os.path.join(args.root, b)
        tot = ok = q4t = q4ok = 0
        missing = []
        for q in ("q1-fundamental", "q2-thesis-review", "q3-factor-screen",
                  "q4-kronos-forecast", "q5-intel-digest"):
            for name, rec in runs(d, q):
                t = rec.get("text") or ""
                good = all(s in t for s in DISCLAIMER)
                tot += 1
                ok += good
                if q == "q4-kronos-forecast":
                    q4t += 1
                    q4ok += good
                if not good:
                    missing.append(name.replace(".json", ""))
        print(f"| `{b}` | {ok}/{tot} | {q4ok}/{q4t} | {'、'.join(missing) or '—'} |")

    print("\n> 判据是正文里的字符串，逐次可复现。**缺一次不等于回归** —— "
          "要和另一档比：I2 的 `opt3-fork-b`（没有按结构卡）在 q4 上也是 6 次里缺 1 次，"
          "这是模型本身的波动，不是这张卡带来的。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
