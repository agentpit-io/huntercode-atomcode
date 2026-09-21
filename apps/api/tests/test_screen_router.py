# -*- coding: utf-8 -*-
"""魔法筛选器 · 路由层回归用例(routers/quant.py 的 /screener/*)—— 不连库、不联网、不打上游。

    容器里跑(本机没有 fastapi):
    cd /app && PYTHONPATH=/app python tests/test_screen_router.py     # 必须 ALL OK

## 为什么要有它

额度 / 登录 / 回溯日期这些规则全写在路由里,服务层的用例(test_screen_quota / test_screen_preset_free)碰不到:
  · 扫描脚本报错时退回次数 —— 漏了就是「失败的扫描吃掉用户次数」,不报错、只有用户自己会发现
  · 单条测试不扣扫描次数、也不回结果行 —— 回了行就是「不扣次数的扫描」
  · 原样运行官方示例不扣、改过的照扣;官方示例不回 quota(前端会提示「计 1 次」)
  · `_all_picks`(几千行)必须 pop 掉,不能进响应
  · 「未来」按上海日期判(容器是 UTC,GN-035:上海 0~8 点选今天被拒)
  · AI 识别:模型没调通退回、答了但用不了照计
  · 保存的扫描策略三个接口要登录

做法:真 import 路由模块,挂到一个最小 FastAPI 上,用一个测试中间件按 header 设 request.state.user_id / user_role
(替代 auth 中间件);screen_source / screen_quota / screen_saved 里会连库或打上游的函数全部换成假的并记录调用。
"""
from __future__ import annotations

import sys
import types
from datetime import date

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import quant  # noqa: E402
from app.services import screen_saved  # noqa: E402
from app.services.quant import screen_quota, screen_source, screen_resort, screen_asof  # noqa: E402
from app.services.quant.screen_dsl import ScreenError  # noqa: E402

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


# ─── 测试用 app:header 决定登录身份 ────────────────────────────────
app = FastAPI()


@app.middleware("http")
async def fake_auth(request: Request, call_next):
    uid = request.headers.get("x-test-user")
    request.state.user_id = uid or None
    request.state.user_role = request.headers.get("x-test-role", "user") if uid else None
    return await call_next(request)


app.include_router(quant.router, prefix="/api")
client = TestClient(app, raise_server_exceptions=False)
U = {"x-test-user": "u-test-1"}
ADMIN = {"x-test-user": "u-admin", "x-test-role": "admin"}

# ─── 假额度:内存计数 + 调用记录 ───────────────────────────────────
Q = {"calls": [], "used": {}, "reserve_raise": None, "today": date(2026, 9, 15)}


def f_reserve(uid, role, kind):
    Q["calls"].append(("reserve", kind))
    if Q["reserve_raise"] is not None:
        raise Q["reserve_raise"]
    lim = screen_quota.limit_of(screen_quota.tier_of(role), kind)
    n = Q["used"].get((uid, kind), 0)
    if lim is not None and n >= lim:
        raise screen_quota.QuotaExceeded(kind, screen_quota.info_of(kind, n, lim))
    Q["used"][(uid, kind)] = n + 1
    return screen_quota.info_of(kind, n + 1, lim)


def f_refund(uid, role, kind):
    Q["calls"].append(("refund", kind))
    Q["used"][(uid, kind)] = max(0, Q["used"].get((uid, kind), 0) - 1)
    return None


def f_check_gap(uid, role, now=None):
    Q["calls"].append(("check_gap", uid))


def f_mark_done(uid, now=None):
    Q["calls"].append(("mark_done", uid))


screen_quota.reserve = f_reserve
screen_quota.refund = f_refund
screen_quota.check_gap = f_check_gap
screen_quota.mark_done = f_mark_done
screen_quota.today_sh = lambda: Q["today"]


def reset():
    Q["calls"].clear()
    Q["used"].clear()
    Q["reserve_raise"] = None
    RUN["calls"].clear()
    RUN["raise"] = None
    RUN["matched"] = 3
    screen_resort.clear()


def kinds(op):
    return [k for o, k in Q["calls"] if o == op]


# ─── 假 run_script:记参数,可按需报错 ─────────────────────────────
RUN = {"calls": [], "raise": None, "matched": 3}


def f_run_script(script, market, limit, sort_by, descending, as_of, keep_all):
    RUN["calls"].append({"script": script, "market": market, "limit": limit, "sort_by": sort_by,
                         "as_of": as_of, "keep_all": keep_all})
    if RUN["raise"] is not None:
        raise RUN["raise"]
    picks = [{"code": f"S{i}", "symbol": f"S{i}", "name": f"S{i}", "close": 10.0 + i, "currency": "USD",
              "fields": {"close": 10.0 + i}} for i in range(RUN["matched"])]
    out = {"market": market, "scanned": 100, "matched": len(picks), "skipped_incomplete": 2,
           "returned": min(limit, len(picks)), "picks": picks[:limit], "columns": ["close"], "warnings": []}
    if keep_all:
        out["_all_picks"] = picks
    return out


screen_source.run_script = f_run_script

MY_SCRIPT = "def c = close > 20;\nplot scan = c;"
PRESET = screen_source.PRESETS[0]


def run(body, headers=U):
    return client.post("/api/quant/screener/run", json=body, headers=headers)


def detail(r):
    try:
        return r.json().get("detail")
    except Exception:  # noqa: BLE001
        return None


def has_key_deep(obj, key):
    if isinstance(obj, dict):
        return key in obj or any(has_key_deep(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(has_key_deep(v, key) for v in obj)
    return False


# ═══ 1. 登录拦截 ═══════════════════════════════════════════════════
reset()
r = run({"script": MY_SCRIPT}, headers={})
check("1 run · 没登录 401 且带 need_login", r.status_code == 401 and (detail(r) or {}).get("need_login") is True, r.text)
check("1 run · 没登录不扣次数、不跑脚本", not Q["calls"] and not RUN["calls"])
r = client.post("/api/quant/screener/parse", json={"script": MY_SCRIPT})
check("1 parse · 没登录 401", r.status_code == 401, r.text)
r = client.get("/api/quant/screener/fields?q=close")
check("1 fields · 没登录 401", r.status_code == 401, r.text)
r = client.post("/api/quant/screener/hit-days", json={"script": MY_SCRIPT, "code": "AAPL"})
check("1 hit-days · 没登录 401", r.status_code == 401, r.text)
r = client.get("/api/quant/screener/quota")
check("1 quota · 没登录不报 401,回 login=false", r.status_code == 200 and r.json().get("login") is False, r.text)
r = client.get("/api/quant/screener/meta")
check("1 meta · 没登录也能看(页面骨架公开)", r.status_code == 200 and r.json().get("presets"), r.status_code)

# ═══ 2. 普通扫描:扣 scan、回 quota、_all_picks 不进响应 ═══════════
reset()
r = run({"script": MY_SCRIPT, "limit": 2})
j = r.json()
check("2 自写脚本 · 200", r.status_code == 200, r.text)
check("2 自写脚本 · 扣的是 scan 且只扣一次", kinds("reserve") == ["scan"] and not kinds("refund"), Q["calls"])
check("2 自写脚本 · 先查间隔、结束后记完成", ("check_gap", "u-test-1") in Q["calls"] and ("mark_done", "u-test-1") in Q["calls"])
check("2 自写脚本 · 回 quota(剩 19 次)", (j.get("quota") or {}).get("remaining") == 19, j.get("quota"))
check("2 自写脚本 · 没有 official_preset", "official_preset" not in j)
check("2 自写脚本 · keep_all=True 传给 run_script(列头重排要用)", RUN["calls"][0]["keep_all"] is True)
check("2 ⭐ _all_picks 不进响应(任何层级)", not has_key_deep(j, "_all_picks"), list(j.keys()))
check("2 limit 原样传下去", RUN["calls"][0]["limit"] == 2)
# 列头重排:不扣次数、不跑脚本,用的是刚才缓存的全部命中
Q["calls"].clear()
RUN["calls"].clear()
r = run({"script": MY_SCRIPT, "resort": True, "sort_by": "close", "descending": False, "limit": 2})
check("2 列头重排 · 200 且不扣次数、不跑脚本", r.status_code == 200 and not Q["calls"] and not RUN["calls"],
      (r.status_code, Q["calls"], r.text[:200]))
check("2 列头重排 · 响应里同样没有 _all_picks", not has_key_deep(r.json(), "_all_picks"))
r = run({"script": MY_SCRIPT + "\n# 改过", "resort": True, "sort_by": "close"})
check("2 列头重排 · 脚本变了 409 resort_expired(不自动重跑)",
      r.status_code == 409 and (detail(r) or {}).get("kind") == "resort_expired" and not RUN["calls"], r.text)

# ═══ 3. 脚本报错退回次数 ═════════════════════════════════════════
reset()
RUN["raise"] = ScreenError("第 1 行:不认识的字段 foo")
r = run({"script": MY_SCRIPT})
check("3 ⭐ ScreenError → 400,message 原样 + 说明不计次数",
      r.status_code == 400 and "不认识的字段 foo" in str(detail(r)) and "不计入次数" in str(detail(r)), r.text)
check("3 ⭐ ScreenError → 占的那次 scan 退回", kinds("reserve") == ["scan"] and kinds("refund") == ["scan"], Q["calls"])
check("3 ScreenError → 净用量为 0", Q["used"].get(("u-test-1", "scan"), 0) == 0, Q["used"])
check("3 ScreenError → 仍记完成(间隔从完成起算)", ("mark_done", "u-test-1") in Q["calls"])
reset()
RUN["raise"] = RuntimeError("上游炸了")
r = run({"script": MY_SCRIPT})
check("3 其它异常 → 500 且同样退回 scan", r.status_code == 500 and kinds("refund") == ["scan"], (r.status_code, Q["calls"]))
reset()
RUN["raise"] = ScreenError("x")
r = run({"script": PRESET["script"], "market": PRESET["market"]})
check("3 官方示例报错 → 退回的是 preset 计数(不是 scan)", kinds("reserve") == ["preset"] and kinds("refund") == ["preset"], Q["calls"])

# ═══ 4. 单条测试(probe)═════════════════════════════════════════
reset()
RUN["matched"] = 7
official_called = []
_real_official = screen_source.official_preset_of
screen_source.official_preset_of = lambda s, m: official_called.append(1) or _real_official(s, m)
r = run({"script": MY_SCRIPT, "probe": True, "limit": 100})
j = r.json()
check("4 probe · 200", r.status_code == 200, r.text)
check("4 ⭐ probe · 只回命中数(不回结果行)",
      set(j.keys()) == {"probe", "matched", "scanned", "skipped_incomplete"} and j["matched"] == 7, j)
check("4 ⭐ probe · 不扣 scan,只记 probe", kinds("reserve") == ["probe"], Q["calls"])
check("4 probe · 不查 5 秒间隔、不记完成", not any(o in ("check_gap", "mark_done") for o, _ in Q["calls"]), Q["calls"])
check("4 probe · run_script limit=1、keep_all=False", RUN["calls"][0]["limit"] == 1 and RUN["calls"][0]["keep_all"] is False, RUN["calls"])
check("4 probe · 不判官方示例", not official_called)
check("4 probe · 不回 quota", "quota" not in j)
reset()
RUN["raise"] = ScreenError("坏条件")
r = run({"script": MY_SCRIPT, "probe": True})
check("4 probe 报错 → 400、退回 probe、文案不提「扫描次数」",
      r.status_code == 400 and kinds("refund") == ["probe"] and "不计入次数" not in str(detail(r)), (r.text, Q["calls"]))
screen_source.official_preset_of = _real_official
reset()
r = run({"script": MY_SCRIPT, "probe": True, "resort": False}, headers=U)
check("4 probe 后缓存里没有这张表(否则 probe 结果能被重排出来)",
      run({"script": MY_SCRIPT, "resort": True}).status_code == 409)

# ═══ 5. 官方示例不扣扫描次数 ═══════════════════════════════════════
reset()
r = run({"script": PRESET["script"], "market": PRESET["market"]})
j = r.json()
check("5 ⭐ 原样官方示例 · 记 preset 不记 scan", kinds("reserve") == ["preset"], Q["calls"])
check("5 ⭐ 原样官方示例 · 回 official_preset、不回 quota",
      (j.get("official_preset") or {}).get("key") == PRESET["key"] and "quota" not in j, list(j.keys()))
check("5 原样官方示例 · 响应里没有 _all_picks", not has_key_deep(j, "_all_picks"))
check("5 原样官方示例 · 5 秒间隔照旧", ("check_gap", "u-test-1") in Q["calls"])
reset()
r = run({"preset": PRESET["key"], "market": PRESET["market"]})
check("5 只传 preset key · 同样当官方示例", kinds("reserve") == ["preset"] and r.json().get("official_preset"), (Q["calls"], r.text[:200]))
reset()
r = run({"preset": "no_such_preset"})
check("5 不存在的 preset key → 404 且不扣", r.status_code == 404 and not kinds("reserve"), r.text)
reset()
import re as _re  # noqa: E402
# 改 def 里的数(第一个数字可能在注释里,改注释不算改条件)
m_def = _re.search(r"(?m)^def [^#\n]*?(?<![\w.])(\d+)(?![\w.])", PRESET["script"])
mod = PRESET["script"][:m_def.start(1)] + str(int(m_def.group(1)) + 1) + PRESET["script"][m_def.end(1):]
check("5 (前提)改了 def 里一个数的脚本确实不同", mod != PRESET["script"], m_def.group(0))
r = run({"script": mod, "market": PRESET["market"]})
j = r.json()
check("5 ⭐ 改过的示例 · 照扣 scan、回 quota、无 official_preset",
      kinds("reserve") == ["scan"] and j.get("quota") and "official_preset" not in j, (Q["calls"], list(j.keys())))
reset()
other = [m for m in ("us", "a", "hk") if m != PRESET["market"]][0]
r = run({"script": PRESET["script"], "market": other})
check("5 换了市场的示例 · 照扣 scan", kinds("reserve") == ["scan"], Q["calls"])

# ═══ 6. 额度用满 / 额度库不可用 ═══════════════════════════════════
reset()
Q["used"][("u-test-1", "scan")] = 20
r = run({"script": MY_SCRIPT})
d = detail(r) or {}
check("6 用满 → 429 kind=quota 带 quota,不跑脚本",
      r.status_code == 429 and d.get("kind") == "quota" and d.get("quota", {}).get("remaining") == 0 and not RUN["calls"], r.text)
reset()
Q["reserve_raise"] = RuntimeError("db down")
r = run({"script": MY_SCRIPT})
check("6 额度库连不上 · 普通会员 503、不跑脚本", r.status_code == 503 and not RUN["calls"], r.text)
reset()
Q["reserve_raise"] = RuntimeError("db down")
r = run({"script": MY_SCRIPT}, headers=ADMIN)
check("6 额度库连不上 · 管理员照常扫", r.status_code == 200 and RUN["calls"], r.text[:200])
reset()
screen_quota.check_gap, _gap = (lambda uid, role, now=None: (_ for _ in ()).throw(screen_quota.TooFast(3.2))), screen_quota.check_gap
r = run({"script": MY_SCRIPT})
d = detail(r) or {}
check("6 太快 → 429 kind=too_fast,不扣不跑", r.status_code == 429 and d.get("kind") == "too_fast"
      and not kinds("reserve") and not RUN["calls"], r.text)
screen_quota.check_gap = _gap

# ═══ 7. 时间回溯日期:按上海日期判未来 ═══════════════════════════
reset()
Q["today"] = date(2026, 9, 15)
r = run({"script": MY_SCRIPT, "as_of": "2026-09-16"})
check("7 ⭐ 上海明天 → 400「不能选未来」,不扣不跑",
      r.status_code == 400 and "未来" in str(detail(r)) and not Q["calls"] and not RUN["calls"], r.text)
reset()
r = run({"script": MY_SCRIPT, "as_of": "2026-09-15"})
check("7 ⭐ 上海今天 → 接受,as_of 以 date 传给 run_script",
      r.status_code == 200 and RUN["calls"] and RUN["calls"][0]["as_of"] == date(2026, 9, 15), r.text[:200])
reset()
# GN-035 场景:容器 UTC 还是 9-14,上海已是 9-15。路由必须用 today_sh,不是 date.today()
Q["today"] = date(2026, 9, 16)
r = run({"script": MY_SCRIPT, "as_of": "2026-09-16"})
check("7 ⭐ 判定跟 screen_quota.today_sh 走(上海已到 9-16 就接受 9-16)", r.status_code == 200, r.text[:200])
Q["today"] = date(2026, 9, 15)
reset()
r = run({"script": MY_SCRIPT, "as_of": " 2026-09-01 "})
check("7 前后空格容忍", r.status_code == 200 and RUN["calls"][0]["as_of"] == date(2026, 9, 1), r.text[:200])
reset()
r = run({"script": MY_SCRIPT, "as_of": "2026/09/01"})
check("7 格式不对 → 400 说明要 YYYY-MM-DD,不扣", r.status_code == 400 and "YYYY-MM-DD" in str(detail(r)) and not Q["calls"], r.text)
reset()
r = client.post("/api/quant/screener/hit-days", json={"script": MY_SCRIPT, "code": "AAPL", "as_of": "bad"}, headers=U)
check("7 hit-days 日期格式不对 → 400", r.status_code == 400, r.text)
r = client.post("/api/quant/screener/hit-days", json={"script": MY_SCRIPT, "code": "  "}, headers=U)
check("7 hit-days 没给代码 → 400", r.status_code == 400, r.text)

# ═══ 8. parse:AI 次数 ═══════════════════════════════════════════
PARSE = {"mode": None}


def f_parse(script, market, allow_ai, uid, on_ai, context=None):   # context:追加模式的当前脚本(2026-09-17)
    m = PARSE["mode"]
    if m == "local":
        return {"conditions": [], "script": script}
    if m == "needs_ai":
        raise screen_source.NeedsAI("看不懂这句", kind="text")
    on_ai()
    if m == "ai_ok":
        return {"conditions": [], "script": script}
    if m == "gateway":
        raise ScreenError("调用模型失败:网关 502")
    if m == "bad_answer":
        raise ScreenError("AI 给的条件编译不过")
    if m == "boom":
        raise RuntimeError("意外")
    raise AssertionError(m)


screen_source.parse_script = f_parse


def parse(mode, body=None, headers=U):
    reset()
    PARSE["mode"] = mode
    return client.post("/api/quant/screener/parse", json=body or {"script": "股价大于20", "allow_ai": True}, headers=headers)


r = parse("local")
check("8 本地识别成功 · 不扣 AI、不回 quota", r.status_code == 200 and not Q["calls"] and "quota" not in r.json(), (Q["calls"], r.text))
r = parse("needs_ai")
d = detail(r) or {}
check("8 NeedsAI → 400 can_try_ai + kind,不扣", r.status_code == 400 and d.get("can_try_ai") is True
      and d.get("kind") == "text" and not Q["calls"], r.text)
r = parse("ai_ok")
check("8 AI 成功 · 扣 1 次 ai、回 quota", kinds("reserve") == ["ai"] and not kinds("refund")
      and (r.json().get("quota") or {}).get("remaining") == 9, (Q["calls"], r.text))
r = parse("gateway")
check("8 ⭐ 调用模型失败 → 退回 ai,文案说明不计入", r.status_code == 400 and kinds("refund") == ["ai"]
      and "不计入" in str(detail(r)), (Q["calls"], r.text))
check("8 ⭐ 调用模型失败 → 净用量 0", Q["used"].get(("u-test-1", "ai"), 0) == 0, Q["used"])
r = parse("bad_answer")
d = detail(r) or {}
check("8 模型答了但用不了 → 照计(不退),detail 带 quota 与说明",
      r.status_code == 400 and not kinds("refund") and d.get("quota", {}).get("remaining") == 9
      and "计 1 次" in str(d.get("quota_note")), (Q["calls"], r.text))
r = parse("boom")
check("8 意外异常 → 500 且退回 ai", r.status_code == 500 and kinds("refund") == ["ai"], (r.status_code, Q["calls"]))
reset()
PARSE["mode"] = "ai_ok"
Q["used"][("u-test-1", "ai")] = 10
r = client.post("/api/quant/screener/parse", json={"script": "股价大于20", "allow_ai": True}, headers=U)
check("8 AI 用满 → 429 kind=quota", r.status_code == 429 and (detail(r) or {}).get("kind") == "quota", r.text)
r = parse("local", body={"preset": "no_such"})
check("8 parse 不存在的 preset → 404", r.status_code == 404, r.text)

# ═══ 9. 保存的扫描策略 ═══════════════════════════════════════════
SV = {"calls": [], "list_raise": None, "save_raise": None, "delete_ok": True}


def f_list(uid):
    SV["calls"].append(("list", uid))
    if SV["list_raise"]:
        raise SV["list_raise"]
    return [{"id": 1, "name": "我的", "market": "us", "script": MY_SCRIPT, "sort_by": None, "sort_desc": True, "updated_at": None}]


def f_save(uid, name, market, script, sort_by, sort_desc, markets):
    SV["calls"].append(("save", uid, name, market, script, sort_by, sort_desc, markets))
    if SV["save_raise"]:
        raise SV["save_raise"]
    return {"item": {"id": 2, "name": name}, "created": True}


def f_delete(uid, pid):
    SV["calls"].append(("delete", uid, pid))
    return SV["delete_ok"]


screen_saved.list_for = f_list
screen_saved.save = f_save
screen_saved.delete = f_delete
BODY = {"name": "我的", "market": "us", "script": MY_SCRIPT, "sort_by": "close", "sort_desc": False}

r = client.get("/api/quant/screener/saved")
check("9 ⭐ 列表 · 没登录 401", r.status_code == 401 and not SV["calls"], r.text)
r = client.post("/api/quant/screener/saved", json=BODY)
check("9 ⭐ 保存 · 没登录 401,不写库", r.status_code == 401 and not SV["calls"], r.text)
r = client.delete("/api/quant/screener/saved/1")
check("9 ⭐ 删除 · 没登录 401,不删", r.status_code == 401 and not SV["calls"], r.text)
r = client.get("/api/quant/screener/saved", headers=U)
check("9 列表 · 登录后按自己的 uid 查,带上限",
      r.status_code == 200 and SV["calls"] == [("list", "u-test-1")] and r.json()["max"] == screen_saved.MAX_PER_USER
      and r.json()["items"][0]["name"] == "我的", r.text)
SV["calls"].clear()
r = client.post("/api/quant/screener/saved", json=BODY, headers=U)
c0 = SV["calls"][0] if SV["calls"] else ()
check("9 保存 · 参数原样传给 screen_saved.save(uid 取自登录)",
      r.status_code == 200 and c0[:7] == ("save", "u-test-1", "我的", "us", MY_SCRIPT, "close", False), c0)
check("9 保存 · 允许的市场集合 = screen_source.MARKETS", c0 and c0[7] == set(screen_source.MARKETS.keys()), c0)
SV["calls"].clear()
SV["save_raise"] = screen_saved.SavedError("脚本里没有 plot 语句")
r = client.post("/api/quant/screener/saved", json=BODY, headers=U)
check("9 保存校验失败 → 400 message 原样", r.status_code == 400 and "plot" in str(detail(r)), r.text)
SV["save_raise"] = None
r = client.post("/api/quant/screener/saved", json={"name": "x"}, headers=U)
check("9 保存缺字段 → 422(不进 save)", r.status_code == 422, r.status_code)
SV["list_raise"] = RuntimeError("db")
r = client.get("/api/quant/screener/saved", headers=U)
check("9 列表读库失败 → 503", r.status_code == 503, r.text)
SV["list_raise"] = None
SV["calls"].clear()
r = client.delete("/api/quant/screener/saved/7", headers=U)
check("9 删除 · 按 (uid, id) 删", r.status_code == 200 and SV["calls"] == [("delete", "u-test-1", 7)], (r.text, SV["calls"]))
SV["delete_ok"] = False
r = client.delete("/api/quant/screener/saved/8", headers=U)
check("9 删别人的 / 不存在 → 404(不区分)", r.status_code == 404, r.text)
r = client.delete("/api/quant/screener/saved/abc", headers=U)
check("9 id 不是整数 → 422", r.status_code == 422, r.status_code)

# ═══ 10. quota / history-range ═══════════════════════════════════
screen_quota.snapshot = lambda uid, role: {"login": True, "uid": uid, "role": role}
r = client.get("/api/quant/screener/quota", headers=ADMIN)
check("10 quota · 登录后按 uid/role 查", r.status_code == 200 and r.json() == {"login": True, "uid": "u-admin", "role": "admin"}, r.text)
r = client.get("/api/quant/screener/history-range?market=xx")
check("10 history-range · 不认识的市场 400", r.status_code == 400, r.text)
screen_asof.history_range = lambda m: (_ for _ in ()).throw(RuntimeError("db"))
r = client.get("/api/quant/screener/history-range?market=us")
check("10 history-range · 日线库连不上 503(不裸 500)", r.status_code == 503, r.text)
screen_asof.history_range = lambda m: {"min_date": "2024-01-02", "max_date": "2026-09-14"}
r = client.get("/api/quant/screener/history-range?market=us")
check("10 history-range · 公开、带 market 与 note", r.status_code == 200 and r.json().get("market") == "us" and r.json().get("note"), r.text)

print(f"\n{N_OK} passed, {len(FAILS)} failed")
for x in FAILS:
    print("FAIL", x)
print("ALL OK" if not FAILS else "SOME FAILED")
sys.exit(1 if FAILS else 0)
