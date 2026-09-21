#!/usr/bin/env python3
"""把 `scores.json` 渲染成 `docs/开发文档/M2-AB对比报告.md`。

    python3 tools/eval/make_report.py --scores docs/eval/scores.json \
        --raw docs/eval/raw --out docs/开发文档/M2-AB对比报告.md

报告里的每一个数字都从 `scores.json` / 原始记录里取，这个脚本**不做任何估算**：
人工项没填的用 `—` 显示，并在表头下面明说还差几项。这样报告永远和证据对得上，
也不可能出现"报告里有个数但找不到出处"。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from questions import QUESTIONS, RUBRIC  # noqa: E402

QBYID = {q["id"]: q for q in QUESTIONS}
SIDE_LABEL = {"atomcode": "HCA（AtomCode 版）", "opencode": "基线（opencode 版）"}
DIMS = {"A": ("A_准确性", ["A1", "A2", "A3"]),
        "B": ("B_工具调用正确率", ["B1", "B2", "B3"]),
        "C": ("C_输出规范符合度", ["C1", "C2", "C3", "C4", "C5"]),
        "D": ("D_步数与耗时", ["D1", "D2"])}


def fmt(v, nd=1):
    return "—" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def pct(a, b):
    return "—" if not b else f"{a / b * 100:.1f}%"


def item_of(run, code):
    m = (run.get("manual") or {}).get(code)
    if isinstance(m, dict) and m.get("score") is not None:
        return m, "人工"
    a = (run.get("auto") or {}).get(code)
    if a is not None:
        return a, "自动"
    return (m or {}), "人工"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", type=Path, required=True)
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--index", type=Path, default=None)
    args = ap.parse_args(argv)

    d = json.loads(args.scores.read_text(encoding="utf-8"))
    runs = d["runs"]
    idx_path = args.index or (args.raw / "index.json")
    index = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.is_file() else {}

    # 逐次汇总
    # 缺一项，这一次就整个不出分 —— 把 null 当 0 加进去会得到一个看上去像分数
    # 的数（人工项全空时能打出「A=0.0、总分 25.0、比值 100.0%」，一个真的都没有）。
    # 总控红线 1：拿不到显示 —，不填默认值。
    rows, missing_total = [], 0
    for rid, r in sorted(runs.items()):
        dims, miss = {}, []
        for k, (_, codes) in DIMS.items():
            vals = []
            for c in codes:
                it, _ = item_of(r, c)
                if it.get("score") is None:
                    miss.append(c)
                else:
                    vals.append(it["score"])
            dims[k] = round(sum(vals), 1)
        missing_total += len(miss)
        if miss:
            rows.append({"id": rid, "q": r["question"], "side": r["side"],
                         "A": None, "B": None, "C": None, "D": None,
                         "gated": False, "total": None,
                         "missing": miss, "facts": r["facts"]})
            continue
        gated = dims["A"] < 12
        if gated:
            dims["D_raw"], dims["D"] = dims["D"], 0.0
        rows.append({"id": rid, "q": r["question"], "side": r["side"],
                     **dims, "gated": gated,
                     "total": round(dims["A"] + dims["B"] + dims["C"] + dims["D"], 1),
                     "missing": [], "facts": r["facts"]})

    by_side = {}
    for r in rows:
        by_side.setdefault(r["side"], []).append(r)

    def side_avg(v):
        done = [x for x in v if not x["missing"]]
        if not done:
            return {**{k: None for k in "ABCD"}, "total": None,
                    "n": 0, "n_total": len(v)}
        return {**{k: round(sum(x[k] for x in done) / len(done), 1) for k in "ABCD"},
                "total": round(sum(x["total"] for x in done) / len(done), 1),
                "n": len(done), "n_total": len(v)}

    summary = {s: side_avg(v) for s, v in by_side.items()}
    a, b = summary.get("atomcode"), summary.get("opencode")

    L = []
    w = L.append
    w("# M2 · A/B 对比报告：HCA（AtomCode 版） vs 基线（opencode 版）")
    w("")
    w("> 由 `tools/eval/make_report.py` 从 `docs/eval/scores.json` 渲染，"
      "每个数字都能回溯到 `docs/eval/raw/` 下的原始记录。")
    w("> 人工项没填的显示 `—`，**不补默认值**。")
    if missing_total:
        w(f"> ⚠️ 当前还有 **{missing_total} 个人工项未评**，下面的分数是不完整的。")
    w("")

    # ── 总分 ──
    w("## 1. 总分")
    w("")
    if a and b:
        w("| 维度 | HCA | 基线 | HCA / 基线 |")
        w("|---|---|---|---|")
        for k, label in (("A", "A 准确性（25）"), ("B", "B 工具调用正确率（25）"),
                         ("C", "C 输出规范符合度（25）"), ("D", "D 步数与耗时（25）"),
                         ("total", "**总分（100）**")):
            w(f"| {label} | {fmt(a[k])} | {fmt(b[k])} | **{pct(a[k], b[k])}** |")
        w("")
        w(f"HCA 侧 {a['n']}/{a['n_total']} 次、基线侧 {b['n']}/{b['n_total']} 次"
          f"**评完**，上面是已评完那些的算术平均（没评完的不按 0 算，整次不出分）。"
          f"**fork 闸门：总分 ≥ 基线 80% 不 fork。**")
        w("另外单独看 C 维度（输出规范符合度）—— 它与工具清单无关，"
          "是「人设能不能压住编码规则」的直接度量。")
    else:
        w("（两边的数据不齐，无法给总分）")
    w("")

    # ── 逐题 ──
    w("## 2. 逐题分项")
    w("")
    for q in QUESTIONS:
        qrows = [r for r in rows if r["q"] == q["id"]]
        if not qrows:
            continue
        w(f"### {q['id']} · {q['kind']}")
        w("")
        w("> " + q["text"].replace("\n", " "))
        w("")
        w("| 运行 | A | B | C | D | 总分 | 轮数 | 工具调用 | 墙钟(ms) | 配额差值 | 停止原因 |")
        w("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in sorted(qrows, key=lambda x: (x["side"], x["id"])):
            f = r["facts"]
            w(f"| {r['id']} | {fmt(r['A'])} | {fmt(r['B'])} | {fmt(r['C'])} | "
              f"{fmt(r['D'])}{'（A<12 被闸掉）' if r['gated'] else ''} | **{fmt(r['total'])}** | "
              f"{fmt(f.get('rounds'), 0)} | {f.get('tool_calls')} | {f.get('wall_ms')} | "
              f"{fmt(f.get('quota_delta'), 0)} | {f.get('stop_reason') or '—'} |")
        w("")
        for side in ("atomcode", "opencode"):
            srows = [r for r in qrows if r["side"] == side]
            if not srows:
                continue
            srows = [x for x in srows if not x["missing"]]
            if not srows:
                continue
            avg = {k: round(sum(x[k] for x in srows) / len(srows), 1) for k in "ABCD"}
            tot = round(sum(x["total"] for x in srows) / len(srows), 1)
            w(f"- **{SIDE_LABEL[side]}** 平均：A {avg['A']} / B {avg['B']} / "
              f"C {avg['C']} / D {avg['D']} → **{tot}**")
        w("")

    # ── 逐条给分与理由 ──
    w("## 3. 逐条给分与理由")
    w("")
    w("「自动」项由 `tools/eval/score.py` 从原始记录算出，换个人拿同一批文件能算出同一个数；"
      "「人工」项是读原文给的，理由一起记在 `docs/eval/scores.json` 里。")
    w("")
    for rid in sorted(runs):
        r = runs[rid]
        w(f"<details><summary><code>{rid}</code>"
          f"（{SIDE_LABEL.get(r['side'], r['side'])}）</summary>")
        w("")
        w("| 项 | 分 / 满分 | 来源 | 理由 |")
        w("|---|---|---|---|")
        for k, (_, codes) in DIMS.items():
            for c in codes:
                it, how = item_of(r, c)
                why = (it.get("why") or "").replace("|", "\\|").replace("\n", " ")
                w(f"| {c} | {fmt(it.get('score'))} / {it.get('max', '?')} | {how} | {why} |")
        w("")
        w("</details>")
        w("")

    # ── 评分表原文 ──
    w("## 4. 评分表（固定，评测前就定好）")
    w("")
    for dim, spec in RUBRIC.items():
        w(f"**{dim}（满分 {spec['total']}）**")
        w("")
        w("| 项 | 满分 | 来源 | 判定 |")
        w("|---|---|---|---|")
        for it in spec["items"]:
            w(f"| {it['id']} | {it['max']} | {it['how']} | {it['desc']} |")
        if spec.get("gate"):
            w("")
            w(f"> 闸门：{spec['gate']}")
        w("")

    # ── 运行环境 ──
    w("## 5. 运行环境与原始记录")
    w("")
    w("| 项 | 值 |")
    w("|---|---|")
    counts = "、".join(f"{SIDE_LABEL[s]} {v['n_total']} 次（已评 {v['n']}）"
                       for s, v in summary.items())
    w(f"| 运行次数 | {len(rows)}（{counts}） |")
    w("| 顺序 | 交错，且每一轮换先手（`tools/eval/run_ab.py`） |")
    w(f"| 原始记录 | `{args.raw}/`（每次一份 `.json` 全文 + A 侧 `.sse` / B 侧 `.raw.json`） |")
    if index.get("finished_at"):
        w(f"| 跑完时间 | {index['finished_at']}（总耗时 {index.get('total_elapsed_s')} 秒） |")
    if index.get("quota_at_end"):
        qe = index["quota_at_end"]
        w(f"| 跑完后配额 | used_today {qe.get('used_today')} / {qe.get('limit_daily')} |")
    if index.get("stopped_for_quota_at"):
        w(f"| ⚠️ 中途停在 | {index['stopped_for_quota_at']}（配额不足） |")
    w("")

    # ── 典型失败样例（原文）──
    #
    # 摘录**不手抄**：scores.json 里只写「哪一次运行、正文里哪一句话」，
    # 由这里回原始记录去截。截不到就报错退出，不给"报告里有句原文但原始记录里
    # 找不到"留任何余地（总控红线 1）。
    samples = d.get("samples") or []
    w("## 6. 典型失败样例（原文）")
    w("")
    if not samples:
        w("（`scores.json` 的 `samples` 为空 —— 还没挑样例）")
        w("")
    for i, sp in enumerate(samples, 1):
        rid = sp["run"]
        f = args.raw / f"{rid}.json"
        if not f.is_file():
            print(f"✗ 样例 {rid} 的原始记录不存在：{f}", file=sys.stderr)
            return 2
        text = json.loads(f.read_text(encoding="utf-8")).get("text") or ""
        needle = sp["quote_contains"]
        at = text.find(needle)
        if at < 0:
            print(f"✗ 样例 {rid} 的摘录在原文里找不到：{needle[:40]!r}", file=sys.stderr)
            return 2
        before = int(sp.get("before", 0))
        after = int(sp.get("after", 200))
        lo, hi = max(0, at - before), min(len(text), at + len(needle) + after)
        excerpt = ("…" if lo else "") + text[lo:hi] + ("…" if hi < len(text) else "")
        run = runs.get(rid, {})
        w(f"### 6.{i} `{rid}`（{SIDE_LABEL.get(run.get('side'), '?')}）· {sp['title']}")
        w("")
        w("```text")
        w(excerpt)
        w("```")
        w("")
        w(f"**问题**：{sp['why']}")
        w("")
        w(f"> 原文全文：`{args.raw}/{rid}.json` 的 `text` 字段"
          f"（本段自第 {lo} 字符起截取，由 `make_report.py` 回原始记录取得，非手抄）。")
        w("")

    # ── 结论与 fork 决策 ──
    w("## 7. 结论与 fork 决策")
    w("")
    if a and b and a["total"] is not None and b["total"]:
        ratio = a["total"] / b["total"] * 100
        verdict = "**不 fork**" if ratio >= 80 else "**进入最小补丁（fork）**"
        w(f"总分比 **{ratio:.1f}%**（HCA {a['total']} / 基线 {b['total']}），"
          f"闸门是 80% → {verdict}。")
        w("")
        if missing_total:
            w(f"⚠️ 还有 {missing_total} 个人工项未评，**这个比值目前不作数**。")
            w("")
    else:
        w("（数据不齐，暂不给比值）")
        w("")
    if d.get("conclusion"):
        w(d["conclusion"].rstrip())
        w("")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"已写 {args.out}（{len(rows)} 次运行，未评人工项 {missing_total}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
