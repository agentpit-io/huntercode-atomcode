# -*- coding: utf-8 -*-
"""保存的扫描策略(services/screen_saved.py)· 校验与存取回归用例 —— 不连库(假连接模拟那张表)。

    cd /app && PYTHONPATH=/app python tests/test_screen_saved.py     # 必须 ALL OK
    (本机没有 loguru 时跑不了,在 api 容器里跑)

盯四件事:
  1. _clean 的每条校验(名称 / 市场 / 脚本长度 / 必须有 plot / 排序字段不合法就不存)
  2. 同名 = 覆盖(不占新名额);新名字满 50 个拒绝,覆盖照样允许
  3. 只删自己的;列表只看得到自己的
  4. ⭐ 时间序列脚本原样存、原样读回:input / rec 关键字、条件宿主结构、注释、缩进、CRLF 一个字节都不能动 ——
     保存时做任何「规范化」都会让用户复制回 thinkorswim 时和粘进来的不一样(CLAUDE.md 时间序列第 7 条 / 条件宿主第 6 条)
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import app.services  # noqa: E402,F401

DB = {"rows": [], "next_id": 1, "opened": 0, "closed": 0, "commits": 0}


class FakeCursor:
    def __init__(self):
        self._one = None
        self._all = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        rows = DB["rows"]
        if s.startswith("CREATE TABLE"):
            return
        if s.startswith("SELECT id FROM user_screen_preset WHERE user_id = %s AND name = %s"):
            uid, name = params
            hit = [r for r in rows if r["user_id"] == uid and r["name"] == name]
            self._one = (hit[0]["id"],) if hit else None
            return
        if s.startswith("SELECT COUNT(*) FROM user_screen_preset"):
            self._one = (sum(1 for r in rows if r["user_id"] == params[0]),)
            return
        if s.startswith("INSERT INTO user_screen_preset"):
            uid, name, market, script, sort_by, sort_desc = params
            now = datetime.now(timezone.utc)
            hit = [r for r in rows if r["user_id"] == uid and r["name"] == name]
            if hit:
                r = hit[0]
                r.update(market=market, script=script, sort_by=sort_by, sort_desc=sort_desc, updated_at=now)
            else:
                r = {"id": DB["next_id"], "user_id": uid, "name": name, "market": market, "script": script,
                     "sort_by": sort_by, "sort_desc": sort_desc, "updated_at": now}
                DB["next_id"] += 1
                rows.append(r)
            self._one = (r["id"], r["name"], r["market"], r["script"], r["sort_by"], r["sort_desc"], r["updated_at"])
            return
        if s.startswith("SELECT id, name, market, script, sort_by, sort_desc, updated_at"):
            mine = sorted((r for r in rows if r["user_id"] == params[0]), key=lambda r: r["updated_at"], reverse=True)
            self._all = [(r["id"], r["name"], r["market"], r["script"], r["sort_by"], r["sort_desc"], r["updated_at"])
                         for r in mine]
            return
        if s.startswith("DELETE FROM user_screen_preset"):
            pid, uid = params
            before = len(rows)
            DB["rows"] = [r for r in rows if not (r["id"] == pid and r["user_id"] == uid)]
            self.rowcount = before - len(DB["rows"])
            return
        raise AssertionError("测试没模拟这条 SQL:" + s[:80])

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


class FakeConn:
    def __init__(self):
        DB["opened"] += 1

    def cursor(self):
        return FakeCursor()

    def commit(self):
        DB["commits"] += 1

    def close(self):
        DB["closed"] += 1


fake_db = types.ModuleType("app.services.database")
fake_db.get_conn = FakeConn
sys.modules["app.services.database"] = fake_db

from app.services import screen_saved as sv  # noqa: E402

try:                                   # 保存成功的 INFO 日志会刷屏 50 多行,测试里关掉
    from loguru import logger as _lg
    _lg.remove()
except Exception:                      # noqa: BLE001
    pass

FAILS: list[str] = []
N_OK = 0


def check(name, ok, detail=""):
    global N_OK
    ok = bool(ok)
    print(("OK   " if ok else "FAIL ") + name + (("  · " + str(detail)[:300]) if (detail and not ok) else ""))
    if ok:
        N_OK += 1
    else:
        FAILS.append(name)


MK = {"us", "a", "hk"}
NL = chr(10)
OK_SCRIPT = "def c = close > 20;" + NL + "plot scan = c;"


def err(fn):
    try:
        fn()
    except sv.SavedError as e:
        return str(e)
    return None


def clean(name="我的", market="us", script=OK_SCRIPT, sort_by=None):
    return sv._clean(name, market, script, sort_by, MK)


def reset():
    DB.update(rows=[], next_id=1, opened=0, closed=0, commits=0)


# ═══ 1. _clean 校验 ══════════════════════════════════════════════
check("1 名称为空 → 拒绝", err(lambda: clean(name="")) and "名称" in err(lambda: clean(name="")))
check("1 名称全空白 → 拒绝", err(lambda: clean(name="   ")))
check("1 名称 None → 拒绝(不崩)", err(lambda: clean(name=None)))
check("1 名称 30 字 → 通过", clean(name="字" * sv.MAX_NAME)[0] == "字" * 30)
e = err(lambda: clean(name="字" * (sv.MAX_NAME + 1)))
check("1 名称 31 字 → 拒绝且说出现在几个字", e and "31" in e and "30" in e, e)
check("1 名称前后空白去掉(按去空白后计长度)", clean(name="  " + "字" * 30 + "  ")[0] == "字" * 30)
check("1 市场大小写 / 空白归一", clean(market=" US ")[1] == "us")
check("1 不认识的市场 → 拒绝", err(lambda: clean(market="jp")))
check("1 市场为空 → 拒绝且写明(空)", "(空)" in (err(lambda: clean(market="")) or ""))
check("1 脚本为空 → 拒绝", err(lambda: clean(script="")))
check("1 脚本全空白 → 拒绝", err(lambda: clean(script=" " + NL + "\t")))
e = err(lambda: clean(script="def c = close > 20;"))
check("1 ⭐ 没有 plot → 拒绝", e and "plot" in e, e)
check("1 ⭐ plot 只出现在注释里 → 拒绝", err(lambda: clean(script="def c = close > 20;" + NL + "# plot scan = c;")))
check("1 ⭐ 「plotx」这种名字不算 plot", err(lambda: clean(script="def plotx = close;" + NL + "def c = plotx > 1;")))
check("1 plot 紧凑写法 plot scan=c 通过", clean(script="def c = close > 1;" + NL + "plot scan=c;")[2].endswith("plot scan=c;"))
check("1 plot 在第一行通过", clean(script="plot scan = close > 1;")[2] == "plot scan = close > 1;")
check("1 plot 前有缩进通过", clean(script="def c = close > 1;" + NL + "   plot scan = c;"))
head = "plot scan = close > 1;" + NL + "#"
check("1 脚本正好 20000 字符 → 通过", clean(script=head + "x" * (sv.MAX_SCRIPT - len(head))))
e = err(lambda: clean(script=head + "x" * (sv.MAX_SCRIPT - len(head) + 1)))
check("1 ⭐ 脚本 20001 字符 → 拒绝且写明上限", e and "20000" in e and "20001" in e, e)
check("1 脚本长度按去掉首尾空白后算(首尾空白不占上限)",
      clean(script=NL * 50 + head + "x" * (sv.MAX_SCRIPT - len(head)) + NL * 50))
check("1 排序字段 None 保持 None", clean(sort_by=None)[3] is None)
check("1 排序字段合法原样保留(Perf.W / close|1W)", clean(sort_by="Perf.W")[3] == "Perf.W" and clean(sort_by="close|1W")[3] == "close|1W")
check("1 排序字段前后空白去掉", clean(sort_by="  close ")[3] == "close")
check("1 ⭐ 排序字段不合法 → 置空,不拒绝整次保存",
      clean(sort_by="close; DROP TABLE x")[3] is None and clean(sort_by="收盘价")[3] is None)
check("1 排序字段全空白 → None", clean(sort_by="   ")[3] is None)
check("1 排序字段 65 字符 → None(64 通过)", clean(sort_by="a" * 65)[3] is None and clean(sort_by="a" * 64)[3] == "a" * 64)

# ═══ 2. 保存 / 同名覆盖 / 上限 ═══════════════════════════════════
reset()
r1 = sv.save("u1", "我的", "us", OK_SCRIPT, "close", True, MK)
check("2 新建 → created=True", r1["created"] is True and r1["item"]["name"] == "我的", r1)
check("2 返回的 item 字段齐全", set(r1["item"]) == {"id", "name", "market", "script", "sort_by", "sort_desc", "updated_at"}, r1)
s2 = OK_SCRIPT.replace("20", "30")
r2 = sv.save("u1", " 我的 ", "US", s2, "bogus sort!", False, MK)
check("2 ⭐ 同名(去空白后)→ 覆盖:created=False、id 不变", r2["created"] is False and r2["item"]["id"] == r1["item"]["id"], r2)
check("2 覆盖后只有一条、内容是新的", len(DB["rows"]) == 1 and DB["rows"][0]["script"] == s2
      and DB["rows"][0]["sort_by"] is None and DB["rows"][0]["sort_desc"] is False, DB["rows"])
check("2 每次打开的连接都关掉了", DB["opened"] == DB["closed"], (DB["opened"], DB["closed"]))
sv.save("u2", "我的", "us", OK_SCRIPT, None, True, MK)
check("2 不同用户同名互不覆盖", len(DB["rows"]) == 2)
e = err(lambda: sv.save("u1", "", "us", OK_SCRIPT, None, True, MK))
check("2 校验失败不碰库(连接数不变)", e and DB["opened"] == DB["closed"])

reset()
for i in range(sv.MAX_PER_USER):
    sv.save("u1", f"s{i}", "us", OK_SCRIPT, None, True, MK)
check("2 前提:存满 50 个", len([r for r in DB["rows"] if r["user_id"] == "u1"]) == 50)
opened = DB["opened"]
e = err(lambda: sv.save("u1", "第51个", "us", OK_SCRIPT, None, True, MK))
check("2 ⭐ 满 50 个再存新名字 → 拒绝,并提示同名覆盖不占名额", e and "50" in e and "覆盖" in e, e)
check("2 满额拒绝后连接照样关掉、没写入", DB["opened"] == DB["closed"] and len(DB["rows"]) == 50)
r = sv.save("u1", "s7", "hk", OK_SCRIPT, None, True, MK)
check("2 ⭐ 满 50 个时覆盖已有名字 → 允许", r["created"] is False and len(DB["rows"]) == 50
      and [x for x in DB["rows"] if x["name"] == "s7"][0]["market"] == "hk")
check("2 别的用户不受 u1 满额影响", sv.save("u2", "x", "us", OK_SCRIPT, None, True, MK)["created"] is True)

# ═══ 3. 列表 / 删除只看自己的 ═══════════════════════════════════
reset()
a = sv.save("u1", "A", "us", OK_SCRIPT, None, True, MK)["item"]
b = sv.save("u2", "B", "us", OK_SCRIPT, None, True, MK)["item"]
check("3 列表只含自己的", [x["name"] for x in sv.list_for("u1")] == ["A"] and [x["name"] for x in sv.list_for("u2")] == ["B"])
check("3 列表的 updated_at 是字符串(能进 JSON)", isinstance(sv.list_for("u1")[0]["updated_at"], str))
check("3 ⭐ 删别人的 id → False 且没删掉", sv.delete("u1", b["id"]) is False and len(DB["rows"]) == 2)
check("3 删自己的 → True", sv.delete("u1", a["id"]) is True and len(DB["rows"]) == 1)
check("3 再删一次 → False", sv.delete("u1", a["id"]) is False)

# ═══ 4. 时间序列脚本原样往返 ═══════════════════════════════════
SERIES = NL.join([
    "# 猎杀 FOMO · 条件宿主写法",
    "declare lower;",
    "input minStreak = 3;",
    "input lookback = 10;",
    "input minTotalReturn = 0.80;",
    "",
    "def isGreen = close > open;",
    "rec greenStreak = if isGreen then greenStreak[1] + 1 else 0;",
    "def totalReturn = close / close[lookback] - 1;",
    "def volSpike = volume > Highest(volume[1], 60) * 1.5;",
    "\t# 缩进 + 中文注释 + 行尾空格   ",
    "def FOMO_Setup = greenStreak >= minStreak and totalReturn >= minTotalReturn",
    "    and volSpike",
    "    and isGreen;",
    "plot scan = FOMO_Setup;",
])
reset()
it = sv.save("u1", "FOMO", "us", SERIES, "close", True, MK)["item"]
check("4 ⭐ 时间序列脚本保存后逐字节相同", it["script"] == SERIES)
check("4 ⭐ 列表读回逐字节相同", sv.list_for("u1")[0]["script"] == SERIES)
for kw in ("input minStreak = 3;", "rec greenStreak = ", "declare lower;",
           "def FOMO_Setup = greenStreak >= minStreak and", "plot scan = FOMO_Setup;", "minTotalReturn = 0.80;"):
    check(f"4 读回里保留「{kw.strip()}」", kw in sv.list_for("u1")[0]["script"])
crlf = SERIES.replace(NL, "\r\n")
sv.save("u1", "CRLF", "us", crlf, None, True, MK)
check("4 CRLF 换行原样保留(不改成 LF)", [x for x in sv.list_for("u1") if x["name"] == "CRLF"][0]["script"] == crlf)
padded = NL + "  " + SERIES + NL + NL
sv.save("u1", "PAD", "us", padded, None, True, MK)
check("4 只去掉首尾空白,中间一个字节不动", [x for x in sv.list_for("u1") if x["name"] == "PAD"][0]["script"] == padded.strip())
term_plot = "input n = 5;" + NL + "def up = close > close[n];" + NL + "plot scan = up and (volume > volume[1] or close > 10);"
sv.save("u1", "TERM", "us", term_plot, None, True, MK)
check("4 plot 里带括号 / or 的 term 原样保留", [x for x in sv.list_for("u1") if x["name"] == "TERM"][0]["script"] == term_plot)

print(f"\n{N_OK} passed, {len(FAILS)} failed")
for x in FAILS:
    print("FAIL", x)
print("ALL OK" if not FAILS else "SOME FAILED")
sys.exit(1 if FAILS else 0)
