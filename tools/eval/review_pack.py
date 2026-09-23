#!/usr/bin/env python3
"""I2 · 人工评分的阅读包 —— 把一个批次里每一次运行压成「看一眼就能打分」的一段。

    python3 tools/eval/review_pack.py docs/eval/i2/opt-b --side atomcode
    python3 tools/eval/review_pack.py docs/eval/i2/opt-b --only q2 --full

每一次运行给出：

  · 事实：墙钟 / 轮数 / 工具调用序列 / token / 停止原因
  · A1 取证：`tools/eval/audit_numbers.py` 的未命中条数（**不给分**，只挑出
    需要人看一眼的那几个）
  · 正文：默认截前 2500 字，`--full` 给全文

评分表里哪些项要人判，见 `tools/eval/questions.py` 末尾与 M2 报告 §4。
**这个脚本不打分** —— 它只是把打分要看的东西放在一屏里。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from audit_numbers import audit  # noqa: E402
from questions import ALL_QUESTIONS  # noqa: E402

QBYID = {q["id"]: q for q in ALL_QUESTIONS}


def audit_summary(stem: Path) -> str:
    """跑一遍 A1 取证，只回一行摘要（未命中几条 / 共几条）。"""
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            audit(stem)
    except Exception as e:  # noqa: BLE001
        return f"取证跑不了：{type(e).__name__}: {e}"
    lines = buf.getvalue().splitlines()
    # audit_numbers 的输出是一张 markdown 表：第二行是总结，表里未命中的行
    # 「命中的工具」一列是 `✗`。别去猜格式 —— 认这两处就够，改了会一眼看出来。
    summary = next((l for l in lines if "个没在工具返回里找到" in l), "")
    miss = [l for l in lines if l.startswith("|") and "✗" in l]
    head = "；".join(l.split("|")[1].strip() + "→" + l.split("|")[3].strip()[:60]
                     for l in miss[:6] if len(l.split("|")) > 3)
    if not summary:
        return "取证没给出总结行（格式变了？）"
    return summary.split("·", 1)[-1].strip() + (f"\n    未命中的数：{head}" if miss else "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("batch", type=Path)
    ap.add_argument("--side", default="", help="只看某一边（atomcode / opencode）")
    ap.add_argument("--only", default="", help="只看某道题（id 子串）")
    ap.add_argument("--chars", type=int, default=2500)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--no-audit", action="store_true", help="跳过 A1 取证（快）")
    a = ap.parse_args(argv)

    # `.raw.json` 是社区版那一侧的 opencode 原始消息（没有 text/wall_ms），
    # 混进来会多打出一堆空壳条目，而且会把上一条的正文挤到下一条的表头后面 ——
    # 读包的人很容易把 A 的正文当成 B 的（I1 人工评分时差点算错一条）。
    files = sorted(f for f in a.batch.glob("*.json")
                   if f.name not in ("index.json", "scores.json")
                   and not f.name.endswith((".raw.json", ".shim.jsonl")))
    n = 0
    for f in files:
        if a.side and f"-{a.side}-" not in f.name:
            continue
        if a.only and a.only not in f.name:
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if d.get("skipped"):
            print(f"\n{'=' * 78}\n{f.stem} —— 未计分：{d.get('error')}")
            continue
        n += 1
        q = QBYID.get(f.stem.rsplit("-atomcode-", 1)[0].rsplit("-opencode-", 1)[0], {})
        calls = d.get("calls") or []
        print(f"\n{'=' * 78}")
        print(f"{f.stem}   墙钟 {d.get('wall_ms')} ms · 轮数 {d.get('rounds')} · "
              f"工具 {len(calls)} 次 · token {d.get('quota_delta')} · "
              f"停止 {d.get('stop_reason')}")
        for c in calls:
            args_ = c.get("arguments")
            if isinstance(args_, dict):
                args_ = {k: str(v)[:60] for k, v in args_.items()
                         if not k.endswith("hermes_user_id")}
            print(f"  {c.get('seq'):>2}. {c.get('tool')} {json.dumps(args_, ensure_ascii=False)[:120]}"
                  f"  → {c.get('success')} {c.get('duration_ms')}ms {c.get('output_len')}B")
        if q.get("points"):
            print("  要点（A2 逐点判）：")
            for i, p in enumerate(q["points"], 1):
                print(f"    {i}. {p}")
        if not a.no_audit:
            print(f"  A1 取证：{audit_summary(a.batch / f.stem)}")
        text = d.get("text") or ""
        print("  ── 正文 ──")
        print(text if a.full else text[:a.chars] + ("\n  …（截断，用 --full 看全文）"
                                                    if len(text) > a.chars else ""))
    print(f"\n共 {n} 次运行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
