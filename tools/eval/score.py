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
from questions import QUESTIONS, RUBRIC, TRADE_INSTRUCTION_PATTERNS  # noqa: E402

QBYID = {q["id"]: q for q in QUESTIONS}

# 代码智能工具：投研场景一个都用不上，调了就算无关工具（B3 扣分）
CODEINTEL = {"list_symbols", "read_symbol", "find_references", "trace_callers",
             "trace_callees", "trace_chain", "blast_radius", "file_dependencies",
             "ast_grep", "code_review"}
WRITE_TOOLS = {"write_file", "edit_file", "search_replace", "parallel_edit_files"}

MANUAL_ITEMS = ["A1", "A2", "A3", "B1", "C2", "C3", "C5"]
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


def score_c4(text: str):
    hits = [p for p in TRADE_INSTRUCTION_PATTERNS if p in text]
    return (5 if not hits else 0), hits


def score_b2(run: dict, needs: list):
    calls = run.get("calls") or []
    if not calls:
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

    # D1/D2 的相对基准：同一道题、两边所有成功运行里的最小值
    mins = {}
    for r in runs:
        q = r["_question"]
        n = len(r.get("calls") or [])
        w = r.get("wall_ms")
        m = mins.setdefault(q, {"calls": None, "wall": None})
        if n > 0 and (m["calls"] is None or n < m["calls"]):
            m["calls"] = n
        if isinstance(w, (int, float)) and w > 0 and (m["wall"] is None or w < m["wall"]):
            m["wall"] = w

    prev = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {"runs": {}}
    result = {"rubric": RUBRIC, "d_baseline": mins, "runs": {}}

    for r in runs:
        q = QBYID.get(r["_question"], {})
        text = r.get("text") or ""
        auto = {}
        c1, c1why = score_c1(text)
        c4, c4why = score_c4(text)
        b2, b2why = score_b2(r, q.get("needs") or [])
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
        d1 = (round(base["calls"] / n * 13, 1) if n and base["calls"] else
              (0.0 if (q.get("needs") and not n) else None))
        d2 = (round(base["wall"] / w * 12, 1) if isinstance(w, (int, float)) and w > 0
              and base["wall"] else None)
        auto["D1"] = {"score": d1, "max": 13,
                      "why": f"本次 {n} 次，本题最小 {base['calls']} 次"}
        auto["D2"] = {"score": d2, "max": 12,
                      "why": f"本次 {w} ms，本题最小 {base['wall']} ms"}

        old = prev.get("runs", {}).get(r["id"], {}).get("manual", {})
        manual = {k: old.get(k, {"score": None, "why": ""}) for k in MANUAL_ITEMS}
        for k in MANUAL_ITEMS:
            manual[k]["max"] = MANUAL_MAX[k]

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
        r = runs[rid]
        return r["auto"].get(code) or r["manual"].get(code) or {}

    rows = []
    for rid, r in sorted(runs.items()):
        dims, total, missing = {}, 0.0, []
        for dim, codes in per_dim.items():
            s = 0.0
            for c in codes:
                v = item(rid, c).get("score")
                if v is None:
                    missing.append(c)
                else:
                    s += v
            dims[dim] = round(s, 1)
        # D 的闸：A < 12 时 D 记 0
        if dims["A"] < 12:
            dims["D_raw"] = dims["D"]
            dims["D"] = 0.0
        total = round(sum(dims[k] for k in "ABCD"), 1)
        rows.append({"id": rid, "question": r["question"], "side": r["side"],
                     **dims, "total": total, "missing": missing,
                     "facts": r["facts"]})

    incomplete = [r["id"] for r in rows if r["missing"]]
    if incomplete:
        print(f"⚠ 还有 {len(incomplete)} 次运行的人工项没填完，总分仅供参考：")
        for i in incomplete[:6]:
            print(f"   {i}: 缺 {runs[i] and [c for c in MANUAL_ITEMS if item(i, c).get('score') is None]}")
        print()

    by_side = {}
    for r in rows:
        by_side.setdefault(r["side"], []).append(r)
    print(f"{'边':10s} {'次数':>4s} {'A':>6s} {'B':>6s} {'C':>6s} {'D':>6s} {'总分':>7s}")
    summary = {}
    for side, rs in sorted(by_side.items()):
        avg = {k: round(sum(x[k] for x in rs) / len(rs), 1) for k in "ABCD"}
        tot = round(sum(x["total"] for x in rs) / len(rs), 1)
        summary[side] = {**avg, "total": tot, "n": len(rs)}
        print(f"{side:10s} {len(rs):>4d} {avg['A']:>6.1f} {avg['B']:>6.1f} "
              f"{avg['C']:>6.1f} {avg['D']:>6.1f} {tot:>7.1f}")
    if "atomcode" in summary and "opencode" in summary:
        a, b = summary["atomcode"], summary["opencode"]
        print()
        for k in ("A", "B", "C", "D", "total"):
            pct = (a[k] / b[k] * 100) if b[k] else None
            print(f"  {k:6s} AtomCode / opencode = "
                  f"{a[k]} / {b[k]} = {('%.1f%%' % pct) if pct is not None else '—'}")

    out = scores.parent / "scores-summary.json"
    out.write_text(json.dumps({"rows": rows, "summary": summary},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写 {out}")
    return 0


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
