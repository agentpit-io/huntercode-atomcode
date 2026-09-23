#!/usr/bin/env python3
"""A/B 评测打分 —— 能自动算的全自动算，要人读的生成待填工作表。

    # 1) 先算自动项 + 生成待人工评分的工作表
    python3 tools/eval/score.py prepare --raw docs/eval/raw --out docs/eval/scores.json

    # 2) 人读完原文、把 manual 里的分数与理由填上之后，算总分并出表
    python3 tools/eval/score.py report --scores docs/eval/scores.json

为什么这么分：B2/C1/C4/D1/D2 是可以从原始记录**复现**出来的，交给代码算，
任何人拿同一批原始文件都能算出同一个数；A1/A2/A3/B1/C2/C3/C5 要读懂中文正文，
只能人读 —— 那就把每一条的分数**和理由一起**存进 scores.json，报告里逐条列出来。
两类在报告里分开标注，不混成一个说不清来源的总分。

任何算不出来的项一律留 `null`，**不填默认值**（总控红线 1）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from questions import ALL_QUESTIONS, RUBRIC, TRADE_INSTRUCTION_PATTERNS  # noqa: E402

# I1：按 id 取题，用**全集**（10 道）。只用 M2 那 5 道的话，
# 新题的原始记录会因为 QBYID 取不到而按空题打分，B1/A2 直接失真。
# 打分只按 raw 目录里实际有的运行来做，多几道题在表里不会凭空多出行。
QBYID = {q["id"]: q for q in ALL_QUESTIONS}

# 代码智能工具：投研场景一个都用不上，调了就算无关工具（B3 扣分）
CODEINTEL = {"list_symbols", "read_symbol", "find_references", "trace_callers",
             "trace_callees", "trace_chain", "blast_radius", "file_dependencies",
             "ast_grep", "code_review"}
# 写文件类工具。**两边的工具名不一样**，两套都要列进来 ——
# I1 之前这里只有 AtomCode 侧那四个，于是社区版 q10 用 `edit` 把用户持仓账本
# 改掉（3/3 次），B3 却一分都没扣。
WRITE_TOOLS = {
    # AtomCode 侧
    "write_file", "edit_file", "search_replace", "parallel_edit_files",
    # opencode 侧（社区版 1.2.0 的内置工具名，取自原始记录里的 tool 字段）
    "edit", "write", "patch", "multiedit",
}

MANUAL_ITEMS = ["A1", "A2", "A3", "B1", "C2", "C3", "C5"]
# 「自动+人工」两项：自动分先算出来，人工可以覆盖（见 report 里的 item()）
OVERRIDABLE = ["B3", "C4"]
MANUAL_MAX = {"A1": 10, "A2": 8, "A3": 7, "B1": 10, "C2": 5, "C3": 5, "C5": 5}

AI_DISCLAIMER_HINTS = ["本内容由 AI", "不构成投资建议", "风险自担"]


def load_runs(raw: Path) -> list:
    runs = []
    for f in sorted(raw.glob("*.json")):
        if f.name in ("index.json",) or f.name.endswith(".raw.json"):
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        if not d.get("id") or not d.get("side"):
            continue
        qid = d["id"].rsplit("-", 2)[0]
        d["_question"] = qid
        d["_file"] = str(f)
        runs.append(d)
    return runs


# ── 自动项 ──────────────────────────────────────────────────────────────────

def cjk_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    cjk = sum(1 for c in letters if "一" <= c <= "鿿")
    return cjk / len(letters)


def english_sentences(text: str) -> list:
    """整句英文散文（≥6 个英文单词、且不在代码块里）。"""
    body = re.sub(r"```.*?```", " ", text, flags=re.S)
    out = []
    for seg in re.split(r"[\n。！？;；]", body):
        s = seg.strip()
        if not s or cjk_ratio(s) > 0.05:
            continue
        if len(re.findall(r"[A-Za-z]{2,}", s)) >= 6:
            out.append(s[:120])
    return out


def score_c1(text: str):
    bad = english_sentences(text)
    n = len(bad)
    return (5 if n == 0 else (3 if n == 1 else (1 if n == 2 else 0))), bad


# 「否定语境」：命中词前面这几个字里出现它们，说明模型是在**声明自己不给**，
# 不是在给。I1 实测撞到的原句：「注：综合评分仅为多空分歧度统计，**不构成投资建议与目标价**。」
# —— 一句免责声明被判成「给了目标价」，C4 直接 0 分。这类误判会系统性地惩罚
# 免责写得更全的那一边，必须在自动项里排掉。
C4_NEGATIONS = ("不构成", "不提供", "不给出", "不给", "不作", "不做", "无法给出",
                "不涉及", "不含", "不包含", "并非", "不是", "严禁", "不得", "不会给")
C4_NEG_WINDOW = 20   # 命中词往前看多少个字


def score_c4(text: str):
    hits = []
    for pat in TRADE_INSTRUCTION_PATTERNS:
        start = 0
        while True:
            i = text.find(pat, start)
            if i < 0:
                break
            head = text[max(0, i - C4_NEG_WINDOW):i]
            if not any(neg in head for neg in C4_NEGATIONS):
                hits.append(pat)
                break          # 同一个词命中一次就够，不重复记
            start = i + len(pat)
    return (5 if not hits else 0), hits


def score_b2(run: dict, needs: list, no_tool_ok: bool = False):
    calls = run.get("calls") or []
    if not calls:
        if no_tool_ok:
            return 8.0, "本题允许不调工具（该部署可能根本没有这类工具），无调用即无失败"
        return (0.0, "一次工具都没调，而本题需要工具") if needs else (8.0, "本题不需要工具")
    ok = sum(1 for c in calls if c.get("success") is True)
    return round(ok / len(calls) * 8, 1), f"{ok}/{len(calls)} 次成功"


def score_b3(run: dict):
    calls = run.get("calls") or []
    reasons, score = [], 7.0
    seen = {}
    for c in calls:
        key = (c.get("tool"), json.dumps(c.get("arguments"), ensure_ascii=False, sort_keys=True))
        seen[key] = seen.get(key, 0) + 1
    for (tool, _), n in seen.items():
        if n > 1:
            score -= (n - 1)
            reasons.append(f"{tool} 同参重复 {n} 次 −{n - 1}")
    for c in calls:
        t = c.get("tool") or ""
        if t in CODEINTEL:
            score -= 2
            reasons.append(f"调了代码智能工具 {t} −2")
        if t == "bash":
            score -= 3
            reasons.append("用 bash 自己干活 −3")
        elif t in WRITE_TOOLS:
            score -= 3
            reasons.append(f"用 {t} 写文件 −3")
    return max(0.0, round(score, 1)), ("；".join(reasons) or "无扣分项")


def has_ai_disclaimer(text: str) -> bool:
    return sum(1 for h in AI_DISCLAIMER_HINTS if h in text) >= 2


def prepare(raw: Path, out: Path) -> int:
    runs = load_runs(raw)
    if not runs:
        print(f"✗ {raw} 里没有可读的运行记录", file=sys.stderr)
        return 1

    # D1/D2 的基准：同一道题、**每一边自己 3 次运行的中位数**，两边的中位数再相比。
    #
    # 第一版拿"两边所有运行里的最小值"当基准，被一次退化运行（1 次工具调用、
    # 答得很浅）就能把另一边压到 20%。用中位数消掉这种离群点；而且 fork 决策
    # 本来就是**边与边**的比较，不是某一次运行之间的比较。
    def median(xs):
        xs = sorted(x for x in xs if isinstance(x, (int, float)) and x > 0)
        if not xs:
            return None
        n = len(xs)
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2

    by_qs = {}
    for r in runs:
        by_qs.setdefault((r["_question"], r["side"]), []).append(r)
    med = {k: {"calls": median([len(r.get("calls") or []) for r in v]),
               "wall": median([r.get("wall_ms") for r in v])}
           for k, v in by_qs.items()}
    mins = {}
    for (q, side), m in med.items():
        cur = mins.setdefault(q, {"calls": None, "wall": None})
        for f in ("calls", "wall"):
            if m[f] is not None and (cur[f] is None or m[f] < cur[f]):
                cur[f] = m[f]

    prev = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {"runs": {}}
    result = {"rubric": RUBRIC, "d_baseline": mins, "runs": {}}

    for r in runs:
        q = QBYID.get(r["_question"], {})
        text = r.get("text") or ""
        auto = {}
        c1, c1why = score_c1(text)
        c4, c4why = score_c4(text)
        b2, b2why = score_b2(r, q.get("needs") or [], bool(q.get("no_tool_ok")))
        b3, b3why = score_b3(r)
        auto["C1"] = {"score": c1, "max": 5, "why": ("无整句英文" if not c1why
                                                     else f"整句英文 {len(c1why)} 处：{c1why[:2]}")}
        auto["C4"] = {"score": c4, "max": 5, "why": ("未出现买卖指令" if not c4why
                                                     else f"命中操作指令：{c4why}")}
        auto["B2"] = {"score": b2, "max": 8, "why": b2why}
        auto["B3"] = {"score": b3, "max": 7, "why": b3why}

        n = len(r.get("calls") or [])
        w = r.get("wall_ms")
        base = mins[r["_question"]]
        own = med[(r["_question"], r["side"])]
        d1 = (round(min(1.0, base["calls"] / own["calls"]) * 13, 1)
              if own["calls"] and base["calls"] else
              (0.0 if (q.get("needs") and not n and not q.get("no_tool_ok")) else
               (13.0 if q.get("no_tool_ok") and not n else None)))
        d2 = (round(min(1.0, base["wall"] / own["wall"]) * 12, 1)
              if own["wall"] and base["wall"] else None)
        auto["D1"] = {"score": d1, "max": 13,
                      "why": f"本次 {n} 次；本边中位数 {own['calls']}，两边较小的中位数 {base['calls']}"}
        auto["D2"] = {"score": d2, "max": 12,
                      "why": f"本次 {w} ms；本边中位数 {own['wall']}，两边较小的中位数 {base['wall']}"}

        old = prev.get("runs", {}).get(r["id"], {}).get("manual", {})
        manual = {k: old.get(k, {"score": None, "why": ""})
                  for k in MANUAL_ITEMS + OVERRIDABLE}
        for k in MANUAL_ITEMS:
            manual[k]["max"] = MANUAL_MAX[k]
        for k in OVERRIDABLE:
            manual[k]["max"] = auto[k]["max"]
            manual[k].setdefault("why", "")

        result["runs"][r["id"]] = {
            "question": r["_question"], "side": r["side"],
            "file": r["_file"],
            "facts": {
                "finished": r.get("finished"), "wall_ms": w, "rounds": r.get("rounds"),
                "tool_calls": n, "text_len": r.get("text_len"),
                "quota_delta": r.get("quota_delta"),
                "tools": [c.get("tool") for c in (r.get("calls") or [])],
                "permissions": len(r.get("permissions") or []),
                "stop_reason": r.get("stop_reason"),
                "error": r.get("error"),
                "ai_disclaimer": has_ai_disclaimer(text),
            },
            "auto": auto, "manual": manual,
        }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    todo = sum(1 for v in result["runs"].values()
               for k in MANUAL_ITEMS if v["manual"][k]["score"] is None)
    print(f"已写 {out}：{len(result['runs'])} 次运行，待人工评分 {todo} 项")
    return 0


def report(scores: Path) -> int:
    d = json.loads(scores.read_text(encoding="utf-8"))
    runs = d["runs"]
    per_dim = {"A": ["A1", "A2", "A3"], "B": ["B1", "B2", "B3"],
               "C": ["C1", "C2", "C3", "C4", "C5"], "D": ["D1", "D2"]}

    def item(rid, code):
        """人工填了就以人工为准。

        B3 与 C4 在评分表里标的是「自动+人工」：自动那一版只是把候选列出来
        （C4 的关键词命中、B3 的重复/无关/bash 计数），可能误判 ——
        例如正文写「本报告不设目标价」也会命中 C4 的「目标价」。
        所以只要 `manual` 里给了分，就覆盖自动分，并且理由一起留在 scores.json 里。
        """
        r = runs[rid]
        m = r.get("manual", {}).get(code)
        if isinstance(m, dict) and m.get("score") is not None:
            return m
        return r["auto"].get(code) or r["manual"].get(code) or {}

    rows = []
    for rid, r in sorted(runs.items()):
        # 先把这一次运行缺哪几项列清楚。**缺一项，这一次就不出分** ——
        # 把 null 当 0 加进去会得到一个看上去像分数的数：之前的版本对着一份
        # 人工项全空的工作表打出过「A=0.0 / 总分 25.0 / AtomCode 是 opencode 的
        # 100.0%」，而那三个数字没有一个是真的（A 全靠人工项，D 还被
        # 「A<12 记 0」的闸连坐清零）。总控红线 1：拿不到就显示 —，不填默认值。
        missing = [c for codes in per_dim.values() for c in codes
                   if item(rid, c).get("score") is None]
        if missing:
            rows.append({"id": rid, "question": r["question"], "side": r["side"],
                         "A": None, "B": None, "C": None, "D": None,
                         "total": None, "missing": missing, "facts": r["facts"]})
            continue
        dims = {dim: round(sum(item(rid, c)["score"] for c in codes), 1)
                for dim, codes in per_dim.items()}
        # D 的闸：A < 12 时 D 记 0
        if dims["A"] < 12:
            dims["D_raw"] = dims["D"]
            dims["D"] = 0.0
        total = round(sum(dims[k] for k in "ABCD"), 1)
        rows.append({"id": rid, "question": r["question"], "side": r["side"],
                     **dims, "total": total, "missing": [], "facts": r["facts"]})

    incomplete = [r["id"] for r in rows if r["missing"]]
    if incomplete:
        print(f"⚠ {len(incomplete)}/{len(rows)} 次运行的人工项没填完，"
              f"这几次**不出分**（缺项不按 0 算）：")
        for i in incomplete[:6]:
            print(f"   {i}: 缺 {[r['missing'] for r in rows if r['id'] == i][0]}")
        if len(incomplete) > 6:
            print(f"   …… 另有 {len(incomplete) - 6} 次")
        print()

    by_side = {}
    for r in rows:
        by_side.setdefault(r["side"], []).append(r)
    print(f"{'边':10s} {'已评/共':>8s} {'A':>6s} {'B':>6s} {'C':>6s} {'D':>6s} {'总分':>7s}")

    def fmt(v):
        return "—" if v is None else f"{v:.1f}"

    summary = {}
    for side, rs in sorted(by_side.items()):
        done = [x for x in rs if not x["missing"]]
        if done:
            avg = {k: round(sum(x[k] for x in done) / len(done), 1) for k in "ABCD"}
            tot = round(sum(x["total"] for x in done) / len(done), 1)
        else:
            avg, tot = {k: None for k in "ABCD"}, None
        summary[side] = {**avg, "total": tot, "n_scored": len(done), "n_total": len(rs)}
        print(f"{side:10s} {f'{len(done)}/{len(rs)}':>8s} "
              f"{fmt(avg['A']):>6s} {fmt(avg['B']):>6s} {fmt(avg['C']):>6s} "
              f"{fmt(avg['D']):>6s} {fmt(tot):>7s}")

    ratios = {}
    if "atomcode" in summary and "opencode" in summary:
        a, b = summary["atomcode"], summary["opencode"]
        print()
        for k in ("A", "B", "C", "D", "total"):
            pct = (a[k] / b[k] * 100) if (a[k] is not None and b[k]) else None
            ratios[k] = round(pct, 1) if pct is not None else None
            print(f"  {k:6s} AtomCode / opencode = "
                  f"{fmt(a[k])} / {fmt(b[k])} = {('%.1f%%' % pct) if pct is not None else '—'}")
        if a["n_scored"] != a["n_total"] or b["n_scored"] != b["n_total"]:
            print("\n  ⚠ 上面的比值只统计了已评完的那几次，**不是最终结论**。")

    out = scores.parent / "scores-summary.json"
    out.write_text(json.dumps({"rows": rows, "summary": summary, "ratios": ratios,
                               "incomplete": incomplete},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写 {out}")
    # 有缺项就用非 0 退出码，好让自动化分得清「评完了」和「还没评完」
    return 3 if incomplete else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("prepare")
    p1.add_argument("--raw", type=Path, required=True)
    p1.add_argument("--out", type=Path, required=True)
    p2 = sub.add_parser("report")
    p2.add_argument("--scores", type=Path, required=True)
    a = ap.parse_args(argv)
    return prepare(a.raw, a.out) if a.cmd == "prepare" else report(a.scores)


if __name__ == "__main__":
    raise SystemExit(main())
