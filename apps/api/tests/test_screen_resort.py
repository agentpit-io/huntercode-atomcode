"""列头排序缓存(screen_resort)—— 纯逻辑,不连库不联网。

    cd apps/api && PYTHONPATH=. python tests/test_screen_resort.py     # 必须 ALL OK

盯的是:重排结果对、空值恒最后、只认同一张表、按用户隔离、过期 / 太大 / 挤出时明确报过期(不静默给旧表)。
"""
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "screen_resort", os.path.join(os.path.dirname(_HERE), "app", "services", "quant", "screen_resort.py"))
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)

failed = 0


def check(name, ok, extra=""):
    global failed
    if ok:
        print("PASS", name)
    else:
        failed += 1
        print("FAIL", name, extra)


def pick(code, close, **fields):
    f = dict(fields)
    f["close"] = close
    return {"code": code, "close": close, "fields": f}


PICKS = [pick("A", 10, pe=5, sector="Tech"), pick("B", 30, pe=None, sector="Bank"),
         pick("C", 20, pe=9, sector=None), pick("D", 40, pe=1, sector="Auto")]
OUT = {"market": "us", "matched": 4, "columns": ["pe", "sector"], "picks": PICKS[:2],
       "quota": {"remaining": 3}, "warnings": ["w"]}
K = sr.key_of("def c = close > 1;\nplot scan = c;", "us", None)

sr.clear()
sr.put("u1", K, OUT, PICKS, now=100.0)
r = sr.resort("u1", K, "pe", True, 100, now=101.0)
check("按 pe 降序,空值在最后", [p["code"] for p in r["picks"]] == ["C", "A", "D", "B"], str([p["code"] for p in r["picks"]]))
r = sr.resort("u1", K, "pe", False, 100, now=101.0)
check("按 pe 升序,空值仍在最后", [p["code"] for p in r["picks"]] == ["D", "A", "C", "B"])
r = sr.resort("u1", K, "close", True, 2, now=101.0)
check("按收盘价降序只回前 N 只", [p["code"] for p in r["picks"]] == ["D", "B"] and r["returned"] == 2)
r = sr.resort("u1", K, "sector", False, 100, now=101.0)
check("文字列按文字排、空值最后", [p["code"] for p in r["picks"]] == ["D", "B", "A", "C"])
check("返回体沿用原表的统计与提示,标 resorted", r["matched"] == 4 and r["warnings"] == ["w"] and r["resorted"] is True)
check("缓存的返回体里没有额度(重排不涉及次数)", "quota" not in r)
check("重排不改缓存里的原顺序", [p["code"] for p in sr.resort("u1", K, None, True, 100, now=101.0)["picks"]] == ["A", "B", "C", "D"])

try:
    sr.resort("u1", K, "no_such", True, 100, now=101.0)
    check("不在列里的排序字段报错", False)
except ValueError:
    check("不在列里的排序字段报错", True)

for name, uid, key, now in [("别的用户取不到", "u2", K, 101.0),
                             ("条件改了(脚本不同)算过期", "u1", sr.key_of("plot scan = close > 2;", "us", None), 101.0),
                             ("切了市场算过期", "u1", sr.key_of("def c = close > 1;\nplot scan = c;", "hk", None), 101.0),
                             ("回溯日不同算过期", "u1", sr.key_of("def c = close > 1;\nplot scan = c;", "us", "2026-08-14"), 101.0),
                             ("超过 10 分钟算过期", "u1", K, 100.0 + sr.TTL_S + 1)]:
    try:
        sr.resort(uid, key, "pe", True, 100, now=now)
        check(name, False)
    except sr.Expired as e:
        check(name, "重新运行" in str(e))

sr.clear()
sr.put("big", K, OUT, [pick(str(i), i) for i in range(sr.MAX_ROWS + 1)], now=1.0)
try:
    sr.resort("big", K, "close", True, 100, now=2.0)
    check("超过 1 万行不缓存", False)
except sr.Expired:
    check("超过 1 万行不缓存", True)
sr.put("big", K, OUT, PICKS, now=3.0)
sr.put("big", K, OUT, [pick(str(i), i) for i in range(sr.MAX_ROWS + 1)], now=4.0)
try:
    sr.resort("big", K, "close", True, 100, now=5.0)
    check("新表太大时,旧表也不能留(已经不是眼前那张)", False)
except sr.Expired:
    check("新表太大时,旧表也不能留(已经不是眼前那张)", True)

sr.clear()
for i in range(sr.MAX_USERS + 5):
    sr.put(f"u{i}", K, OUT, PICKS, now=float(i))
check("缓存用户数有上限", len(sr._cache) <= sr.MAX_USERS, len(sr._cache))
try:
    sr.resort("u0", K, "pe", True, 100, now=50.0)
    check("最早的被挤出后算过期", False)
except sr.Expired:
    check("最早的被挤出后算过期", True)

print("ALL OK" if not failed else "SOME FAILED (%d)" % failed)
sys.exit(1 if failed else 0)
