#!/usr/bin/env python3
"""I2 · 冒烟一眼看 —— 跑完一次之后，只看「该看的三件事」。

    python3 tools/eval/smoke_peek.py docs/eval/i2/smoke-opt

三件事，对应三项优化各自最容易悄悄失效的地方：

  1. **模型有没有真的去用组合工具**（`mcp__hcapack__*`）—— 组合工具挂上了但
     人设没把模型引过去的话，步数一点都不会降，而 MCP 状态看起来一切正常。
  2. **工具白名单有没有生效** —— 它是 llm-shim 的环境变量，只重建 daemon 的话
     会带着上一个阶段的值跑，daemon 侧看不出任何异常。
  3. **会话自动起名关掉没有** —— 关掉之后一轮对话的上游请求数应当等于轮数。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("用法：smoke_peek.py <批次目录>", file=sys.stderr)
        return 2
    d = Path(argv[0])
    files = sorted(f for f in d.glob("*-atomcode-*.json") if not f.name.endswith(".raw.json"))
    if not files:
        print(f"[冒烟] {d} 里没有 HCA 侧的运行记录 —— 这一跑就没成", file=sys.stderr)
        return 1
    rc = 0
    for f in files:
        rec = json.loads(f.read_text(encoding="utf-8"))
        if rec.get("skipped") or rec.get("error"):
            print(f"[冒烟] {f.stem}：未完成 —— {rec.get('error')}")
            rc = 1
            continue
        calls = [c.get("tool") for c in (rec.get("calls") or [])]
        print(f"[冒烟] {f.stem}：墙钟 {rec.get('wall_ms')} ms · 轮数 {rec.get('rounds')} · "
              f"工具 {len(calls)} 次 → {calls}")
        if not any((t or "").startswith("mcp__hcapack__") for t in calls):
            print("[冒烟]   ⚠ 一次组合工具都没调 —— 步数不会降，先去看人设第五节")
            rc = 1
        sh = f.with_name(f.stem + ".shim.jsonl")
        if not sh.is_file():
            print("[冒烟]   ⚠ 没有 shim 追踪（权限？路径？）")
            rc = 1
            continue
        rows = [json.loads(l) for l in sh.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not rows:
            print("[冒烟]   ⚠ shim 追踪是空的")
            rc = 1
            continue
        r0 = rows[0]
        print(f"[冒烟]   上游请求 {len(rows)} 条（轮数 {rec.get('rounds')}）；"
              f"首条 {r0['n_tools']} 个工具 / {r0['tools_bytes']} 字节 schema / "
              f"{(r0.get('usage') or {}).get('prompt_tokens')} 输入 token")
        if any(t["name"].startswith("atomgit_") or t["name"] in ("code_review", "recall")
               for t in r0.get("tools", [])):
            print("[冒烟]   ⚠ 工具白名单没生效（请求里还有 atomgit_/code_review/recall）")
            rc = 1
        if rec.get("rounds") and len(rows) > rec["rounds"]:
            print(f"[冒烟]   ⚠ 上游请求比轮数多 —— 会话自动起名可能还开着")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
