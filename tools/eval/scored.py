"""把已经填过人工分的那几次运行的 A / B / C / D 分算出来（没填的跳过）。"""
import json, sys, statistics
from pathlib import Path
d = json.loads((Path(sys.argv[1]) / "scores.json").read_text(encoding="utf-8"))
runs = d.get("runs", d)
DIM = {"A": ["A1", "A2", "A3"], "B": ["B1", "B2", "B3"],
       "C": ["C1", "C2", "C3", "C4", "C5"], "D": ["D1", "D2"]}
rows = []
for rid, r in runs.items():
    auto, manual = r.get("auto", {}), r.get("manual", {})
    def get(code):
        m = manual.get(code) or {}
        if m.get("score") is not None:
            return m["score"]
        a = auto.get(code) or {}
        return a.get("score")
    vals = {}
    ok = True
    for dim, codes in DIM.items():
        xs = [get(c) for c in codes]
        if any(x is None for x in xs):
            ok = False
            break
        vals[dim] = round(sum(xs), 1)
    if ok:
        rows.append((rid, vals))
if not rows:
    print("（这个批次还没有任何一次运行是完整评过分的）"); raise SystemExit
print(f"{Path(sys.argv[1]).name}：{len(rows)} 次运行完整出分")
print("| 运行 | A/25 | B/25 | C/25 | D/25 | 合计/100 |")
print("|---|---|---|---|---|---|")
for rid, v in sorted(rows):
    tot = round(v["A"] + v["B"] + v["C"] + v["D"], 1)
    print(f"| {rid} | {v['A']} | {v['B']} | {v['C']} | {v['D']} | **{tot}** |")
for dim in "ABCD":
    xs = [v[dim] for _, v in rows]
    print(f"{dim} 平均 {statistics.mean(xs):.1f}", end="  ")
print()
print(f"合计平均 {statistics.mean([sum(v.values()) for _, v in rows]):.1f} / 100")
