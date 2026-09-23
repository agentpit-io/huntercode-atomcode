#!/usr/bin/env python3
"""I4 · 响应时间专项评测的汇总 —— 报告里的表和 `summary.json` 都由它生成，不手抄。

    python3 tools/eval/i4_summary.py docs/eval/i4/i4-b docs/eval/i4/i4-a \\
        docs/eval/i4/idle-b docs/eval/i4/idle-a \\
        --hook-bench docs/eval/i4/hook-bench.json \\
        --out docs/eval/i4/summary.json --md docs/eval/i4/tables.md

批次目录名就是批次名（`i4-b` = 产品形态，`i4-a` = 两边同样 6 个 MCP，
`idle-*` = 纯引擎空载题）。**每个指标都带 `source`**，写的是算它用到的原始文件的
glob，拿着它可以重算 —— 这份是给 PPT 取数用的，取数的人不该再去翻报告正文。

## 分段口径（两侧必须是同一件事，否则比的是口径不是引擎）

一次运行被切成四段，**HCA 侧四段之和 == 墙钟**（探针里有单测钉着）：

| 段 | 定义 | HCA | 社区版 |
|---|---|---|---|
| 首字延迟 TTFT | 消息发出 → 第一块正文到达 | 瀑布里第一个 `text_start` 段的 `end_ms` | 第一个 `stream` 段的 `start_ms` |
| 模型段 | 不在跑工具、也不在出末轮正文的时间 | `totals.model_ms` | `totals.model_ms` **+ 非末轮的 `stream` 段**（见下） |
| 工具段 | 全部工具区间的**并集** | `totals.tool_ms` | `totals.tool_ms` |
| 出字段 | 末轮第一块正文开始 → 最后一块正文结束 | `totals.stream_ms` | 末轮 `stream` 段的 `max(end) − min(start)` |
| 收尾段 | 最后一块正文 → 运行结束 | `totals.finish_ms` | **量不到**，只能给上界（= 残差，见下） |

**为什么社区版的模型段要把「非末轮的正文」折进去**：HCA 的瀑布里，一轮中间
吐出来的正文（本部署人设要求的「路标」）没有单独成段，它落在模型段里；
社区版那边每个 text part 都单独成了 `stream` 段。不折进去的话，
「HCA 的模型段更长」就成了口径差造成的假象。

**残差** = 墙钟 − （模型 + 工具 + 出字 + 收尾）。HCA 侧实测 |残差| < 1 ms；
社区版侧收尾段量不到，残差就是它的**上界**（I1 的记录上实测 24～32 ms）。
报告里社区版的收尾写「≤ 残差」，不写 0，也不写 null —— 0 是猜的，null 丢掉了已知信息。

## 「原样」与「都真做题」两套数

I1 §3.11 记过：`q2` 上社区版有几次**没做这道题** —— 反过来要用户把论点贴出来
（而 `theses/601088.md` 就在它自己的工作区里）。这种运行又快又短，混进中位数里
会让对照组显得很快。所以每道题给两套数：

  · **原样**：全部有效运行，一次不剔；
  · **都真做题**：剔掉「把任务退回给用户」的运行。

判据是**事前写死、与耗时无关**的一条正则（`PUNT_BACK`），只认「向用户索要材料」
这一类措辞，并且**不作用于 `q4` / `q10`** —— 那两道题的正确答案本来就可能是
「拿不到」「我不做」。在 I1 的 108 次运行上试过：命中 3 次，正好是 §3.11 点名的
那 3 次，没有误伤。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from questions import ALL_QUESTIONS, IDLE_QUESTIONS  # noqa: E402

QBYID = {q["id"]: q for q in ALL_QUESTIONS + IDLE_QUESTIONS}
SIDES = ("atomcode", "opencode")
SIDE_CN = {"atomcode": "HCA（AtomCode 版）", "opencode": "社区版（opencode 版 1.2.0）"}

# 「把任务退回给用户」——「没做题」的判据。见模块 docstring。
PUNT_BACK = re.compile(
    r"请(?:提供|把|将|您?把|您?将)|尚未.{0,8}提供|"
    r"未(?:读取|获取|找)到.{0,12}(?:论点|理由|笔记|文本)|发给我|请您(?:提供|发)")
# 这两道题的正确答案本来就可能是「拿不到 / 我不做」，不套上面那条判据
PUNT_EXEMPT = {"q4-kronos-forecast", "q10-refusal"}


# ── 统计 ────────────────────────────────────────────────────────────────────
def _clean(xs):
    return [x for x in xs if isinstance(x, (int, float))]


# 统一保留 3 位小数：ms 级的数看不出差别，而 `ms_per_char` 只有 3 位才不失真。
# **P90 不做任何舍入**（见 p90 的注释）。
ND = 3


def median(xs):
    xs = _clean(xs)
    return round(statistics.median(xs), ND) if xs else None


def p90(xs):
    """最近秩法：排序后取第 ceil(0.9n) 个。

    **不用插值分位数**。n=6 时插值会在两个实测点之间造出一个谁都没量到的数；
    最近秩法取到的永远是一次真实运行的值。代价是 n 小的时候 P90 会等于最大值 ——
    这一点报告里写明，不假装它是个稳定分位数。

    **返回值一个字都不舍入**：舍入过的 9.8 不是任何一次运行量到的数，
    而这个函数的全部意义就是「它是一次真实运行的值」（单测钉着这一条）。
    """
    xs = sorted(_clean(xs))
    if not xs:
        return None
    return xs[min(len(xs) - 1, math.ceil(0.9 * len(xs)) - 1)]


def block(xs):
    xs_c = _clean(xs)
    return {"n": len(xs_c), "median": median(xs), "p90": p90(xs),
            "min": min(xs_c) if xs_c else None,
            "max": max(xs_c) if xs_c else None,
            "each": [round(x, ND) if isinstance(x, (int, float)) else None for x in xs]}


def ratio(a, b):
    """a / b，任一为空或 b=0 就返回 None —— 不拿 0 顶。"""
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or b == 0:
        return None
    return round(a / b, 3)


# ── 单次运行 → 统一口径的分段 ───────────────────────────────────────────────
def _totals_and_segments(rec):
    wf = rec.get("waterfall") or {}
    return (wf.get("totals") or {}), (wf.get("segments") or [])


def _last_tool_end(segs):
    return max([s["end_ms"] for s in segs
                if s.get("kind") == "tool" and isinstance(s.get("end_ms"), (int, float))],
               default=0.0)


def _one_turn(rec, side):
    """一轮（或单轮题的整次运行）→ 统一口径的四段 + 首字。"""
    tot, segs = _totals_and_segments(rec)
    if not segs:
        return None
    out = {"model_ms": tot.get("model_ms"), "tool_ms": tot.get("tool_ms")}
    lt = _last_tool_end(segs)
    if side == "opencode":
        streams = [s for s in segs if s.get("kind") == "stream"]
        last = [s for s in streams if isinstance(s.get("start_ms"), (int, float))
                and s["start_ms"] >= lt]
        out["stream_ms"] = (round(max(s["end_ms"] for s in last)
                                  - min(s["start_ms"] for s in last), 1)
                            if last else None)
        out["ttft_answer_ms"] = round(min(s["start_ms"] for s in last), 1) if last else None
        # 非末轮的正文折进模型段（口径对齐，理由见 docstring）
        inter = sum(s["ms"] for s in streams
                    if isinstance(s.get("ms"), (int, float))
                    and isinstance(s.get("start_ms"), (int, float)) and s["start_ms"] < lt)
        if isinstance(out["model_ms"], (int, float)):
            out["model_ms"] = round(out["model_ms"] + inter, 1)
        out["finish_ms"] = None           # 量不到，见 docstring
        xs = [s["start_ms"] for s in segs if s.get("kind") == "stream"
              and isinstance(s.get("start_ms"), (int, float))]
    else:
        out["stream_ms"] = tot.get("stream_ms")
        out["finish_ms"] = tot.get("finish_ms")
        # 末轮第一块正文的时刻 = 墙钟 − 收尾段那一段（tail = 出字 + 收尾）
        wall_ = rec.get("wall_ms")
        tail_ = tot.get("tail_ms")
        out["ttft_answer_ms"] = (round(wall_ - tail_, 1)
                                 if isinstance(wall_, (int, float))
                                 and isinstance(tail_, (int, float)) else None)
        xs = [s["end_ms"] for s in segs
              if s.get("text_start") and isinstance(s.get("end_ms"), (int, float))]
    out["ttft_ms"] = round(min(xs), 1) if xs else None
    out["wall_ms"] = rec.get("wall_ms")
    # 首字是不是「路标」而不是答案正文：第一块正文早于第一次工具调用，
    # 而这次运行确实调了工具 —— 那用户先看到的是一行进度提示，不是答案。
    # 本部署的人设要求模型在一批工具调用前先发一行路标（`## PROGRESS SIGNPOSTS`），
    # 所以 HCA 侧的首字延迟里有一部分是**产品设计**，不是引擎更快。写出来，别藏着。
    first_tool = min([s["start_ms"] for s in segs if s.get("kind") == "tool"
                      and isinstance(s.get("start_ms"), (int, float))], default=None)
    out["ttft_is_signpost"] = (bool(first_tool is not None
                                    and out["ttft_ms"] is not None
                                    and out["ttft_ms"] < first_tool))
    return out


def segments_of(rec):
    """整次运行 → 统一口径的分段。多轮题把各轮相加，首字取**第 1 轮**的。

    多轮题为什么这么算：用户等的第一口字就是第 1 轮那一次；而「一共等了多久」
    是三轮之和。两边同样处理。
    """
    side = rec.get("side")
    turns = rec.get("turns") or []
    if turns:
        per = []
        for t in turns:
            t = dict(t)
            t.setdefault("side", side)
            r = _one_turn(t, side)
            if r:
                per.append(r)
        if not per:
            return None
        out = {}
        for k in ("model_ms", "tool_ms", "stream_ms", "finish_ms"):
            vals = [p[k] for p in per]
            out[k] = round(sum(v for v in vals if isinstance(v, (int, float))), 1) \
                if any(isinstance(v, (int, float)) for v in vals) else None
        out["ttft_ms"] = per[0]["ttft_ms"]
        out["ttft_answer_ms"] = per[0].get("ttft_answer_ms")
        out["ttft_is_signpost"] = per[0].get("ttft_is_signpost")
        out["wall_ms"] = rec.get("wall_ms")
    else:
        out = _one_turn(rec, side)
        if out is None:
            return None
    wall = out.get("wall_ms")
    acc = sum(out.get(k) or 0 for k in ("model_ms", "tool_ms", "stream_ms", "finish_ms"))
    out["residual_ms"] = round(wall - acc, 1) if isinstance(wall, (int, float)) else None
    # 每字耗时：出字段 / 正文字数。正文为空就不给，不拿 0 顶。
    tl = rec.get("text_len")
    out["ms_per_char"] = (round(out["stream_ms"] / tl, 3)
                          if isinstance(out.get("stream_ms"), (int, float))
                          and isinstance(tl, int) and tl > 0 else None)
    return out


def did_the_work(rec, qid) -> bool:
    """这一次运行有没有「真做题」。判据见模块 docstring。"""
    if qid in PUNT_EXEMPT:
        return True
    return not PUNT_BACK.search(rec.get("text") or "")


# ── 批次装载 ────────────────────────────────────────────────────────────────
def load_batch(batch: Path) -> dict:
    """(题, 边) → 每次运行。**排除的运行也留下来**，写明原因。"""
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


METRICS = [("ttft_ms", "首字延迟"), ("ttft_answer_ms", "答案首字延迟"),
           ("wall_ms", "总墙钟"), ("model_ms", "模型段"),
           ("tool_ms", "工具段"), ("stream_ms", "出字段"), ("finish_ms", "收尾段"),
           ("residual_ms", "残差"), ("ms_per_char", "每字耗时")]


def stat_block(batch_dir: Path, recs: list, qid: str, side: str) -> dict:
    segs = [segments_of(r) for r in recs]
    out = {"n": len(recs),
           "source": f"{batch_dir}/{qid}-{side}-r*.json",
           "files": [Path(r["_file"]).name for r in recs]}
    for key, _cn in METRICS:
        out[key] = block([(s or {}).get(key) for s in segs])
    out["rounds"] = block([r.get("rounds") for r in recs])
    out["tool_calls"] = block([len(r.get("calls") or []) for r in recs])
    out["text_len"] = block([r.get("text_len") for r in recs])
    out["token"] = block([r.get("quota_delta") for r in recs])
    out["tools_used"] = sorted({c.get("tool") for r in recs for c in (r.get("calls") or [])
                                if c.get("tool")})
    out["首字是路标的次数"] = sum(1 for s in segs if (s or {}).get("ttft_is_signpost"))
    return out


def batch_meta(batch_dir: Path) -> dict:
    idx_p = batch_dir / "index.json"
    if not idx_p.is_file():
        return {}
    idx = json.loads(idx_p.read_text(encoding="utf-8"))
    loads = [r.get("loadavg_before", [None])[0] for r in idx.get("runs", [])]
    proof = batch_dir / "same-db-proof.txt"
    return {
        "runs_recorded": len(idx.get("runs", [])),
        "finished_at": idx.get("finished_at"),
        "total_elapsed_s": idx.get("total_elapsed_s"),
        "loadavg_before_median": median(loads),
        "loadavg_before_max": max(_clean(loads)) if _clean(loads) else None,
        "quota_at_end": idx.get("quota_at_end"),
        "stopped_for_quota_at": idx.get("stopped_for_quota_at"),
        "stopped_for_mcp_at": idx.get("stopped_for_mcp_at"),
        "same_db_proof": str(proof) if proof.is_file() else None,
        "same_db_proof_ok": ("全部成立" in proof.read_text(encoding="utf-8", errors="replace")
                             if proof.is_file() else None),
        "source": str(idx_p),
    }


def build(batch_dirs, hook_bench: Path | None) -> dict:
    out = {
        "说明": "I4 · 响应时间专项评测。每个指标旁边的 source 是算它用到的原始文件，"
                "拿着它可以重算。单位一律毫秒；取不到写 null，不猜。",
        "分段口径": {
            "首字延迟 ttft_ms": "消息发出 → 第一块正文到达（多轮题取第 1 轮）。"
                                "⚠ 本部署的人设要求模型在一批工具调用前先发一行「路标」，"
                                "所以这一块正文可能是进度提示而不是答案 —— "
                                "每个格子旁边的「首字是路标的次数」标出了有几次是这样",
            "答案首字延迟 ttft_answer_ms": "消息发出 → **末轮**第一块正文开始（= 真正的答案"
                                           "开始出字）。两边同一口径，不受路标影响",
            "模型段 model_ms": "不在跑工具、也不在出末轮正文的时间；"
                               "社区版侧已把非末轮的正文块折进来对齐口径",
            "工具段 tool_ms": "全部工具区间的并集（并行调用不重复计）",
            "出字段 stream_ms": "末轮第一块正文开始 → 最后一块正文结束",
            "收尾段 finish_ms": "最后一块正文 → 运行结束；社区版侧量不到（阻塞 POST，"
                                "没有独立的结束事件），写 null，其上界见 residual_ms",
            "残差 residual_ms": "墙钟 − 四段之和。HCA 侧应当 ≈ 0；"
                                "社区版侧就是它收尾段的上界",
            "每字耗时 ms_per_char": "出字段 / 正文字数",
            "P90": "最近秩法（排序后第 ceil(0.9n) 个），不插值；n=6 时等于最大值",
        },
        "两套数": {
            "原样": "全部有效运行",
            "都真做题": "剔掉「把任务退回给用户」的运行（判据 PUNT_BACK，"
                        "不作用于 q4 / q10）",
        },
        "batches": {}, "hook": None,
    }
    for bd in batch_dirs:
        bd = Path(bd)
        loaded = load_batch(bd)
        meta = batch_meta(bd)
        qids = sorted({k[0] for k in loaded["runs"]})
        b = {"dir": str(bd), "meta": meta, "excluded": loaded["excluded"],
             "questions": {}}
        for qid in qids:
            q = {"原样": {}, "都真做题": {}, "比值": {}}
            for cut in ("原样", "都真做题"):
                for side in SIDES:
                    recs = loaded["runs"].get((qid, side)) or []
                    if cut == "都真做题":
                        recs = [r for r in recs if did_the_work(r, qid)]
                    if recs:
                        q[cut][side] = stat_block(bd, recs, qid, side)
                # 比值：HCA / 社区版，中位数与 P90 各一份
                a, o = q[cut].get("atomcode"), q[cut].get("opencode")
                if a and o:
                    q["比值"][cut] = {
                        key: {"median": ratio(a[key]["median"], o[key]["median"]),
                              "p90": ratio(a[key]["p90"], o[key]["p90"])}
                        for key, _cn in METRICS}
            # 两套数一不一样，一眼能看出来
            q["剔除了几次"] = {
                side: (len(q["原样"].get(side, {}).get("files", []))
                       - len(q["都真做题"].get(side, {}).get("files", [])))
                for side in SIDES}
            b["questions"][qid] = q
        out["batches"][bd.name] = b
    if hook_bench and Path(hook_bench).is_file():
        out["hook"] = json.loads(Path(hook_bench).read_text(encoding="utf-8"))
        out["hook"]["source"] = str(hook_bench)
        out["hook"]["说明"] = (
            "hook 段在 SSE 上和模型调用 / MCP 往返混在一段里，分不开，"
            "所以是**单独直测**的（tools/eval/hook_bench.py 在 daemon 容器里按 hook 真实的"
            "调用方式跑 N 次取中位数）。一次运行要付的 hook = per_prompt_ms + "
            "工具调用次数 × per_tool_call_ms —— 这是**推算**，不是从那次运行里量到的，"
            "报告里按推算标注。社区版没有 hook 机制，这一段不适用（不是 0，是没有这回事）。")
    return out


# ── Markdown 表 ─────────────────────────────────────────────────────────────
def fmt(v, unit="ms"):
    if v is None:
        return "—"
    if unit == "s":
        return f"{v / 1000:.2f}"
    if unit == "x":
        return f"{v:.2f}"
    return f"{v:,.0f}"


def to_md(summary: dict) -> str:
    md = ["# I4 · 响应时间评测数据表（由 tools/eval/i4_summary.py 生成，勿手改）", ""]
    for bname, b in summary["batches"].items():
        meta = b["meta"]
        md.append(f"## 批次 `{bname}`")
        md.append("")
        md.append(f"- 目录：`{b['dir']}`；记录 {meta.get('runs_recorded', '—')} 次；"
                  f"收跑 {meta.get('finished_at', '—')}；"
                  f"总耗时 {meta.get('total_elapsed_s', '—')} s")
        md.append(f"- 开跑前 1 分钟负载：中位 {meta.get('loadavg_before_median', '—')}、"
                  f"最大 {meta.get('loadavg_before_max', '—')}")
        md.append(f"- 同库证据：{meta.get('same_db_proof') or '—'}"
                  f"（{'全部成立' if meta.get('same_db_proof_ok') else '未通过/缺'}）")
        if b["excluded"]:
            md.append(f"- **排除 {len(b['excluded'])} 次**："
                      + "；".join(f"{x.get('file')}（{x.get('why')}）" for x in b["excluded"]))
        md.append("")
        for cut in ("原样", "都真做题"):
            rows = []
            for qid, q in b["questions"].items():
                if not q[cut]:
                    continue
                for side in SIDES:
                    s = q[cut].get(side)
                    if not s:
                        continue
                    rows.append((qid, side, s))
            if not rows:
                continue
            md.append(f"### {cut}（单位：秒；括号里是 P90）")
            md.append("")
            md.append("| 题 | 边 | n | 首字 | 其中路标 | 答案首字 | 墙钟 | 模型段 | 工具段 "
                      "| 出字段 | 收尾段 | 残差 | 轮数 | 工具次数 | 字数 | token |")
            md.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for qid, side, s in rows:
                md.append(
                    f"| `{qid}` | {'HCA' if side == 'atomcode' else '社区版'} | {s['n']} | "
                    + f"{fmt(s['ttft_ms']['median'], 's')}（{fmt(s['ttft_ms']['p90'], 's')}） | "
                    + f"{s['首字是路标的次数']}/{s['n']} | "
                    + " | ".join(
                        f"{fmt(s[k]['median'], 's')}（{fmt(s[k]['p90'], 's')}）"
                        for k in ("ttft_answer_ms", "wall_ms", "model_ms", "tool_ms",
                                  "stream_ms", "finish_ms", "residual_ms"))
                    + f" | {fmt(s['rounds']['median'])} | {fmt(s['tool_calls']['median'])} "
                      f"| {fmt(s['text_len']['median'])} | {fmt(s['token']['median'])} |")
            md.append("")
            md.append(f"#### {cut} · HCA ÷ 社区版（<1 = HCA 更快）")
            md.append("")
            md.append("| 题 | 首字(中位) | 首字(P90) | 答案首字(中位) | 墙钟(中位) | 墙钟(P90) "
                      "| 模型段(中位) | 工具段(中位) | 出字段(中位) |")
            md.append("|---|---|---|---|---|---|---|---|---|")
            for qid, q in b["questions"].items():
                r = q["比值"].get(cut)
                if not r:
                    continue
                md.append(f"| `{qid}` | " + " | ".join(fmt(x, "x") for x in (
                    r["ttft_ms"]["median"], r["ttft_ms"]["p90"],
                    r["ttft_answer_ms"]["median"],
                    r["wall_ms"]["median"], r["wall_ms"]["p90"],
                    r["model_ms"]["median"], r["tool_ms"]["median"],
                    r["stream_ms"]["median"])) + " |")
            md.append("")
    if summary.get("hook"):
        md.append("## hook 段（HCA 侧单独直测；社区版没有 hook 机制，不适用）")
        md.append("")
        md.append("```json")
        md.append(json.dumps(summary["hook"], ensure_ascii=False, indent=2))
        md.append("```")
        md.append("")
    return "\n".join(md)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("batches", nargs="+", type=Path)
    ap.add_argument("--hook-bench", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--md", type=Path, default=None)
    args = ap.parse_args(argv)

    summary = build(args.batches, args.hook_bench)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {args.out}")
    if args.md:
        args.md.write_text(to_md(summary), encoding="utf-8")
        print(f"→ {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
