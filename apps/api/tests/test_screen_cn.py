# -*- coding: utf-8 -*-
"""扫描结果中文化(screen_cn)· 不联网。

    cd apps/api && PYTHONPATH=. python tests/test_screen_cn.py

2026-09-19 用户:港股结果的名称、板块和 A 股一样换成中文。这里钉住:港股按 5 位代码查清单、
板块同一套对照表、查不到保留英文(不留空不编)、美股不动、后台刷新拿到半截数据不覆盖。
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.quant import screen_cn as cn  # noqa: E402

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


# 测试里不许起后台刷新线程去联网:假装刚刷新过
for mk in cn._MARKETS:
    cn._state[mk]["loaded_at"] = time.time()


def pick(code, name, sector):
    return {"code": code, "name": name, "fields": {"description": name, "name": code, "sector": sector}}


# ── 清单文件 ──
d = json.loads(cn._MARKETS["hk"]["baseline"].read_text(encoding="utf-8"))
codes = [x["code"] for x in d["items"]]
check("港股清单超过 2000 只", len(codes) > 2000, len(codes))
check("港股清单代码都是 5 位数字", all(len(c) == 5 and c.isdigit() for c in codes))
check("港股清单没有空名字", all(x["name"].strip() for x in d["items"]))

# ── 港股 ──
p = cn.localize_pick(pick("03968", "China Merchants Bank Co., Ltd. Cla", "Finance"), "hk")
check("⭐港股名称换中文(截图里的招商银行)", p["name"] == "招商银行" and p["fields"]["description"] == "招商银行", p)
check("⭐港股板块换中文", p["fields"]["sector"] == "金融", p)
check("fields.name 是代码时不动", p["fields"]["name"] == "03968")
for code, want in (("00300", "美的集团"), ("02359", "药明康德"), ("00700", "腾讯控股"), ("00992", "联想集团")):
    check(f"港股 {code} → {want}", cn.localize_pick(pick(code, "x", "Finance"), "hk")["name"] == want)
check("代码少了前导零也认(700 → 00700)", cn.localize_pick(pick("700", "Tencent", "x"), "hk")["name"] == "腾讯控股")
q = cn.localize_pick(pick("99998", "Some Trust", "Miscellaneous"), "hk")
check("⭐清单里没有的港股保留英文名,不留空不编", q["name"] == "Some Trust" and q["fields"]["description"] == "Some Trust", q)
check("没有的票板块照样翻", q["fields"]["sector"] == "其他")
r = cn.localize_pick(pick("00005", "HSBC", "Brand New Sector"), "hk")
check("对照表里没有的新板块原样显示英文", r["fields"]["sector"] == "Brand New Sector")

# ── A 股不受影响 ──
a = cn.localize_a_pick(pick("000001", "Ping An Bank", "Finance"))
check("A 股老接口照旧", a["name"] == "平安银行" and a["fields"]["sector"] == "金融", a)
a2 = cn.localize_pick(pick("000001", "Ping An Bank", "Finance"), "a")
check("A 股新接口同结果", a2["name"] == "平安银行")
check("港股代码不会去 A 股清单里查(00001 ≠ 000001)", cn.localize_pick(pick("00001", "CK Hutchison", "x"), "hk")["name"] == "长和")

# ── 美股不动 ──
u = cn.localize_pick(pick("AAPL", "Apple Inc.", "Electronic Technology"), "us")
check("⭐美股名称、板块都不动", u["name"] == "Apple Inc." and u["fields"]["sector"] == "Electronic Technology", u)

# ── 后台刷新 ──
cfg = cn._MARKETS["hk"]
orig = cfg["fetch"]
cfg["fetch"] = lambda: {"03968": "假名字"}
cn._refresh("hk")
check("⭐刷新只拿到半截数据 → 不覆盖", cn.localize_pick(pick("03968", "x", "x"), "hk")["name"] == "招商银行")
big = {f"{i:05d}": f"股{i}" for i in range(1, 2500)}
big["03968"] = "招商银行(新)"
cfg["fetch"] = lambda: big
cn._refresh("hk")
check("刷新拿到全量 → 用新名字", cn.localize_pick(pick("03968", "x", "x"), "hk")["name"] == "招商银行(新)")
check("刷新完 refreshing 复位", cn._state["hk"]["refreshing"] is False)


def boom():
    raise RuntimeError("网络挂了")


cfg["fetch"] = boom
cn._refresh("hk")
check("刷新抛错不影响已有名字", cn.localize_pick(pick("03968", "x", "x"), "hk")["name"] == "招商银行(新)"
      and cn._state["hk"]["refreshing"] is False)
cfg["fetch"] = orig

print(f"\n{'ALL OK' if not FAILS else 'SOME FAILED'} · 通过 {N_OK} · 失败 {len(FAILS)}")
sys.exit(1 if FAILS else 0)
