# -*- coding: utf-8 -*-
"""小鹿看板 · 悬停日K 三层蓝线(agent_run.scan_layers)用例(纯计算,不连库)。

    cd apps/api && PYTHONPATH=. python tests/test_agent_scan_layers.py

2026-09-15 用户问「为什么点开每只票都显示命中了很多天」:原来只画一层 = 进了这条线的候选池,
池子只做粗筛(突破买入每天约 224 只),KRYS 在池里 105 天、买入条件全满足只有买入那 1 天。
现在分三层:淡蓝 = 进候选池 / 中蓝 = 形态就绪 / 深蓝 = 买入条件全满足。这里钉住中蓝与深蓝的判法。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.quant import agent_run as ar          # noqa: E402
from app.services.quant import agent_breakout as abk    # noqa: E402

fails: list[str] = []
passed = 0


def check(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
    else:
        fails.append(f"{name}  {extra}")


R = abk.SETUP_RULES

# ① 全满足:进深蓝;fails 为空也算形态就绪
out = ar.scan_layers([("2026-06-11", "KRYS", 100, [])], R)
check("全满足进 entry", out.get("KRYS", {}).get("entry") == ["2026-06-11"], out)
check("全满足同时算形态就绪", out.get("KRYS", {}).get("setup") == ["2026-06-11"], out)

# ② 只差突破与市场:形态就绪,不是全满足
out = ar.scan_layers([("2026-06-10", "KRYS", 71, ["P-01", "P-06"])], R)
check("只差 P-01 / P-06 算形态就绪", out.get("KRYS", {}).get("setup") == ["2026-06-10"], out)
check("只差 P-01 / P-06 不算全满足", out.get("KRYS", {}).get("entry") == [], out)

# ③ 形态条件没过:两层都不进
out = ar.scan_layers([("2026-06-09", "KRYS", 71, ["P-04", "P-06"])], R)
check("P-04 没过两层都不进", "KRYS" not in out, out)
out = ar.scan_layers([("2026-06-09", "KRYS", 85, ["P-22"])], R)
check("P-22 没过不算形态就绪", "KRYS" not in out, out)

# ④ 老记录没有 fails:不猜形态就绪(进度再高也不算),全满足仍按进度
out = ar.scan_layers([("2026-03-01", "STT", 85, None), ("2026-03-02", "STT", 100, None)], R)
check("没有 fails 的老记录不算形态就绪", out.get("STT", {}).get("setup") == [], out)
check("没有 fails 时全满足仍按 progress_pct", out.get("STT", {}).get("entry") == ["2026-03-02"], out)

# ⑤ 引擎没声明 SETUP_RULES:永远没有中蓝那层,深蓝照常
out = ar.scan_layers([("2026-05-01", "ARMK", 100, []), ("2026-05-02", "ARMK", 80, ["R-04"])], None)
check("没声明形态就绪规则就不出 setup", out.get("ARMK", {}).get("setup") == [], out)
check("没声明形态就绪规则时 entry 照常", out.get("ARMK", {}).get("entry") == ["2026-05-01"], out)

# ⑥ 从 SQL 的 ->> 取出来的进度是文本
out = ar.scan_layers([("2026-06-11", "CVS", "100", [])], R)
check("文本 '100' 也算全满足", out.get("CVS", {}).get("entry") == ["2026-06-11"], out)
out = ar.scan_layers([("2026-06-11", "CVS", "85", ["P-06"])], R)
check("文本 '85' 不算全满足", out.get("CVS", {}).get("entry") == [], out)

# ⑦ 没有代码的行跳过;多只票各归各
out = ar.scan_layers([("2026-06-11", None, 100, []), ("2026-06-11", "A", 100, []), ("2026-06-12", "B", 71, ["P-06"])], R)
check("空代码跳过", None not in out and "" not in out, out)
check("多只票各归各", out.get("A", {}).get("entry") == ["2026-06-11"] and out.get("B", {}).get("setup") == ["2026-06-12"], out)

# ⑧ 突破买入的形态就绪规则:不含市场 P-01 与突破 P-06
check("SETUP_RULES 不含 P-01 / P-06", "P-01" not in R and "P-06" not in R and set(R) == {"P-02", "P-03", "P-04", "P-05", "P-22"}, R)
check("有 SETUP_LABEL 与 SETUP_NOTE", bool(abk.SETUP_LABEL) and bool(abk.SETUP_NOTE))

# ⑨ watch_item 往后要落 fails(规则编号),和 progress_pct 同一份 checks
_orig = abk.entry_checks
try:
    abk.entry_checks = lambda ind, p=None, score=None, market=None: [
        {"rule": "P-01", "ok": True, "text": ""}, {"rule": "P-02", "ok": True, "text": ""},
        {"rule": "P-06", "ok": False, "text": "还没站上枢轴"}, {"rule": "P-22", "ok": True, "text": ""}]
    it = abk.watch_item("X", "X", {"close": 10.0}, False, None, 90)
finally:
    abk.entry_checks = _orig
check("watch_item 输出 fails", it.get("fails") == ["P-06"], it)
check("watch_item 进度与 fails 同一份 checks", it.get("progress_pct") == 75, it)
it_none = abk.watch_item("X", "X", None, False, None, 90)
check("指标算不出时不写 fails(不猜)", "fails" not in it_none, it_none)

# ⑩ _trade_rounds 新参数可省略(agent_research 与老用例按两个参数调)
check("_trade_rounds 两个参数照旧能调", ar._trade_rounds([], {}) == [])
check("_trade_rounds 四个参数能调", ar._trade_rounds([], {}, {}, {}) == [])

print(f"passed {passed}, failed {len(fails)}")
for f in fails:
    print("FAIL", f)
if fails:
    sys.exit(1)
print("ALL OK")
