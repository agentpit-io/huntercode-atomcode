"""魔法筛选器 · 会员额度与上游保护(不连库、不联网)。

    cd apps/api && PYTHONPATH=. python tests/test_screen_quota.py     # 必须 ALL OK

盯三件事:
  1. 档位 / 剩余次数的算术(管理员不限、普通会员 AI 10 / 扫描 20)
  2. 两次扫描至少隔 5 秒,从上一次**完成**起算
  3. fetch_rows 的缓存 / 同 key 合并 / 失败缓存 / 返回副本 —— 5000 会员放开后上游能不能扛住就靠它
"""
import sys
import threading
import time
import types

# 不连库:screen_quota 只在 reserve/refund/snapshot 里 get_conn,这里测不到的部分由线上验证覆盖
if "app.services.database" not in sys.modules:
    fake_db = types.ModuleType("app.services.database")
    fake_db.get_conn = lambda: (_ for _ in ()).throw(RuntimeError("测试不连库"))
    sys.modules["app.services.database"] = fake_db

from app.services.quant import screen_quota as q  # noqa: E402

failed = 0


def check(name, ok, extra=""):
    global failed
    if ok:
        print("PASS", name)
    else:
        failed += 1
        print("FAIL", name, extra)


# ─── 1. 档位 ───────────────────────────────────────────────
check("管理员不限", q.tier_of("admin") is None and q.limit_of(None, "scan") is None)
check("普通用户是普通会员", q.tier_of("user") == "normal" and q.tier_of(None) == "normal")
check("普通会员 AI 每天 10 次", q.limit_of("normal", "ai") == 10)
check("普通会员扫描每天 20 次", q.limit_of("normal", "scan") == 20)
i = q.info_of("scan", 7, 20)
check("剩余 = 上限 − 已用", i["remaining"] == 13 and i["used"] == 7 and not i["unlimited"])
check("超用不出负数", q.info_of("ai", 12, 10)["remaining"] == 0)
check("不限时 remaining 为 None(不是 0,也不是一个编出来的大数)",
      q.info_of("scan", 3, None)["remaining"] is None and q.info_of("scan", 3, None)["unlimited"])
check("重置时间写的是明天 0 点上海时间", "00:00" in i["reset_at"] and "上海" in i["reset_at"])
e = q.QuotaExceeded("ai", q.info_of("ai", 10, 10))
check("用满的提示里带次数与重置时间", "10 次" in str(e) and "明天" in str(e))

# ─── 2. 扫描间隔 ───────────────────────────────────────────
q._last_scan.clear()
q.check_gap("u1", "user", now=100.0)
try:
    q.check_gap("u1", "user", now=103.0)
    check("3 秒内第二次扫描被挡", False)
except q.TooFast as te:
    check("3 秒内第二次扫描被挡", 1.9 < te.wait_s < 2.1, te.wait_s)
q.mark_done("u1", now=104.0)
try:
    q.check_gap("u1", "user", now=107.0)
    check("间隔从上一次完成起算(完成后 3 秒仍挡)", False)
except q.TooFast:
    check("间隔从上一次完成起算(完成后 3 秒仍挡)", True)
try:
    q.check_gap("u1", "user", now=109.5)
    check("完成 5 秒后放行", True)
except q.TooFast:
    check("完成 5 秒后放行", False)
try:
    q.check_gap("u2", "user", now=109.6)
    check("不同用户互不影响", True)
except q.TooFast:
    check("不同用户互不影响", False)
try:
    q.check_gap("adm", "admin", now=1.0)
    q.check_gap("adm", "admin", now=1.1)
    check("管理员不受间隔限制", True)
except q.TooFast:
    check("管理员不受间隔限制", False)

# ─── 3. fetch_rows 上游保护 ────────────────────────────────
from app.services.quant import screen_source as ss  # noqa: E402
from app.services.quant.screen_dsl import ScreenError  # noqa: E402

calls = []
mode = {"fail": False, "sleep": 0.0}


def fake_upstream(md, market_key, cols, limit_scan, extra_filter):
    calls.append(tuple(cols))
    if mode["sleep"]:
        time.sleep(mode["sleep"])
    if mode["fail"]:
        raise ScreenError("扫描源返回 HTTP 502。")
    return [{"_code": "AAPL", "close": 1.0}, {"_code": "MSFT", "close": 2.0}], 2


ss._fetch_upstream = fake_upstream


def reset():
    calls.clear()
    ss._rows_cache.clear()
    ss._rows_fail.clear()
    ss._inflight.clear()
    mode["fail"] = False
    mode["sleep"] = 0.0


reset()
r1, t1 = ss.fetch_rows("us", ["close"])
r2, _ = ss.fetch_rows("us", ["close"])
check("90 秒内同一组列只打一次上游", len(calls) == 1 and t1 == 2)
r1[0]["rs_rating"] = 99
check("返回的是副本:往行里补字段不串到下一个请求", "rs_rating" not in ss.fetch_rows("us", ["close"])[0][0])
# 注意 close / volume 在 ALWAYS_COLS 里,永远会带上 —— 拿它们测「列不同」测的其实是同一组列
ss.fetch_rows("us", ["close", "RSI7"])
check("列不同是另一份缓存", len(calls) == 2, len(calls))
ss.fetch_rows("us", ["RSI7", "Perf.W"])
ss.fetch_rows("us", ["Perf.W", "RSI7"])
check("列顺序不同算同一份", len(calls) == 3, len(calls))

reset()
mode["sleep"] = 0.3
out = []
ths = [threading.Thread(target=lambda: out.append(ss.fetch_rows("us", ["RSI"])[1])) for _ in range(12)]
[t.start() for t in ths]
[t.join() for t in ths]
check("12 个并发的相同请求只打一次上游(single-flight)", len(calls) == 1 and out == [2] * 12, (len(calls), out))

reset()
mode["fail"] = True
errs = 0
for _ in range(5):
    try:
        ss.fetch_rows("us", ["close"])
    except ScreenError:
        errs += 1
check("上游失败 10 秒内不重复去撞(失败也缓存)", errs == 5 and len(calls) == 1, (errs, len(calls)))
mode["fail"] = False
ss._rows_fail.clear()
check("失败缓存过期后能恢复", ss.fetch_rows("us", ["close"])[1] == 2 and len(calls) == 2)

reset()
old_ttl = ss._ROWS_TTL
ss._ROWS_TTL = 0.0
ss.fetch_rows("us", ["close"])
ss.fetch_rows("us", ["close"])
ss._ROWS_TTL = old_ttl
check("缓存过期后重新取数", len(calls) == 2)

reset()
for n in range(ss._ROWS_MAX_KEYS + 6):
    ss.fetch_rows("us", ["col%d" % n])
check("缓存份数有上限(防内存涨)", len(ss._rows_cache) <= ss._ROWS_MAX_KEYS, len(ss._rows_cache))

reset()
mode["sleep"] = 0.25
slots = ss._UPSTREAM_SLOTS
peak = {"now": 0, "max": 0}
lock = threading.Lock()


def counting(md, market_key, cols, limit_scan, extra_filter):
    with lock:
        peak["now"] += 1
        peak["max"] = max(peak["max"], peak["now"])
    time.sleep(0.2)
    with lock:
        peak["now"] -= 1
    return [], 0


ss._fetch_upstream = counting
ths = [threading.Thread(target=lambda n=n: ss.fetch_rows("us", ["x%d" % n])) for n in range(9)]
[t.start() for t in ths]
[t.join() for t in ths]
check("同时去上游的不超过 3 路", peak["max"] <= 3, peak)

print("ALL OK" if not failed else "SOME FAILED (%d)" % failed)
sys.exit(1 if failed else 0)
