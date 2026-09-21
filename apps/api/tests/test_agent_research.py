# -*- coding: utf-8 -*-
"""研究台判定(agent_research.split_after / judge / decide)· 纯函数,不连库。

    cd apps/api && PYTHONPATH=. python tests/test_agent_research.py

2026-09-18 用户:研究台只留「运行中 / 封存」两列;全年回测和纸上跑合并,真正区分的是**规则冻结之后的新数据**。
这里钉住:30 笔判定只数冻结后的完整交易、冻结后的收益从冻结那天收盘起算、用户研究线只给参考不动状态、
没写冻结日的线过回测关那天自动以回测最后一天为冻结日。
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.quant import agent_research as ar  # noqa: E402

FAILS: list[str] = []
N_OK = 0


def check(name, ok, detail=""):
    global N_OK
    if ok:
        N_OK += 1
        print("OK  ", name)
    else:
        FAILS.append(name)
        print("FAIL", name, detail)


def metrics(n_days=60, start=date(2026, 7, 1), rounds=None, daily=0.001, cycles=None, exp=None, dd=-3.0):
    """造一份 branch_metrics 的返回:n_days 个交易日净值每天涨 daily;rounds = [(entry, pnl)]。"""
    ds = []
    d = start
    while len(ds) < n_days:
        if d.weekday() < 5:
            ds.append(d)
        d += timedelta(days=1)
    eq, v = {}, 100_000.0
    for x in ds:
        eq[x] = v
        v *= 1 + daily
    rs = [{"entry_date": str(e), "exit_date": str(e), "pnl_abs": p} for e, p in (rounds or [])]
    c = len(rs) if cycles is None else cycles
    net = sum(r["pnl_abs"] for r in rs)
    return {"first": ds[0], "last": ds[-1], "equity": eq, "cycles": c, "max_dd_pct": dd,
            "expectancy_net": (exp if exp is not None else (round(net / c, 2) if c else None)), "_rounds": rs}


# ── split_after ─────────────────────────────────────────────────
m = metrics(rounds=[(date(2026, 7, 6), 100), (date(2026, 8, 20), 50), (date(2026, 8, 25), -20), (date(2026, 9, 1), 30)])
ft = str(m["first"] + timedelta(days=50))            # 冻结日落在 8 月下旬某天
o = ar.split_after(m, ft)
check("⭐冻结后只数 entry_date 晚于冻结日的交易", o["cycles"] == sum(1 for r in m["_rounds"] if r["entry_date"] > ft), o)
check("冻结后每笔净损益只用冻结后的交易", o["expectancy_net"] == round(sum(r["pnl_abs"] for r in m["_rounds"] if r["entry_date"] > ft) / o["cycles"], 2))
check("冻结后收益从冻结那天(或之后第一个交易日)收盘起算", o["pnl_pct"] is not None and o["pnl_pct"] > 0 and o["days"] >= 1, o)
check("冻结后的净值只含冻结日及之后", min(o["equity"]) >= date.fromisoformat(ft))
check("冻结当天入场的交易不算冻结后(那天的数据写规则时已经看到)", ar.split_after(m, "2026-07-06")["cycles"] == 3)
check("冻结日晚于最后一天:没有新交易日、0 笔", ar.split_after(m, str(m["last"]))["days"] == 0 and ar.split_after(m, str(m["last"]))["cycles"] == 0)
check("没有冻结日 / 没跑过 → None", ar.split_after(m, None) is None and ar.split_after(None, "2026-09-01") is None)

# ── decide:用户研究线(auto_kill=False)─────────────────────────
user_ln = {"key": "breakout", "status": "backtest", "auto_kill": False, "frozen_through": "2026-08-10"}
good = metrics(rounds=[(date(2026, 7, 6), 300)] * 5 + [(date(2026, 8, 20), 100)] * 3)
v, patch = ar.decide(user_ln, good, None, caught_up=True)
check("⭐用户研究线:过了回测关、冻结后不满 30 笔 → 参考结论写 x/30,状态不动", v["decision"] == "wait" and "3/30" in v["text"]
      and "status" not in patch, (v, patch))
check("用户研究线的文案里没有「淘汰」「纸上跑」这种旧阶段词", "淘汰" not in v["text"] and "纸上跑" not in v["text"], v["text"])
bad = metrics(rounds=[(date(2026, 7, 6), -300)] * 5, exp=-300)
v, patch = ar.decide(user_ln, bad, None, caught_up=True)
check("⭐用户研究线:回测关不达标 → 只给参考,不改状态、不写淘汰", v["decision"] == "advice" and "status" not in patch
      and "仅参考" in v["text"] and not v["text"].rstrip().endswith("淘汰"), (v, patch))
# 冻结前 5 笔大赚、冻结后 30 笔小亏:全段每笔是正的(过回测关),冻结后是负的
oos30 = metrics(n_days=120, rounds=[(date(2026, 7, 6), 2000)] * 5 + [(date(2026, 9, 1), -50)] * 30)
v, patch = ar.decide(dict(user_ln, frozen_through="2026-08-20"), oos30, None, caught_up=True)
check("用户研究线:冻结后满 30 笔且每笔为负 → 参考结论(不改状态)", v["decision"] == "advice" and "status" not in patch and "30" in v["text"], v)
v, patch = ar.decide(user_ln, good, None, caught_up=False)
check("没追到最新交易日 → 等", v["decision"] == "wait" and "进行中" in v["text"])

# ── decide:自动判定的线 ─────────────────────────────────────────
auto_ln = {"key": "x", "status": "backtest", "events": []}
v, patch = ar.decide(auto_ln, good, None, caught_up=True)
check("⭐没写冻结日的线过回测关:以回测最后一天为冻结日并落库", patch.get("frozen_through") == str(good["last"]), patch)
check("过回测关:内部状态 backtest → paper(显示同为运行中),事件里写冻结日", patch.get("status") == "paper"
      and str(good["last"]) in patch["events"][-1]["text"], patch)
check("刚冻结:冻结后 0/30 笔 → 等", v["decision"] == "wait" and "0/30" in v["text"], v)
v, patch = ar.decide(auto_ln, bad, None, caught_up=True)
check("自动线回测关不过 → 淘汰", v["decision"] == "kill" and patch.get("status") == "killed")
paper_ln = {"key": "y", "status": "paper", "frozen_through": "2026-08-20", "events": []}
v, patch = ar.decide(paper_ln, oos30, None, caught_up=True)
check("⭐自动线冻结后满 30 笔、每笔为负 → 淘汰(只看冻结后,全段是正的也不行)", v["decision"] == "kill" and patch.get("status") == "killed"
      and oos30["expectancy_net"] is not None, (v, patch))
many_old = metrics(n_days=120, rounds=[(date(2026, 7, 6), 200)] * 40 + [(date(2026, 9, 1), 50)] * 3)
v, patch = ar.decide(dict(paper_ln), many_old, None, caught_up=True)
check("⭐全段 43 笔但冻结后只有 3 笔 → 仍然等(30 笔不拿回测里的凑)", v["decision"] == "wait" and "3/30" in v["text"], v)

# ── 显示名 / 列 ────────────────────────────────────────────────
check("显示名:回测 / 纸上跑 → 运行中;立项 → 待写引擎", ar.STATUS_TEXT["backtest"] == ar.STATUS_TEXT["paper"] == "运行中"
      and ar.STATUS_TEXT["idea"] == "待写引擎")
check("研究台只有两列", [c[0] for c in ar.COLUMNS] == ["running", "archived"])
check("两条用户研究线写了冻结日", ar.LINES["breakout"]["frozen_through"] == "2026-09-14" and ar.LINES["limitup"]["frozen_through"] == "2026-09-17")

print(f"\n{'ALL OK' if not FAILS else 'SOME FAILED'} · 通过 {N_OK} · 失败 {len(FAILS)}")
sys.exit(1 if FAILS else 0)
