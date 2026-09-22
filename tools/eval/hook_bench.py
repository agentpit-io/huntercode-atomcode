#!/usr/bin/env python3
"""I2 · hook 开销基准 —— 在 daemon 容器里直接跑 hook 脚本并计时。

为什么不从 SSE 里推：hook 的耗时在 SSE 上和「模型调用」「MCP 往返」混在一段里
（`tool_start` → `tool_result` 之间同时包含 PreToolUse hook + MCP + PostToolUse hook），
分不开。这里按 hook 真实的调用方式（payload 从 stdin 进、读 stdout 最后一行 JSON）
直接跑 N 次取中位数 —— 量的就是内核每次要等的那段时间。

    docker exec hca-i2-daemon python3 /opt/hca/tools/hook_bench.py --repeat 7

输出一行 JSON：每个 hook 的中位数 / 最小 / 最大（毫秒），以及两个合计：
  · per_tool_call_ms  = guard(PreToolUse) + audit(PostToolUse)，**每次工具调用都付**
  · per_prompt_ms     = UserPromptSubmit 那几条里**最慢的一条**
                        （上游 cc_hooks.rs:636 是并发跑的，付的是最大值不是和）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HOOKS_DIR = os.environ.get("HCA_HOOKS_DIR", "/workspace/.hooks")

# payload 形状按上游 cc_hooks.rs 实际写进 stdin 的那份来（M1/M4 的用例里核过）
PAYLOADS = {
    "guard": {
        "script": "guard.sh", "event": "PreToolUse", "per_tool_call": True,
        "payload": {"hook_event_name": "PreToolUse", "session_id": "bench",
                    "cwd": "/workspace", "tool_name": "mcp__watchlist__stock_quickview",
                    "tool_input": {"code": "600519"}},
    },
    "audit": {
        "script": "audit.sh", "event": "PostToolUse", "per_tool_call": True,
        "payload": {"hook_event_name": "PostToolUse", "session_id": "bench",
                    "cwd": "/workspace", "tool_name": "mcp__watchlist__stock_quickview",
                    "tool_input": {"code": "600519"},
                    "tool_response": {"output": "{\"price\": 1}"}},
    },
    "context": {
        "script": "context.sh", "event": "UserPromptSubmit", "per_prompt": True,
        "payload": {"hook_event_name": "UserPromptSubmit", "session_id": "bench",
                    "cwd": "/workspace", "prompt": "600519 今天怎么样"},
    },
    "lang": {
        "script": "lang.sh", "event": "UserPromptSubmit", "per_prompt": True,
        "payload": {"hook_event_name": "UserPromptSubmit", "session_id": "bench",
                    "cwd": "/workspace", "prompt": "600519 今天怎么样"},
    },
    "budget": {
        "script": "budget.sh", "event": "UserPromptSubmit", "per_prompt": True,
        "payload": {"hook_event_name": "UserPromptSubmit", "session_id": "bench",
                    "cwd": "/workspace", "prompt": "600519 今天怎么样"},
    },
}


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def run_once(script: str, payload: dict) -> float:
    path = os.path.join(HOOKS_DIR, script)
    data = json.dumps(payload, ensure_ascii=False).encode()
    t0 = time.time()
    subprocess.run([path], input=data, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=60)
    return (time.time() - t0) * 1000


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=7)
    ap.add_argument("--warmup", type=int, default=1,
                    help="先跑几次不计数 —— 第一次要付页缓存与交易日历缓存的冷启动")
    ap.add_argument("--only", default="")
    args = ap.parse_args(argv)

    out = {"hooks_dir": HOOKS_DIR, "repeat": args.repeat, "hooks": {}}
    for name, spec in PAYLOADS.items():
        if args.only and args.only not in name:
            continue
        path = os.path.join(HOOKS_DIR, spec["script"])
        if not os.path.isfile(path):
            out["hooks"][name] = {"error": f"没有 {path}"}
            continue
        for _ in range(args.warmup):
            run_once(spec["script"], spec["payload"])
        ms = [run_once(spec["script"], spec["payload"]) for _ in range(args.repeat)]
        out["hooks"][name] = {"event": spec["event"], "median_ms": round(median(ms), 1),
                              "min_ms": round(min(ms), 1), "max_ms": round(max(ms), 1)}

    got = {k: v for k, v in out["hooks"].items() if "median_ms" in v}
    per_call = [v["median_ms"] for k, v in got.items() if PAYLOADS[k].get("per_tool_call")]
    per_prompt = [v["median_ms"] for k, v in got.items() if PAYLOADS[k].get("per_prompt")]
    # 每次工具调用付的是 Pre + Post 之**和**（一前一后，串行）
    out["per_tool_call_ms"] = round(sum(per_call), 1) if per_call else None
    # UserPromptSubmit 的几条是**并发**跑的（cc_hooks.rs:636），付最慢那条
    out["per_prompt_ms"] = round(max(per_prompt), 1) if per_prompt else None
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
