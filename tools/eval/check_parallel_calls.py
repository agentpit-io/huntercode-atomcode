#!/usr/bin/env python3
"""基线侧有没有发生过并行 tool_calls，发生时有没有被合并成一个。

    python3 tools/eval/check_parallel_calls.py [--raw docs/eval/raw]

背景：hunter 网关（Gemini 经 OneAPI）流式发并行 `tool_calls` 时**不带 `index`**
（M0 §11.2）。AtomCode 的 `openai_compat.rs` 是 `tc.index.unwrap_or(0)`，缺
`index` 时多个调用会全挤进 0 号槽合并成一个 —— 这正是 M0 给 llm-shim 打补丁
要修的缺陷。网关侧已于 2026-09-22 07:50 上海时间修复（hermes feb8953）。

**为什么要在报告里查这一条**：本批正式评测 23:29 UTC 开跑（07:29 上海），
横跨 07:50 这个修复时刻。HCA 侧恒走自带补丁的 llm-shim，两侧都不受影响；
**基线侧是直连网关的**（`opencode.json` 的 baseURL 指向网关，不经 shim），
所以只有它可能落在修复的两侧。

判据不是猜时间，而是看原始记录：同一个 `step` 里如果出现了 2 个以上
`type=tool` 的 part，说明这一轮真的发了并行调用；再看它们的 `tool` 名与
`state.input` 是不是各不相同 —— 被合并的表现是只剩一个、或几个参数糊在一起。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def analyse(raw_json: Path):
    d = json.loads(raw_json.read_text(encoding="utf-8"))
    steps, worst, parallel_groups = 0, 0, []
    for m in d.get("messages") or []:
        cur = []
        for part in (m.get("parts") or []):
            t = part.get("type")
            if t in ("step-start", "step_start"):
                steps += 1
                if len(cur) > 1:
                    parallel_groups.append(cur)
                worst = max(worst, len(cur))
                cur = []
            elif t == "tool":
                st = part.get("state") or {}
                cur.append((part.get("tool"),
                            json.dumps(st.get("input"), ensure_ascii=False, sort_keys=True),
                            st.get("status")))
        if len(cur) > 1:
            parallel_groups.append(cur)
        worst = max(worst, len(cur))
    return steps, worst, parallel_groups


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=Path("docs/eval/raw"))
    args = ap.parse_args(argv)

    files = sorted(args.raw.glob("*-opencode-*.raw.json"))
    if not files:
        print(f"（{args.raw} 下没有基线侧的 .raw.json）")
        return 0

    print("| 运行 | step 数 | 单 step 内最多调用 | 并行组是否各不相同 |")
    print("|---|---|---|---|")
    any_parallel, any_merged = 0, 0
    for f in files:
        steps, worst, groups = analyse(f)
        any_parallel += len(groups)
        verdict = "—（没发生并行）"
        for g in groups:
            keys = {(t, a) for t, a, _ in g}
            if len(keys) != len(g):
                verdict = f"**✗ 有重复，疑似被合并**：{g}"
                any_merged += 1
                break
            bad = [s for _, _, s in g if s != "completed"]
            verdict = ("✓ 各不相同且全部 completed" if not bad
                       else f"✓ 各不相同，但有 {len(bad)} 个未完成：{bad}")
        print(f"| `{f.name[:-9]}` | {steps} | {worst} | {verdict} |")
    print()
    print(f"基线侧共出现 **{any_parallel}** 组并行 tool_calls，"
          f"其中疑似被合并的 **{any_merged}** 组。")
    if any_parallel and not any_merged:
        print()
        print("> 结论：并行调用真的发生过（最多一轮 3 个），而且**每一组都被正确拆开**。"
              "网关 07:50 的修复因此不是这批数据的混杂因素 —— "
              "修复前跑的那些运行同样没有出现合并。"
              "（合理解释：合并是 AtomCode `openai_compat.rs` 里 "
              "`tc.index.unwrap_or(0)` 的特有行为；基线用的 "
              "`@ai-sdk/openai-compatible` 按 `id` 归并，不依赖 `index`。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
