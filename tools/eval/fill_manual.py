#!/usr/bin/env python3
"""把人工评分从 `docs/eval/manual-scores.json` 合进 `docs/eval/scores.json`。

    python3 tools/eval/fill_manual.py \
        --manual docs/eval/manual-scores.json --scores docs/eval/scores.json

为什么单独存一份：`scores.json` 是 `score.py prepare` 生成的，里面混着自动项、
事实字段和工作表骨架，几千行，人在里面改分容易改错也不好 review。
人工判断只有「哪一次运行、哪一项、几分、为什么」四个字段，单独存成
`manual-scores.json` 之后：

  · diff 看得懂 —— 改了哪一条分、理由怎么变的，一眼就能看出来；
  · 可重跑 —— `prepare` 重新生成 `scores.json` 之后再 fill 一遍就回来了
    （`prepare` 自己也会带上一版的 manual，这里是双保险）；
  · 报告里每一条理由都能追到这个文件的某一行。

格式（分数与理由必须成对，只给一个就报错）：

    {
      "q1-fundamental-atomcode-r1": {
        "A1": [10, "审了 20 个数，3 个未命中逐个看过：……"],
        "A2": [8.0, "8 个要点全中"]
      }
    }

满分校验：超过评分表的 max 直接报错退出 —— 手滑把 8 分项写成 10 分，
报告里会多出一个没人能解释的数。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from score import MANUAL_ITEMS, OVERRIDABLE  # noqa: E402

ALLOWED = set(MANUAL_ITEMS) | set(OVERRIDABLE)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manual", type=Path, default=Path("docs/eval/manual-scores.json"))
    ap.add_argument("--scores", type=Path, default=Path("docs/eval/scores.json"))
    args = ap.parse_args(argv)

    manual = json.loads(args.manual.read_text(encoding="utf-8"))
    d = json.loads(args.scores.read_text(encoding="utf-8"))
    runs = d["runs"]

    errs, n = [], 0
    for rid, items in manual.items():
        if rid.startswith("_"):      # 允许 _说明 这类注释键
            continue
        if rid not in runs:
            errs.append(f"{rid}：scores.json 里没有这次运行")
            continue
        for code, val in items.items():
            if code not in ALLOWED:
                errs.append(f"{rid}.{code}：不是人工项（人工项只有 {sorted(ALLOWED)}）")
                continue
            if not (isinstance(val, list) and len(val) == 2):
                errs.append(f"{rid}.{code}：要写成 [分数, \"理由\"]")
                continue
            score, why = val
            if not isinstance(score, (int, float)):
                errs.append(f"{rid}.{code}：分数不是数字")
                continue
            if not (isinstance(why, str) and why.strip()):
                errs.append(f"{rid}.{code}：理由不能为空 —— 没理由的分不算分")
                continue
            slot = runs[rid].setdefault("manual", {}).setdefault(code, {})
            mx = slot.get("max")
            if isinstance(mx, (int, float)) and score > mx:
                errs.append(f"{rid}.{code}：{score} 超过满分 {mx}")
                continue
            if score < 0:
                errs.append(f"{rid}.{code}：{score} 是负分")
                continue
            slot["score"] = score
            slot["why"] = why
            n += 1

    if errs:
        print("✗ 人工评分有问题，一条都没写进去：", file=sys.stderr)
        for e in errs:
            print("   " + e, file=sys.stderr)
        return 2

    args.scores.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    todo = sum(1 for v in runs.values() for k in MANUAL_ITEMS
               if (v.get("manual", {}).get(k) or {}).get("score") is None)
    print(f"已合入 {n} 条人工评分 → {args.scores}；还差 {todo} 项没评")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
