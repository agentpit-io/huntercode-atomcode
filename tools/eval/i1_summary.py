#!/usr/bin/env python3
"""I1 · 两条线 × 十道题的汇总 —— 报告里的表和 `summary.json` 都由它生成，不手抄。

    python3 tools/eval/i1_summary.py docs/eval/c1/raw \
        --scores docs/eval/c1/scores-c1-a.json docs/eval/c1/scores-c1-b.json \
        --out docs/eval/c1/summary.json --md docs/eval/c1/tables.md

批次目录名就是线名：`c1-a`（A 线 · 纯引擎，两边同样 6 个 MCP）、
`c1-b`（B 线 · 产品形态，HCA 全量 vs 社区版 6 个）。

**每一个数字都带出处**：`summary.json` 里每个指标旁边有 `source`，写的是
算它用到的原始文件的 glob（例如 `docs/eval/c1/raw/c1-b/q1-fundamental-atomcode-r*.json`），
拿着它可以重算。这是给 PPT 取数用的 —— 取数的人不该再去翻报告正文。

D1 / D2 的口径与 M2 评分表、`tools/eval/i2_compare.py` 完全一致（直接 import，
不另写一份）：base = 两边中位数里较小的那个，D1 = min(1, base/自己) × 13，D2 同理 × 12。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from questions import ALL_QUESTIONS, turns_of  # noqa: E402
from i2_compare import median, median0, d_scores  # noqa: E402

QIDS = [q["id"] for q in ALL_QUESTIONS]
QBYID = {q["id"]: q for q in ALL_QUESTIONS}
SIDES = ("atomcode", "opencode")
SIDE_CN = {"atomcode": "HCA（AtomCode 版）", "opencode": "社区版（opencode 版 1.2.0）"}


def ttft_ms(rec: dict) -> float | None:
    """首字延迟 = 这一次运行里**第一块正文**到达的相对毫秒。

    两边的原始结构不同，取的是同一件事：
      · HCA：瀑布里第一个 `text_start=True` 的模型段的 `end_ms`（= 第一个 text 事件到达）
      · 社区版：瀑布里第一个 `stream` 段的 `start_ms`（= 第一个 text part 的开始时刻）
    多轮题（q9）取**第 1 轮**的首字延迟 —— 用户等的第一口字就是那一次。
    取不到返回 None，不拿别的数顶。
    """
    # 多轮题一律取**第 1 轮**的 —— 整批那张瀑布没有统一时间轴：
    # HCA 侧每轮 t0 重置，社区版侧整批的 t0 取的是整个会话的第一条消息，
    # 于是第 2、3 轮的 text part 会算出负的相对毫秒（实测 −30 083 ms）。
    turns = rec.get("turns") or []
    if turns:
        t0 = dict(turns[0])
        t0.setdefault("side", rec.get("side"))
        return ttft_ms(t0)
    wf = rec.get("waterfall")
    if not isinstance(wf, dict) or not wf.get("segments"):
        return None
    segs = wf["segments"]
    if rec.get("side") == "opencode" or any(s.get("kind") == "stream" for s in segs):
        xs = [s["start_ms"] for s in segs
              if s.get("kind") == "stream" and isinstance(s.get("start_ms"), (int, float))]
    else:
        xs = [s["end_ms"] for s in segs
              if s.get("text_start") and isinstance(s.get("end_ms"), (int, float))]
    return min(xs) if xs else None


def load_batch(batch: Path) -> dict:
    """(题, 边) → 每次运行的明细列表。**排除的运行也留下来**，写明原因。"""
    runs, excluded = {}, []
    for f in sorted(batch.glob("*.json")):
        if f.name in ("index.json", "scores.json") or f.name.endswith(".raw.json"):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            excluded.append({"file": str(f), "why": f"JSON 解析失败：{e}"})
            continue
        side = d.get("side") or ("atomcode" if "-atomcode-" in f.name else "opencode")
        qid = f.stem.rsplit(f"-{side}-", 1)[0]
        if d.get("skipped") or d.get("error"):
            excluded.append({"file": str(f), "question": qid, "side": side,
                             "why": d.get("error") or "skipped=true"})
            continue
        d["_file"] = str(f)
        runs.setdefault((qid, side), []).append(d)
    return {"runs": runs, "excluded": excluded}


def stat_block(batch_dir: Path, recs: list, qid: str, side: str) -> dict:
    calls = [len(r.get("calls") or []) for r in recs]
    src = f"{batch_dir}/{qid}-{side}-r*.json"
    return {
        "n": len(recs),
        "source": src,
        "tool_calls_median": median0(calls),
        "tool_calls_each": calls,
        "wall_ms_median": median([r.get("wall_ms") for r in recs]),
        "wall_ms_each": [r.get("wall_ms") for r in recs],
        "token_median": median([r.get("quota_delta") for r in recs]),
        "token_each": [r.get("quota_delta") for r in recs],
        "rounds_median": median([r.get("rounds") for r in recs]),
        "ttft_ms_median": median([ttft_ms(r) for r in recs]),
        "ttft_ms_each": [ttft_ms(r) for r in recs],
        "text_len_median": median([r.get("text_len") for r in recs]),
        "tools_used": sorted({c.get("tool") for r in recs for c in (r.get("calls") or [])
                              if c.get("tool")}),
    }


def load_scores(paths: list) -> dict:
    """人工 + 自动评分（score.py report 的产物）→ (批次, 题, 边) → 四维得分。"""
    out = {}
    for p in paths or []:
        p = Path(p)
        if not p.is_file():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        batch = d.get("batch") or p.stem.replace("scores-", "")
        for r in d.get("runs", []):
            key = (batch, r.get("question"), r.get("side"))
            out.setdefault(key, []).append(r)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("raw", type=Path, help="docs/eval/c1/raw")
    ap.add_argument("--lines", nargs="*", default=["c1-a", "c1-b"])
    ap.add_argument("--scores", nargs="*", default=[])
    ap.add_argument("--out", type=Path, default=Path("docs/eval/c1/summary.json"))
    ap.add_argument("--md", type=Path, default=None)
    args = ap.parse_args(argv)

    scores = load_scores(args.scores)
    summary = {
        "_说明": "I1 对比测试的机器可读汇总。每个指标的 source 是算它用到的原始文件 glob，"
                 "拿着它可以重算。数字全部来自真实调用，取不到写 null。",
        "生成命令": "python3 tools/eval/i1_summary.py " + str(args.raw),
        "题目": {q["id"]: {"类型": q["kind"], "轮数": len(turns_of(q)),
                           "需要的数据类别": q.get("needs"),
                           "允许零工具": bool(q.get("no_tool_ok"))}
                 for q in ALL_QUESTIONS},
        "线": {},
    }
    md = []

    for line in args.lines:
        bdir = args.raw / line
        if not bdir.is_dir():
            continue
        data = load_batch(bdir)
        runs = data["runs"]
        # i2_compare 的口径：(题,边) → 中位数三件套
        m = {}
        for (qid, side), recs in runs.items():
            m[(qid, side)] = {
                "n": len(recs),
                "calls": median([len(r.get("calls") or []) for r in recs]),
                "calls0": median0([len(r.get("calls") or []) for r in recs]),
                "wall": median([r.get("wall_ms") for r in recs]),
                "token": median([r.get("quota_delta") for r in recs]),
                "rounds": median([r.get("rounds") for r in recs]),
            }
        line_out = {"批次目录": str(bdir), "排除的运行": data["excluded"], "题": {}}
        md.append(f"\n### 批次 `{line}`\n")
        md.append("| 题 | 边 | 次数 | 工具调用(中位) | 墙钟(中位) | token(中位) | 首字(中位) | D1 | D2 |")
        md.append("|---|---|---|---|---|---|---|---|---|")
        for qid in QIDS:
            if not any((qid, s) in runs for s in SIDES):
                continue
            d = d_scores(m, qid)
            q_out = {}
            for side in SIDES:
                recs = runs.get((qid, side)) or []
                if not recs:
                    q_out[side] = {"n": 0, "why": "这个批次里没有这一边的有效记录"}
                    continue
                blk = stat_block(bdir, recs, qid, side)
                blk["D1"] = d.get(side, {}).get("D1")
                blk["D2"] = d.get(side, {}).get("D2")
                sc = scores.get((line, qid, side)) or []
                if sc:
                    for dim in ("A", "B", "C", "D", "total"):
                        vals = [r.get(dim) for r in sc if isinstance(r.get(dim), (int, float))]
                        blk[f"{dim}_median"] = round(statistics.median(vals), 1) if vals else None
                q_out[side] = blk
                md.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                    qid, SIDE_CN[side], blk["n"],
                    blk["tool_calls_median"],
                    f'{blk["wall_ms_median"]/1000:.1f} s' if blk["wall_ms_median"] else "—",
                    f'{blk["token_median"]:,.0f}' if blk["token_median"] else "—",
                    f'{blk["ttft_ms_median"]/1000:.1f} s' if blk["ttft_ms_median"] else "—",
                    blk["D1"], blk["D2"]))
            line_out["题"][qid] = q_out
        # 线级合计：D1/D2 按题求平均（与 I2 报告同口径）
        for side in SIDES:
            d1 = [v[side]["D1"] for v in line_out["题"].values()
                  if v.get(side, {}).get("D1") is not None]
            d2 = [v[side]["D2"] for v in line_out["题"].values()
                  if v.get(side, {}).get("D2") is not None]
            line_out.setdefault("合计", {})[side] = {
                "D1_均值": round(statistics.mean(d1), 1) if d1 else None,
                "D2_均值": round(statistics.mean(d2), 1) if d2 else None,
                "D_均值": round(statistics.mean(d1) + statistics.mean(d2), 1) if d1 and d2 else None,
                "题数": len(d1),
                "source": f"{bdir}/*-{side}-r*.json",
            }
        summary["线"][line] = line_out

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写 {args.out}")
    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text("\n".join(md) + "\n", encoding="utf-8")
        print(f"已写 {args.md}")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
