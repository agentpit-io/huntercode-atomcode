#!/usr/bin/env python3
"""I2 · 瀑布表生成 —— 把一批运行的原始记录拼成「时间都花在哪了」。

    python3 tools/eval/waterfall_report.py docs/eval/i2/baseline-b [更多批次…]

三份数据合起来看：

  · `<case>.json` 的 `waterfall`  —— daemon 这一侧的段落切分（客户端时间戳，
    见 eval_atomcode.waterfall）：首轮模型调用 / 每次工具执行 / 下一轮模型 / 收尾
  · `<case>.shim.jsonl`           —— llm-shim 这一跳看到的每次上游请求：
    工具个数与 schema 字节、系统提示字符数、首字与总耗时、网关回的 usage
  · 两者相减                      —— **daemon 内部的固定开销**：
    「首轮模型段」减去「第一次上游请求的总耗时」= 发请求之前那段
    （UserPromptSubmit hook + 上下文装配 + 首轮提示装配）。

⚠️ **PreToolUse / PostToolUse 的耗时不在「工具段」里**。实测（docs/eval/i2/pilot）
工具段 1 157 ms、工具自报 1 156 ms —— 差 1 ms，说明上游是在发出 `tool_start`
**之前**跑 PreToolUse、在 `tool_result` **之后**跑 PostToolUse，两跳都落进了
相邻的「模型段」里，SSE 上分不开。所以 hook 那部分只能用
`tools/eval/hook_bench.py` 单独量，表里的 `工具段余量` 只是个佐证。

只用真实记录，缺哪一份就在表里写 `—`，不推算。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


def median(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.median(xs), 1) if xs else None


def load_batch(d: Path) -> list:
    runs = []
    for f in sorted(d.glob("*.json")):
        if f.name == "index.json":
            continue
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if rec.get("skipped") or rec.get("error"):
            continue
        shim = d / (f.stem + ".shim.jsonl")
        rec["_shim"] = [json.loads(l) for l in shim.read_text(encoding="utf-8").splitlines()
                        if l.strip()] if shim.is_file() else []
        rec["_question"] = f.stem.rsplit("-atomcode-", 1)[0].rsplit("-opencode-", 1)[0]
        rec["_batch"] = d.name
        runs.append(rec)
    return runs


def dissect(rec: dict) -> dict:
    """一次运行的时间构成。取不到的写 None。"""
    wf = rec.get("waterfall") or {}
    segs = wf.get("segments") or []
    shim = rec.get("_shim") or []
    first_model = next((s for s in segs if s["kind"] == "model"), None)
    # 发请求之前那一段 = 首轮模型段 − 第一次上游请求的总耗时
    pre = None
    if first_model and shim and isinstance(shim[0].get("total_ms"), (int, float)):
        pre = round(first_model["ms"] - shim[0]["total_ms"], 1)
    tool_segs = [s for s in segs if s["kind"] == "tool"]
    # 工具段余量 = SSE 上看到的工具段 − 工具自己报的耗时。
    # **不是 hook 的耗时**（见文件头的说明），只是用来确认这一段里没有别的东西。
    hook_ms = [round(s["ms"] - s["duration_ms_self"], 1) for s in tool_segs
               if isinstance(s.get("ms"), (int, float))
               and isinstance(s.get("duration_ms_self"), (int, float))]
    req = shim[0] if shim else {}
    usage = (req.get("usage") or {})
    return {
        "wall_ms": rec.get("wall_ms"),
        "calls": len(rec.get("calls") or []),
        "rounds": rec.get("rounds"),
        "token": rec.get("quota_delta"),
        "pre_request_ms": pre,
        "model_ms": (wf.get("totals") or {}).get("model_ms"),
        "tool_ms": (wf.get("totals") or {}).get("tool_ms"),
        "tail_ms": (wf.get("totals") or {}).get("tail_ms"),
        "tool_gap_ms_per_call": median(hook_ms),
        "tool_gap_ms_total": round(sum(hook_ms), 1) if hook_ms else None,
        "upstream_requests": len(shim),
        "n_tools": req.get("n_tools"),
        "tools_bytes": req.get("tools_bytes"),
        "system_chars": req.get("system_chars"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "ttfb_ms": req.get("ttfb_ms"),
    }


FIELDS = [("wall_ms", "墙钟 ms"), ("calls", "工具调用"), ("rounds", "轮数"),
          ("token", "token"), ("pre_request_ms", "发请求前 ms"),
          ("model_ms", "模型段合计 ms"), ("tool_ms", "工具段合计 ms"),
          ("tail_ms", "收尾 ms"), ("tool_gap_ms_per_call", "工具段余量/次 ms"),
          ("tool_gap_ms_total", "工具段余量合计 ms"), ("upstream_requests", "上游请求数"),
          ("n_tools", "工具个数"), ("tools_bytes", "schema 字节"),
          ("system_chars", "系统提示字符"), ("prompt_tokens", "首轮输入 token"),
          ("ttfb_ms", "首字 ms")]


def fmt(v):
    return "—" if v is None else (f"{v:,}" if isinstance(v, int) else f"{v:,.1f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+", type=Path)
    ap.add_argument("--json", type=Path, help="同时落一份结构化结果")
    args = ap.parse_args(argv)

    runs = []
    for d in args.dirs:
        if not d.is_dir():
            print(f"✗ 没有目录 {d}", file=sys.stderr)
            return 2
        runs.extend(load_batch(d))
    if not runs:
        print("✗ 没有可读的运行记录", file=sys.stderr)
        return 2

    groups: dict = {}
    for r in runs:
        groups.setdefault((r["_question"], r["_batch"], r.get("side")), []).append(dissect(r))

    out = {}
    questions = sorted({k[0] for k in groups})
    for q in questions:
        print(f"\n### {q}\n")
        keys = [k for k in groups if k[0] == q]
        keys.sort(key=lambda k: (k[2] or "", k[1]))
        header = " | ".join([f"{k[2]}·{k[1]}（n={len(groups[k])}）" for k in keys])
        print(f"| 项 | {header} |")
        print("|---|" + "---|" * len(keys))
        for field, label in FIELDS:
            cells = [fmt(median([d[field] for d in groups[k]])) for k in keys]
            print(f"| {label} | " + " | ".join(cells) + " |")
        out[q] = {f"{k[2]}·{k[1]}": {field: median([d[field] for d in groups[k]])
                                     for field, _ in FIELDS} for k in keys}

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n（结构化结果已写 {args.json}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
