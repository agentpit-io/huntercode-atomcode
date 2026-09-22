#!/usr/bin/env python3
"""把一个批次里 HCA 侧每一次运行的 A1 取证结果汇成一行一条。

A1 是**一票否决项**（「每个数字都来自本次工具返回」），所以它要跑全部运行，
不能只抽样。这个脚本不打分 —— 它只报「审了几个数、几个没在工具返回里找到」，
未命中的那几个仍然要人看一眼（多数是模型自己算出来的派生数，不是编的）。
"""
import io, sys, json, re
from contextlib import redirect_stdout
from pathlib import Path
sys.path.insert(0, "tools/eval")
from audit_numbers import audit

root = Path(sys.argv[1])
side = sys.argv[2] if len(sys.argv) > 2 else "atomcode"
rows = []
for f in sorted(root.glob(f"*-{side}-r*.json")):
    if f.name.endswith(".raw.json"):
        continue
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            audit(f.with_suffix(""))
    except Exception as e:
        rows.append((f.stem, None, None, f"{type(e).__name__}: {e}"))
        continue
    out = buf.getvalue()
    m = re.search(r"审了 (\d+) 个不同的数", out)
    m2 = re.search(r"\*\*(\d+) 个没在工具返回里找到\*\*", out) or re.search(r"(\d+) 个没在工具返回里找到", out)
    miss = re.findall(r"`([^`]*?)`\s*→", out)
    rows.append((f.stem, int(m.group(1)) if m else None,
                 int(m2.group(1)) if m2 else None, "; ".join(miss[:6])))
tot_n = sum(r[1] or 0 for r in rows)
tot_m = sum(r[2] or 0 for r in rows)
print(f"批次 {root.name} · {side} 侧 · {len(rows)} 次运行 · 共审 {tot_n} 个数 · 未命中 {tot_m}")
for stem, n, miss, detail in rows:
    flag = "  " if not miss else "⚠ "
    print(f"{flag}{stem:42s} 审 {n if n is not None else '—':>3} 未命中 {miss if miss is not None else '—':>2}  {detail[:90]}")
