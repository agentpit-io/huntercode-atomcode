# -*- coding: utf-8 -*-
"""扫描筛选 · 历史命中日(screen_hits)的纯函数用例,不连库。

    cd apps/api && PYTHONPATH=. python tests/test_screen_hits.py

盯两件事:
1. 单只票查名次(rating_of)与全市场排名(screen_rs.rs_ratings)逐位相同 —— 含并列、含覆盖率门槛;
2. raw_at 与 rs_history.rs_raw_exact 浮点逐位一致 —— 不一致的话二分查不到自己,评级整批变 None。
"""
from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.quant import screen_hits as sh     # noqa: E402
from app.services.quant import screen_rs              # noqa: E402
from app.services.quant import rs_history as rh       # noqa: E402

fails: list[str] = []
passed = 0


def check(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
    else:
        fails.append(f"{name}  {extra}")


rnd = random.Random(20260913)

# ── 1. 名次与全市场排名一致 ─────────────────────────────────────
for trial in range(40):
    n = rnd.randint(5, 400)
    vals = [round(rnd.uniform(-0.8, 3.0), rnd.choice([1, 2, 6])) for _ in range(n)]   # 保留位数少 → 故意制造并列
    raw = {k: v for k, v in enumerate(vals)}
    pool_n = n + rnd.randint(0, n // 20)
    full, _cov = screen_rs.rs_ratings(raw, pool_n)
    srt = sorted(vals)
    bad = [k for k, v in raw.items() if sh.rating_of(srt, pool_n, v, screen_rs.RS_UNIVERSE_THRESHOLD) != full.get(k)]
    check(f"名次一致 · 第 {trial} 组({n} 只)", not bad, f"不一致 {len(bad)} 只")

vals = [1.0, 2.0, 2.0, 2.0, 3.0]
full, _ = screen_rs.rs_ratings(dict(enumerate(vals)), 5)
check("并列取平均名次", sh.rating_of(sorted(vals), 5, 2.0, 0.9) == full[1], str(full))
check("覆盖率低于门槛整天不给(4/5 = 80% < 90%)", sh.rating_of([1.0, 2.0, 3.0, 4.0], 5, 2.0, 0.9) is None)
check("全市场排名在门槛下同样不给", screen_rs.rs_ratings({0: 1.0, 1: 2.0, 2: 3.0, 3: 4.0, 4: None}, 5)[0] == {})
check("值不在表里 → None(不猜名次)", sh.rating_of([1.0, 2.0, 3.0], 3, 2.5, 0.9) is None)
check("Raw 为 None → None", sh.rating_of([1.0, 2.0], 2, None, 0.9) is None)

# ── 2. raw_at 与 rs_raw_exact 逐位一致 ───────────────────────────
for trial in range(30):
    n = rnd.randint(253, 600)
    px = [rnd.uniform(5, 50)]
    for _ in range(n - 1):
        px.append(max(0.5, px[-1] * (1 + rnd.gauss(0, 0.03))))
    i = rnd.randint(252, n - 1)
    a = sh.raw_at(px, i)
    b = rh.rs_raw_exact(px[:i + 1])
    check(f"Raw 逐位一致 · 第 {trial} 组", a == b, f"{a!r} vs {b!r}")
check("不足 253 根 → None", sh.raw_at([10.0] * 300, 251) is None and rh.rs_raw_exact([10.0] * 252) is None)

# ── 3. 时间序列脚本的命中日(_hit_days_series)与全市场扫描同口径 ─────────
# 「表里命中的票,图上最后一天必有蓝线」:扫描走 bars_matrix(截 plan.window 根)+ evaluate,
# 命中日走整段历史 + evaluate。两边任何一处窗口差一根,所有命中日整体平移一天,且不报错。
from bisect import bisect_right  # noqa: E402
from datetime import date as _date, timedelta as _td  # noqa: E402

import numpy as np  # noqa: E402
from app.services.quant import screen_dsl as sd, screen_series as sser  # noqa: E402

_SMA = [5, 10, 20, 50, 200]
_FIELDS = {"close", "open", "high", "low", "volume", "market_cap_basic"} | {f"SMA{n}" for n in _SMA}
_has = lambda n: n in _FIELDS  # noqa: E731
NL = chr(10)


def _comp(script):
    c = sd.compile_script(script, _has, _SMA, _SMA, [14])
    assert c.series is not None, script
    return c


_DAYS: list = []
_d = _date(2025, 11, 3)
while len(_DAYS) < 90:
    if _d.weekday() < 5:
        _DAYS.append(_d)
    _d += _td(days=1)


def _bars(close, open_=None):
    c = np.array(close, dtype=float)
    o = np.array(open_, dtype=float) if open_ is not None else c * 0.99
    return np.column_stack([c, c * 1.01, c * 0.98, np.full(len(c), 1e6), o])


_n = len(_DAYS)
jump = [10.0] * _n
for _i in range(40, _n):
    jump[_i] = 20.0
nan_c = [10.0 + 0.1 * i for i in range(_n)]
for _i in (60, 61, 62):
    nan_c[_i] = float("nan")
rnd2 = random.Random(915)
zig = [10.0]
for _ in range(_n - 1):
    zig.append(max(1.0, zig[-1] * (1 + rnd2.gauss(0, 0.02))))
zig_open = [x * (1 + rnd2.choice([-0.01, 0.01])) for x in zig]
_store = {"codes": {
    "JUMP": (list(_DAYS), _bars(jump)),
    "NANS": (list(_DAYS), _bars(nan_c)),
    "ZIG": (list(_DAYS), _bars(zig, zig_open)),
    "LATE": (list(_DAYS[30:]), _bars(zig[30:], zig_open[30:])),          # 次新:第 30 天才上市
    "UP": (list(_DAYS), _bars([10.0 + i for i in range(_n)])),
}}

J_SCRIPT = "plot scan = close > close[1] * 1.5;"
c_jump = _comp(J_SCRIPT)
dates_j, arr_j = _store["codes"]["JUMP"]
out = sh._hit_days_series(c_jump, dates_j, arr_j, len(dates_j), 250, dates_j[-1], "JUMP", "us")
check("⭐ 只在第 40 天跳涨 → 命中日恰好是那一天(差一根就会报成前一天或后一天)",
      out["hits"] == [str(_DAYS[40])], out["hits"])
check("整段 90 根 · evaluated=90、from=第一天、to=截止日", out["evaluated"] == 90 and out["from"] == str(_DAYS[0])
      and out["to"] == str(_DAYS[-1]), (out["evaluated"], out["from"], out["to"]))
out = sh._hit_days_series(c_jump, dates_j, arr_j, 45, 10, dates_j[44], "JUMP", "us")
check("截到第 45 根、看 10 天 → from=第 35 天、仍命中第 40 天", out["from"] == str(_DAYS[35]) and out["hits"] == [str(_DAYS[40])]
      and out["evaluated"] == 10 and out["to"] == str(_DAYS[44]), out)
out = sh._hit_days_series(c_jump, dates_j, arr_j, 40, 10, dates_j[39], "JUMP", "us")
check("⭐ 截到跳涨前一天(k_end=40)→ 不许看到第 40 天(不泄漏未来)", out["hits"] == [], out["hits"])
out = sh._hit_days_series(c_jump, dates_j, arr_j, 41, 1, dates_j[40], "JUMP", "us")
check("截到跳涨当天、只看 1 天 → 最后一天命中", out["hits"] == [str(_DAYS[40])] and out["evaluated"] == 1, out)

c_up = _comp("plot scan = close > close[1];")
dn, an = _store["codes"]["NANS"]
out = sh._hit_days_series(c_up, dn, an, 70, 15, dn[69], "NANS", "us")
check("⭐ 第 60~62 天收盘缺失 → 60/61/62/63 四天算不出(63 要用 62 的收盘)", out["unknown"] == 4, out)
check("缺失那几天不算命中,其余 11 天都命中", len(out["hits"]) == 11 and str(_DAYS[60]) not in out["hits"]
      and str(_DAYS[63]) not in out["hits"] and str(_DAYS[64]) in out["hits"], out["hits"])
check("有算不出的天 → note 里写明天数", out["note"] and "4 天算不出" in out["note"], out["note"])
dz, az = _store["codes"]["ZIG"]
out = sh._hit_days_series(c_up, dz, az, len(dz), 250, dz[-1], "ZIG", "us")
check("第 0 根没有前一根 → 算不出 1 天(不当成没命中)", out["unknown"] == 1, out["unknown"])

c_snap = _comp("def up = close > close[1];" + NL + "plot scan = up and market_cap_basic > 1;")
out = sh._hit_days_series(c_snap, dz, az, len(dz), 30, dz[-1], "ZIG", "us")
ups = sum(1 for i in range(len(dz) - 30, len(dz)) if az[i, 0] > az[i - 1, 0])
check("⭐ 用到快照字段 · 上涨的天算不出、下跌的天不满足、0 命中", out["hits"] == [] and out["unknown"] == ups,
      (out["unknown"], ups))
check("用到快照字段 · unavailable 点名、note 说明没有历史", out["unavailable"] == ["market_cap_basic"]
      and "没有历史" in (out["note"] or ""), (out["unavailable"], out["note"]))

# 与全市场扫描逐日逐票对照
CONSIST = {
    "偏移": "plot scan = close > close[1];",
    "窗口": "def hi = Highest(high[1], 10);" + NL + "plot scan = close > hi * 0.99;",
    # 光写 close > open 是横截面脚本(不进序列引擎),要带一个序列写法
    "开盘": "def g = if close > open then 1 else 0;" + NL + "plot scan = g > 0;",
    "递归连阳": "def s = if close > open then s[1] + 1 else 0;" + NL + "plot scan = s >= 2;",
    "input+rec+宿主": NL.join(["input n = 3;", "input k = 2;",
                             "rec g = if close > close[1] then g[1] + 1 else 0;",
                             "def ret = close / close[n] - 1;",
                             "def Setup = g >= k and ret > 0 and close > Average(close, 5);",
                             "plot scan = Setup;"]),
}
for label, script in CONSIST.items():
    c = _comp(script)
    bad = []
    n_hit = n_unk = 0
    for d in _DAYS[-25:]:
        codes, bars_m, _short = sser.bars_matrix(_store, d, c.series.window)
        res = sser.evaluate(c, bars_m, {})
        for code, v in zip(codes, res["verdict"]):
            dates, arr = _store["codes"][code]
            k_end = bisect_right(dates, d)
            one = sh._hit_days_series(c, dates, arr, k_end, 1, d, code, "us")
            if v != v:
                n_unk += 1
                ok = one["unknown"] == 1 and one["hits"] == []
            elif v:
                n_hit += 1
                ok = one["hits"] == [str(d)]
            else:
                ok = one["hits"] == [] and one["unknown"] == 0
            if not ok:
                bad.append((code, str(d), v, one["hits"], one["unknown"]))
    check(f"⭐ 扫描与命中日同口径 · {label}(25 天 × 5 只:命中 {n_hit} / 算不出 {n_unk})", not bad, bad[:4])
    check(f"对照用例确实测到了东西 · {label}(有命中也有不命中)", n_hit > 0 and n_hit < 25 * 5, n_hit)

# ═══ 第 4 组 · 扫描当日以结果表为准(2026-09-19 用户:「蓝线一定是基于当前筛选器配置命中的」)═══
from datetime import date as _d  # noqa: E402

check("snapshot_day:daily-bar.time 是 UTC 零点", sh.snapshot_day({sh.BAR_TIME: 1789689600}) == _d(2026, 9, 18))
check("snapshot_day:缺字段 / 非数字 → None", sh.snapshot_day({}) is None and sh.snapshot_day({sh.BAR_TIME: "x"}) is None
      and sh.snapshot_day(None) is None)
base = {"hits": ["2026-09-11"], "evaluated": 250, "unknown": 0, "note": None}
o = sh._pin_scan_day(dict(base), _d(2026, 9, 18), _d(2026, 9, 18))
check("回算没命中扫描当日 → 补标并写明口径差", o["hits"] == ["2026-09-11", "2026-09-18"] and "细微差别" in o["note"]
      and o["scan_day"] == "2026-09-18")
o = sh._pin_scan_day(dict(base, hits=["2026-09-18"]), _d(2026, 9, 18), _d(2026, 9, 18))
check("回算已命中 → 不动、不加说明", o["hits"] == ["2026-09-18"] and o["note"] is None)
check("回算已命中 → 不标 scan_pinned(前端不能挪走真命中)", "scan_pinned" not in o and "scan_note" not in o)
o = sh._pin_scan_day(dict(base), _d(2026, 9, 18), _d(2026, 9, 18))
check("补标 → scan_pinned + scan_note 是补标那句", o.get("scan_pinned") is True and o["scan_note"].startswith("扫描当日 2026-09-18"))
o = sh._pin_scan_day(dict(base), _d(2026, 9, 18), _d(2026, 9, 17))
check("自家日线落后于快照 → 补标并写明日线最新到哪天", "2026-09-18" in o["hits"] and "还没进自家日线库" in o["note"]
      and "2026-09-17" in o["note"])
o = sh._pin_scan_day({"hits": [], "evaluated": 0, "unknown": 0, "note": "自家全市场日线里没有这只票"}, _d(2026, 9, 18), _d(2026, 9, 18))
check("日线里没有这只票 → 只标扫描当日,原说明保留", o["hits"] == ["2026-09-18"] and o["note"].startswith("自家全市场日线里没有")
      and "只能标出扫描当日" in o["note"])
o = sh._pin_scan_day(dict(base), None, _d(2026, 9, 18))
check("快照日期拿不到 → 原样返回", o == base)
check("补标后命中日升序", sh._pin_scan_day(dict(base, hits=["2026-09-19"]), _d(2026, 9, 18), _d(2026, 9, 19))["hits"]
      == ["2026-09-18", "2026-09-19"])

print(f"{passed} passed, {len(fails)} failed")
for x in fails:
    print("FAIL", x)
print("ALL OK" if not fails else "SOME FAILED")
sys.exit(1 if fails else 0)
