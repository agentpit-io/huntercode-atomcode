#!/usr/bin/env python3
"""把评测账本铺进 HCA 的工作区（`holdings/` 与 `theses/`）。

**内容不是另写一份**，而是从 `eval-account.json` 里的 `seed` 生成 —— 那正是
`deploy/eval/seed_eval_account.py` 播进基线 api 的同一份数据。这样两边看到的
持仓与论点**由构造保证一致**，不会出现"我在两个地方各写了一遍、其中一处打错"
这种把评测结果变成噪声的事。

HCA 侧同时能从两处读到它：
  · 文件 —— `holdings/positions.md` / `theses/<代码>.md`（工作区形态，read_file 可读）
  · api  —— `mcp__portfolio__*` / `mcp__watchlist__*` 走 `HERMES_API_URL` 到同一套 api

在**宿主**上跑，用 docker cp 送进容器（这条路不经模型，不受 guard hook 约束）。

    python3 tools/eval/seed_workspace.py --account ~/hca/secrets/eval-account.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def positions_md(seed: list) -> str:
    lines = [
        "# 持仓与交易记录",
        "",
        "> 这是 A/B 评测用的测试账本（`tools/eval/seed_workspace.py` 生成），",
        "> 与基线 api 里播种的是同一份数据。持仓数量与成本价是评测设定的，",
        "> 不是行情 —— 行情请用数据工具实时取。",
        "",
        "| 代码 | 名称 | 持股数 | 买入均价(元) | 买入日期 | 论点文件 |",
        "|---|---|---|---|---|---|",
    ]
    for s in seed:
        lines.append(
            f"| {s['code']} | {s['name']} | {s['shares']} | {s['cost_price']} | "
            f"{s['buy_date']} | `theses/{s['code']}.md` |"
        )
    lines.append("")
    return "\n".join(lines)


def thesis_md(s: dict) -> str:
    text = s["thesis"]
    reasons, falsify = text, ""
    if "证伪条件：" in text:
        reasons, falsify = text.split("证伪条件：", 1)
    reasons = reasons.replace("买入理由：", "").strip()
    return "\n".join([
        f"# {s['code']} {s['name']} · 投资论点",
        "",
        f"- 建仓日期：{s['buy_date']}",
        f"- 持股数：{s['shares']}",
        f"- 买入均价：{s['cost_price']} 元",
        "",
        "## 买入理由",
        "",
        reasons,
        "",
        "## 证伪条件",
        "",
        (falsify.strip() or "（未填写）"),
        "",
    ])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=Path, required=True)
    ap.add_argument("--container", default="hca-daemon")
    ap.add_argument("--workspace", default="/workspace")
    args = ap.parse_args(argv)

    acct = json.loads(args.account.read_text(encoding="utf-8"))
    seed = acct.get("seed") or []
    if not seed:
        print("✗ eval-account.json 里没有 seed", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "holdings").mkdir()
        (tmp / "theses").mkdir()
        (tmp / "holdings" / "positions.md").write_text(positions_md(seed), encoding="utf-8")
        for s in seed:
            (tmp / "theses" / f"{s['code']}.md").write_text(thesis_md(s), encoding="utf-8")

        for sub in ("holdings", "theses"):
            for f in sorted((tmp / sub).iterdir()):
                dst = f"{args.container}:{args.workspace}/{sub}/{f.name}"
                p = subprocess.run(["docker", "cp", str(f), dst],
                                   capture_output=True, text=True)
                if p.returncode != 0:
                    print(f"✗ docker cp {dst} 失败：{p.stderr.strip()}", file=sys.stderr)
                    return 1
                print(f"[seed-ws] 已铺 {args.workspace}/{sub}/{f.name}")

    # 核对：容器里真的有这些文件（不看 docker cp 的返回码就算数）
    p = subprocess.run(["docker", "exec", args.container, "sh", "-c",
                        f"ls -l {args.workspace}/holdings {args.workspace}/theses"],
                       capture_output=True, text=True)
    print(p.stdout or p.stderr)
    return 0 if p.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
