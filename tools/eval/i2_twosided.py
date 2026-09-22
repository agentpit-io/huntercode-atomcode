#!/usr/bin/env python3
"""I2 · **两侧同口径**的瀑布对比 —— 把墙钟差拆成「轮数 / 出字 / 工具 / 收尾」。

    python3 tools/eval/i2_twosided.py docs/eval/i2 --batches opt-b,opt-a

## 为什么要专门有这一份

报告 §1.6 与 §4.1 原来的推理是：「步数相同时 HCA 的墙钟约是社区版的 1.8～2 倍
→ 差的是引擎侧固定开销」。那一步**缺一个量** —— 社区版每一轮模型要多久，从来没量过：
HCA 侧有 SSE 逐事件的相对时刻，社区版那边的探针是一次阻塞 POST，只有一个总墙钟。
于是「引擎侧开销」成了一个由减法得到、无法证伪的余项。

现在两侧都有同口径的段落（`eval_opencode.waterfall` 从
`info.time` / `state.time` / part `time` 拼出来，46 份真实记录验过段落之和与墙钟
最大差 35 ms），可以直接把「每轮模型时间」摊开比。

## 两个输出

1. `--table`（默认）：逐题两侧的 段落分解 + 墙钟。
2. `--rounds`：把**全部运行**的每一段「模型」摊平成一个分布，两侧对比。
   这一份回答的是「HCA 的引擎是不是每轮更慢」这个问题 —— 一道题只有 2～3 轮，
   而这个通道上单轮的 p90 是中位的 1.7 倍，所以必须用分布看，不能用某一题的均值。

只读原始记录，缺哪一项写 `—`，不推算。
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_opencode import flatten as oc_flatten, waterfall as oc_waterfall  # noqa: E402

# 单段「模型」低于这个毫秒数的不进分布：并行工具之间那种几十毫秒的碎片段
# 不是一次模型往返，混进去会把中位数拉低。
MIN_MODEL_MS = 500


def stale(rec) -> bool:
    """这条记录的 HCA 侧段落切分是不是**修并行工具误算之前**的探针产的。

    判据：修好的那一版一定会写 `tool_sum_ms`（并集之外再给一个加法值）。
    没有这个字段 = 老探针 = 它的 `model_ms` / `tool_ms` 在有并行调用的题上不可信
    （q1 上 `tool_ms` 加出 54 355 ms 而墙钟只有 42 765 ms，两个 `tool_start` 之间
    还记了一段 25 535 ms 的「模型」）。**墙钟与调用次数不受影响** —— 判达标用的
    就是这两个，所以老批次的结论不用作废，只是拆分那几列要标出来。
    """
    if rec.get("side") != "atomcode":
        return False
    t = ((rec.get("_w") or {}).get("totals")) or {}
    return "tool_sum_ms" not in t


def segments_of(f: Path, rec: dict):
    """一次运行的段落。社区版那边现场从 raw.json 拼 —— 两个理由：老批次的 json 里
    还没有 `waterfall` 字段（那时 eval_opencode 还不算瀑布），而新批次里存的那份
    可能是旧口径（`tool_ms` 用加法）。现场算两头都能用上、口径还一致。"""
    if rec.get("side") == "opencode":
        # **有 raw.json 就现场重算**，不用 json 里存的那份：存下来的可能是旧口径
        # （`tool_ms` 用加法，并行调用会算重）。现场算保证同一次运行在新旧批次里
        # 口径一致；没有 raw.json 时退回存下来的那份，并按它有没有 `tool_sum_ms`
        # 就能看出是新口径还是旧口径。
        raw = f.with_name(f.name.replace(".json", ".raw.json"))
        if raw.is_file():
            return oc_waterfall(oc_flatten(json.loads(raw.read_text(encoding="utf-8"))
                                           .get("messages") or [])) or None
        return rec.get("waterfall") or None
    return rec.get("waterfall") or None


def load(root: Path, batch: str):
    out = []
    d = root / batch
    for f in sorted(d.glob("*.json")):
        if f.name == "index.json" or f.name.endswith(".raw.json"):
            continue
        rec = json.loads(f.read_text(encoding="utf-8"))
        if not rec.get("wall_ms"):
            continue
        rec["_q"] = rec["id"].rsplit("-", 1)[0].rsplit("-", 1)[0]
        rec["_w"] = segments_of(f, rec)
        out.append(rec)
    return out


def med(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(st.median(xs)) if xs else None


def f1(v, unit=" ms"):
    return "—" if v is None else f"{v:,}{unit}"


def table(root: Path, batches):
    for b in batches:
        runs = load(root, b)
        if not runs:
            print(f"（跳过：{root / b} 没有可用记录）")
            continue
        print(f"\n### 批次 `{b}`\n")
        print("| 题 | 边 | 墙钟 | 模型 | 工具 | 出字 | 收尾 | 字数 | 轮 | 调用 |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        qs = sorted({r["_q"] for r in runs})
        for q in qs:
            for side in ("atomcode", "opencode"):
                rs = [r for r in runs if r["_q"] == q and r.get("side") == side]
                if not rs:
                    continue
                t = [(r["_w"] or {}).get("totals") or {} for r in rs]
                # 老探针产的拆分标 ⚠：有并行调用的题上它的 model/tool 不可信
                mark = " ⚠" if any(stale(r) for r in rs) else ""
                # 社区版没有「最后一块文本 → 运行结束」这个可观测事件，收尾写 —
                print(f"| {q} | {'HCA' if side == 'atomcode' else '社区版'} "
                      f"| {f1(med([r['wall_ms'] for r in rs]))} "
                      f"| {f1(med([x.get('model_ms') for x in t]))}{mark} "
                      f"| {f1(med([x.get('tool_ms') for x in t]))}{mark} "
                      f"| {f1(med([x.get('stream_ms') for x in t]))} "
                      f"| {f1(med([x.get('finish_ms') for x in t]))} "
                      f"| {f1(med([r.get('text_len') for r in rs]), '')} "
                      f"| {f1(med([r.get('rounds') for r in rs]), '')} "
                      f"| {f1(med([r.get('tool_calls_stat') for r in rs]), '')} |")


def rounds(root: Path, batches, safe=True):
    """每一轮模型调用的耗时，两侧摊平成分布。

    `safe=True`（默认）**只取工具调用 ≤ 1 次的运行**。理由：只有一次调用就不可能
    并行，于是老探针的误算在这批数据上不会发生 —— 两侧都干净，可比。
    放开它（`--all-runs`）会把老批次里被污染的段落也算进来（HCA 侧会多出
    一条 32 140 ms 的「模型段」，那其实是三个并行工具在跑）。
    """
    per, kept, dropped = {}, 0, 0
    for b in batches:
        for r in load(root, b):
            if safe and (r.get("tool_calls_stat") or 0) > 1:
                dropped += 1
                continue
            kept += 1
            segs = ((r["_w"] or {}).get("segments")) or []
            xs = [s["ms"] for s in segs
                  if s.get("kind") == "model" and s.get("ms") and s["ms"] > MIN_MODEL_MS]
            per.setdefault(r.get("side"), []).extend(xs)
    scope = ("只取调用 ≤ 1 次的运行（不可能有并行，两侧都干净）"
             if safe else "全部运行（⚠ 含老探针被并行调用污染的段落）")
    print(f"\n### 每一轮模型调用的耗时分布 —— {scope}\n")
    print(f"取了 {kept} 次运行" + (f"，按上面的口径排除 {dropped} 次。\n" if dropped else "。\n"))
    print("| 边 | 段数 | 中位 | p10 | p90 | 最大 | > 8 s 的占比 |")
    print("|---|---|---|---|---|---|---|")
    for side, name in (("atomcode", "HCA"), ("opencode", "社区版")):
        xs = sorted(per.get(side) or [])
        if not xs:
            continue
        n = len(xs)
        print(f"| {name} | {n} | {st.median(xs):,.0f} ms | {xs[n // 10]:,.0f} ms "
              f"| {xs[min(n - 1, int(n * 0.9))]:,.0f} ms | {xs[-1]:,.0f} ms "
              f"| {sum(1 for x in xs if x > 8000) / n:.1%} |")


def paired(root: Path, batches):
    """**配对**比首轮模型耗时：每个「批次 × 题」各取两侧的中位数，再看差值的分布。

    为什么不能只看把全部段落摊平的那张分布表：那张表混了首轮与后续轮、混了不同题，
    两侧的题目构成还不一样（社区版少跑几轮），中位数撞在一起不等于「每轮一样快」。
    配对之后每一对的机器状态、网关状态、题目都相同，差值才是可比的。

    只用**首轮**（t=0 → 第一个 text/tool_start）：这一段两侧的语义完全一致 ——
    从消息进来到模型第一个可见输出，HCA 这边额外装着 UserPromptSubmit hook、
    llm-shim 一跳、更大的提示。后续轮的提示里还混着两边大小不同的工具返回，
    不是一个干净的对照。
    """
    rows = []
    for b in batches:
        runs = load(root, b)
        for q in sorted({r["_q"] for r in runs}):
            side_med = {}
            for side in ("atomcode", "opencode"):
                xs = []
                for r in runs:
                    if r["_q"] != q or r.get("side") != side:
                        continue
                    segs = ((r["_w"] or {}).get("segments")) or []
                    first = next((s for s in segs if s["kind"] == "model" and s.get("ms")), None)
                    if first:
                        xs.append(first["ms"])
                if xs:
                    side_med[side] = st.median(xs)
            if len(side_med) == 2:
                rows.append((b, q, side_med["atomcode"], side_med["opencode"]))
    if not rows:
        print("（没有可配对的记录）")
        return
    print("\n### 首轮模型耗时 —— 按「批次 × 题」配对\n")
    print("| 批次 | 题 | HCA 中位 | 社区版 中位 | 差 |")
    print("|---|---|---|---|---|")
    for b, q, a, o in rows:
        print(f"| {b} | {q} | {a:,.0f} ms | {o:,.0f} ms | **{a - o:+,.0f} ms** |")
    d = sorted(a - o for _, _, a, o in rows)
    print(f"\n**差值中位 {st.median(d):+,.0f} ms**；{len(d)} 对里 "
          f"HCA 更慢 {sum(1 for x in d if x > 0)} 对、更快 {sum(1 for x in d if x < 0)} 对。")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--batches", default="")
    ap.add_argument("--rounds", action="store_true", help="只出「每轮模型耗时」分布")
    ap.add_argument("--paired", action="store_true",
                    help="只出「首轮模型耗时」的配对比较（批次 × 题）")
    ap.add_argument("--all-runs", action="store_true",
                    help="分布里连多工具的运行一起算（⚠ 老批次的段落会被并行调用污染）")
    a = ap.parse_args(argv)
    batches = [x for x in a.batches.split(",") if x] or \
              sorted(p.name for p in a.root.iterdir()
                     if p.is_dir() and not p.name.startswith("_"))
    if a.paired:
        paired(a.root, batches)
    elif a.rounds:
        rounds(a.root, batches, safe=not a.all_runs)
    else:
        table(a.root, batches)
        rounds(a.root, batches, safe=not a.all_runs)
        paired(a.root, batches)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
