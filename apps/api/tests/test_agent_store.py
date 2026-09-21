# -*- coding: utf-8 -*-
"""小鹿回测可复用数据(agent_store)纯函数用例,不连库。

    cd apps/api && PYTHONPATH=. python tests/test_agent_store.py
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.quant import agent_store as st          # noqa: E402
from app.services.quant import agent_breakout as ab       # noqa: E402

fails: list[str] = []
passed = 0


def check(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
    else:
        fails.append(f"{name}  {extra}")


c = st.clean({"a": float("nan"), "b": [1.0, float("inf")], "c": {"d": -math.inf, "e": 2}, "f": None, "g": "x"})
check("clean · NaN / Infinity 一律变 None(jsonb 不收),其余原样", c == {"a": None, "b": [1.0, None], "c": {"d": None, "e": 2}, "f": None, "g": "x"}, str(c))
check("clean · 元组变列表(存 JSON)", st.clean((1, 2.5)) == [1, 2.5])

ind = {"close": 104.0, "pivot": 102.0, "prev": {"acc": {"an": 2}}, "gf": {"res": (116.0, "前高"), "sup": {"items": [("均线", 101.0, "x")]}}}
back = st.undump(st.dump(ind))
check("dump / undump · 往返逐位相同(元组也保留)", back == ind and isinstance(back["gf"]["res"], tuple), str(back))
check("dump · 压缩后是 bytes", isinstance(st.dump(ind), bytes))
check("undump · 数据库给的 memoryview 也能解", st.undump(memoryview(st.dump(ind))) == ind)

check("valid · 收盘价一致 → 能用", st.valid(ind, 104.0))
check("valid · 复权基准变了(拆股后收盘价减半)→ 不能用", not st.valid(ind, 52.0))
check("valid · 差 1e-9 的浮点噪声仍算一致", st.valid(ind, 104.0 * (1 + 1e-9)))
check("valid · 指标为空 / 当天没收盘 → 不能用", not st.valid(None, 104.0) and not st.valid(ind, None))

v0 = ab.ind_version()
check("ind_version · 同一套参数版本号稳定", v0 == ab.ind_version(dict(ab.PARAMS)))
check("ind_version · 改了指标里用到的参数(放量倍数)→ 换版本", ab.ind_version(dict(ab.PARAMS, vol_surge=1.4)) != v0)
check("ind_version · 改了指标用不到的参数(财报风控)→ 版本不变", ab.ind_version(dict(ab.PARAMS, earn_block_days=3)) == v0)
check("ind_version · 带算法基准号", v0.startswith(ab.IND_BASE + ":"))
check("ind_version · 参数键都真在 PARAMS 里", all(k in ab.PARAMS for k in ab.IND_PARAM_KEYS))

print(f"{passed} passed, {len(fails)} failed")
for x in fails:
    print("FAIL", x)
print("ALL OK" if not fails else "SOME FAILED")
sys.exit(1 if fails else 0)
