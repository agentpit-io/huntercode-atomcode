"""扫描筛选 · 一只票在过去一年里「哪些天会被当前脚本命中」(用户 2026-09-13 要求)。

筛选器结果表悬停日K 上的淡蓝色竖带就是它:把**这次运行的脚本**放回过去 250 个交易日,每天按那天收盘重算
这只票的字段、求值一次。口径与「时间回溯」(`screen_asof.build_rows`)逐条相同 —— 同一份日线、同一份拆股修正、
同一套字段算法、同一个 RS 排名池 —— 只是只算一只票,所以一只票一年大约 1~3 秒,而不是 250 次全市场回溯。

## 为什么 RS 评级要单独建一张表

RS 评级是**全市场排名**:算某天这只票的评级,要知道那天排名池里所有票的 RS Raw。逐日跑 build_rows 太重,
这里按市场建一张「日期 → 当天排名池里全部 RS Raw(升序)」的表,和整窗日线一起按缓存周期复用;
单只票的评级用二分查名次,并列取平均名次 —— 与 `screen_rs.rs_ratings` 逐位相同(tests/test_screen_hits.py 盯着)。
算术顺序照抄 `rs_history.rs_raw_exact`,浮点结果逐位一致,二分才查得到自己。

## 算不出的天

脚本用到**没有历史**的字段(市值 / 财务,`screen_asof.reconstructable` 为假)时,那些条件每天都是「算不出」,
不算命中也不算没命中,计进 `unknown` 并在 `note` 里点名 —— 不许当成「那天没命中」,
否则图上一条蓝线都没有,用户会以为这只票过去一年从没满足过条件。

## 扫描当天以结果表为准(2026-09-19 用户:「显示命中的蓝色线一定是基于当前筛选器配置命中的,这点原则一直不能变」)

快照类脚本(不带 `[1]` 这类写法)的结果表用**扫描源快照**求值,这里用**自家日线**回算 —— 两份数据在扫描当天可能对不上:
① 自家日线还没有快照那一天(每晚任务没跑到 / 本地版不拉数):那天根本没法回算,结果是「一年命中 0 天」;
② 两边数值有细微差别(均线差几分钱卡在边界、个别票自家日线的量或高低价有问题)。
2026-09-19 本地实测「放量突破」134 只:94 只扫描当天有蓝线,18 只没有(②),22 只自家日线里根本没有这只票。
所以 `in_result=True`(这只票就在这次实时扫描的结果表里)时,**快照所属交易日(扫描源 `daily-bar.time`)一律记为命中**,
自家日线回算和它不一致就在 note 里写明;过去的日子仍按自家日线回算(扫描源没有历史快照,只能这样)。
时间回溯(as_of)与时间序列脚本本来就和结果表同一份日线同一套算法,不走这条。
"""
from __future__ import annotations

import hashlib
import threading
import time
from bisect import bisect_left, bisect_right
from datetime import date

DAYS = 250
_TTL = 20 * 60
_lock = threading.Lock()
_snap_cache: dict = {}      # 市场 → (时间, 快照行, {code: 行})
_rs_cache: dict = {}        # 市场 → (整窗日线 loaded_at, {日期: (升序 RS Raw, 排名池只数)})
_hit_cache: dict = {}       # (市场, 脚本哈希, 代码, 截止日, 天数) → (loaded_at, 结果)
_RS_WEIGHTS = ((63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2))


def rating_of(sorted_raws: list[float], pool_n: int, value: float | None, threshold: float) -> int | None:
    """某天排名池全部 RS Raw(升序、不含 None)里,value 的 RS 评级(1–99)。

    与 screen_rs.rs_ratings 同一口径:覆盖率 = 有 Raw 的只数 ÷ 排名池只数,低于门槛整天不给;
    并列取平均名次,round(pct × 98 + 1)。value 不在表里 → None(不猜名次)。
    """
    n = len(sorted_raws)
    if value is None or n == 0 or pool_n <= 0 or n / pool_n < threshold:
        return None
    i = bisect_left(sorted_raws, value)
    j = bisect_right(sorted_raws, value) - 1
    if j < i:
        return None
    pct = ((i + 1) + (j + 1)) / 2.0 / n
    return int(round(pct * 98 + 1))


def raw_at(closes, i: int) -> float | None:
    """收盘序列第 i 根的精确 RS Raw。算术顺序照抄 rs_history.rs_raw_exact(浮点逐位一致)。"""
    if i < 252:
        return None
    last = float(closes[i])
    if last <= 0:
        return None
    tot = 0.0
    for days, w in _RS_WEIGHTS:
        base = float(closes[i - days])
        if base <= 0:
            return None
        tot += w * (last / base - 1.0)
    return tot


def _snapshot(market_raw: str, market_key: str, has_field):
    """今天的快照,只用静态列 + 拆股锚点 + 排名池两列(与 run_script 的回溯分支同一份列)。"""
    from app.services.quant import screen_source, screen_asof, rs_history
    now = time.time()
    with _lock:
        hit = _snap_cache.get(market_key)
    if hit and now - hit[0] < _TTL:
        return hit[1], hit[2]
    cols = [x for x in screen_asof.STATIC_COLS if x not in screen_source.ALWAYS_COLS and has_field(x)]
    cols += list(rs_history._PERF_COLS) + ["exchange", "market_cap_basic"]
    if has_field(BAR_TIME):
        cols.append(BAR_TIME)          # 快照是哪个交易日的(扫描当天以结果表为准要用)
    rows, _ = screen_source.fetch_rows(market_raw, cols)
    perf = {r["_code"]: r for r in rows}
    with _lock:
        _snap_cache[market_key] = (now, rows, perf)
    return rows, perf


BAR_TIME = "daily-bar.time"


def snapshot_day(row: dict | None) -> date | None:
    """扫描源快照属于哪个交易日:`daily-bar.time` 是当天日 K 的 UTC 零点时间戳(实测 MPC / AAPL 2026-09-18 → 1789689600)。"""
    from datetime import datetime, timezone
    t = (row or {}).get(BAR_TIME)
    if not isinstance(t, (int, float)) or t <= 0:
        return None
    return datetime.fromtimestamp(t, tz=timezone.utc).date()


def _pin_scan_day(out: dict, scan_day: date | None, store_last) -> dict:
    """扫描当天以结果表为准:把快照所属交易日记为命中,回算和它不一致时写明原因。"""
    if scan_day is None:
        return out
    d = str(scan_day)
    hits = list(out.get("hits") or [])
    notes = [out["note"]] if out.get("note") else []
    out = dict(out, scan_day=d)
    if d in hits:
        return out
    if store_last is not None and scan_day > store_last:
        notes.append(f"扫描当日 {d} 的日线还没进自家日线库(最新到 {store_last}),这一天按扫描结果标为命中")
    elif out.get("evaluated"):
        notes.append(f"扫描当日 {d} 按扫描结果标为命中;用自家日线回算这一天不满足"
                     "(扫描源快照与自家日线的均线 / 成交量 / 高低价有细微差别,卡在条件边界上)")
    else:
        notes.append(f"只能标出扫描当日 {d}(按扫描结果),更早的日子算不出")
    hits.append(d)
    out["hits"] = sorted(hits)
    out["scan_pinned"] = True          # 这一天是按结果表补标的(不是回算命中),前端可按日K 最后一根挪位
    out["scan_note"] = notes[-1]       # 补标说明单独给,前端常驻显示
    out["note"] = ";".join(notes)
    return out


def _rs_table(market_key: str, store: dict, snap: dict) -> dict:
    """{日期: (那天排名池里全部 RS Raw 升序, 排名池只数)},只建最近 DAYS+5 个交易日。

    排名池口径同 build_rows:在排名池里(交易所上市 + 市值门槛)、那天有收盘、且已有 253 根以上日线
    (次新股不进分母)。Raw 算不出(收盘非正)的票在分母里、不在列表里。
    """
    from app.services.quant import screen_rs
    with _lock:
        hit = _rs_cache.get(market_key)
    if hit and hit[0] == store["loaded_at"]:
        return hit[1]
    window = sorted(store["bench"])[-(DAYS + 5):]
    want = set(window)
    raws: dict = {d: [] for d in window}
    pool: dict = {d: 0 for d in window}
    first = window[0] if window else None
    for code, (dates, arr) in store["codes"].items():
        s = snap.get(code)
        if not s or not screen_rs.in_population(s, market_key) or len(dates) <= 252:
            continue
        closes = arr[:, 0]
        for i in range(max(bisect_left(dates, first), 252), len(dates)):
            d = dates[i]
            if d not in want:
                continue
            pool[d] += 1
            v = raw_at(closes, i)
            if v is not None:
                raws[d].append(v)
    table = {d: (sorted(v), pool[d]) for d, v in raws.items()}
    with _lock:
        _rs_cache[market_key] = (store["loaded_at"], table)
    return table


def _hit_days_series(c, dates, arr, k_end: int, days: int, end, code: str, market_key: str) -> dict:
    """一只票、整段历史、时间序列引擎:plot 整列的最后 days 个值 → 命中 / 未命中 / 算不出。"""
    import numpy as np
    from app.services.quant import screen_series
    plan = c.series
    seg = arr[:k_end]
    ncol = seg.shape[1]
    bars = {"close": seg[:, 0][None, :], "high": seg[:, 1][None, :], "low": seg[:, 2][None, :],
            "volume": seg[:, 3][None, :],
            "open": (seg[:, 4] if ncol > 4 else np.full(seg.shape[0], np.nan))[None, :]}
    # 快照字段没有历史 —— 与时间回溯同口径,整段按算不出
    snap = {f: None for f in c.fields}
    res = screen_series.evaluate(c, bars, snap, keep={c.plot_name})
    plot = res["full"][c.plot_name][0]
    idxs = range(max(0, k_end - days), k_end)
    hits = [str(dates[i]) for i in idxs if plot[i] == plot[i] and plot[i] != 0]
    unknown = sum(1 for i in idxs if plot[i] != plot[i])
    notes = []
    if c.fields:
        names = "、".join(screen_dsl_label(f) for f in c.fields)
        notes.append(f"脚本用到没有历史的字段({names}),用到它们的条件每天都算不出")
    if unknown:
        notes.append(f"{unknown} 天算不出(历史不足 {plan.depth + 1} 根 / 那几天缺开盘价或高低量),不算命中也不算没命中")
    return {"code": code, "market": market_key, "from": str(dates[idxs[0]]) if len(idxs) else None,
            "to": str(end), "evaluated": len(idxs), "hits": hits, "unknown": unknown,
            "unavailable": list(c.fields), "note": ";".join(notes) or None}


def screen_dsl_label(f: str) -> str:
    from app.services.quant import screen_dsl
    return screen_dsl.field_label_cn(f) or f


def hit_days(script: str, market: str, code: str, as_of: date | None = None, days: int = DAYS,
             in_result: bool = False) -> dict:
    """→ {code, market, from, to, evaluated, hits: [YYYY-MM-DD], unknown, unavailable: [字段], note, scan_day?}

    in_result:这只票在这次**实时**扫描的结果表里(前端按结果表传)。只对快照类脚本、没有回溯日时生效,见文件头最后一节。"""
    out = _hit_days(script, market, code, as_of, days)
    series, store_last = out.pop("_series", False), out.pop("_store_last", None)     # 内部用,不进响应
    if not in_result or as_of is not None or series:
        return out
    from app.services.quant import screen_source
    md = screen_source._market(market)
    meta = screen_source.get_meta(market)
    _rows, perf = _snapshot(market, md.key, lambda n: n in meta.names)
    return _pin_scan_day(out, snapshot_day(perf.get(code)), store_last)


def _hit_days(script: str, market: str, code: str, as_of: date | None, days: int) -> dict:
    from app.services.quant import screen_source, screen_dsl, screen_asof, screen_rs, rs_history as rh, vcp

    if not (script or "").strip():
        raise screen_source.ScreenError("没有脚本")
    md = screen_source._market(market)
    meta = screen_source.get_meta(market)

    def has_field(n: str) -> bool:
        return n in meta.names

    script = screen_dsl.fix_case(script, meta.names)[0]      # 不分大小写,同 parse_script
    c = screen_dsl.compile_script(script, has_field, meta.sma, meta.ema, meta.rsi)
    fields = list(c.fields)
    _rows, perf = _snapshot(market, md.key, has_field)
    store = screen_asof.get_store(md.key, perf)
    item = store["codes"].get(code)
    days = max(1, min(int(days or DAYS), DAYS))
    base = {"code": code, "market": md.key, "hits": [], "evaluated": 0, "unknown": 0, "unavailable": []}
    if not item:
        return dict(base, **{"from": None, "to": None, "_store_last": store["last"],
                             "_series": c.series is not None,
                             "note": "自家全市场日线里没有这只票(不在日线池里,或者还没拉到),算不出过去哪些天会被命中"})
    dates, arr = item
    end = min(as_of, store["last"]) if as_of else store["last"]
    k_end = bisect_right(dates, end)
    if k_end == 0:
        return dict(base, **{"from": None, "to": None, "note": f"这只票在 {end} 之前没有日线"})
    key = (md.key, hashlib.sha1(script.encode("utf-8")).hexdigest(), code, str(end), days)
    with _lock:
        hit = _hit_cache.get(key)
    if hit and hit[0] == store["loaded_at"]:
        return dict(hit[1], _store_last=store["last"], _series=c.series is not None)

    if c.series is not None:
        # 时间序列脚本:整段历史一次算出 plot 的整列,最后 days 列就是逐日命中。
        # 与全市场扫描是同一个引擎、同一份日线,所以"表里命中的票,图上最后一天必有蓝线"
        out = _hit_days_series(c, dates, arr, k_end, days, end, code, md.key)
        with _lock:
            if len(_hit_cache) > 2000:
                _hit_cache.clear()
            _hit_cache[key] = (store["loaded_at"], out)
        return dict(out, _series=True)
    rcache = screen_dsl.build_resolver_cache(c, has_field, meta.sma, meta.ema, meta.rsi)

    unavailable = sorted({f for f in fields if not screen_asof.reconstructable(f)})
    plain = [f for f in fields if f not in screen_asof.STATIC_COLS and screen_asof.need_bars(f) is not None
             and f not in screen_rs.RS_FIELDS and f not in vcp.FIELDS and f != vcp.DISPLAY]
    uses_rating = any(f in ("rs_rating", "rs_raw") for f in fields)
    uses_line = "rs_line_up_days" in fields
    vcp_used = [f for f in fields if f in vcp.FIELDS or f == vcp.DISPLAY]
    uses_vcp_core = any(f.startswith("vcp_") for f in vcp_used)
    uses_pv = any(f in ("up_days_20d", "down_days_20d", "ud_vol_ratio_20d") for f in vcp_used)
    uses_win = any(f in vcp.WINDOW_FIELDS for f in vcp_used)
    s = perf.get(code) or {}
    in_pool = bool(s) and screen_rs.in_population(s, md.key)
    table = _rs_table(md.key, store, perf) if uses_rating else None

    hits: list[str] = []
    unknown = 0
    idxs = range(max(0, k_end - days), k_end)
    for i in idxs:
        d = dates[i]
        bars = screen_asof._tuples(dates, arr, i + 1)
        row = {"_code": code}
        for col in screen_asof.STATIC_COLS:
            if col in fields:
                row[col] = s.get(col)
        row.update(screen_asof.compute_fields(bars, plain, d.year))
        for f in unavailable:
            row[f] = None
        if uses_line:
            st = rh.rs_line_stats([(b[0], b[1]) for b in bars], store["bench"])    # 股票日线已截到 d,基准多出来的日子对不上,不影响
            row["rs_line_up_days"] = st["up_days"] if st else None
        if uses_vcp_core:
            vs = vcp.vcp_stats(bars) or {}
            for f in vcp.FIELDS:
                if f.startswith("vcp_"):
                    row[f] = vs.get(f[4:])
        if uses_pv:
            pv = vcp.pv_stats(bars)
            row["up_days_20d"], row["down_days_20d"] = pv["up_days"], pv["down_days"]
            row["ud_vol_ratio_20d"] = pv["ud_vol_ratio"]
        if uses_win:
            row.update(vcp.window_stats(bars))
        if uses_rating:
            raw = rh.rs_raw_exact([b[1] for b in bars]) if (in_pool and len(bars) > 252) else None
            sr, pn = table.get(d, ([], 0))
            row["rs_raw"] = raw
            row["rs_rating"] = rating_of(sr, pn, raw, screen_rs.RS_UNIVERSE_THRESHOLD)
        env: dict = {}
        for stmt in c.stmts:
            env[stmt.name] = screen_dsl._eval(stmt.node, row, env, rcache)
        v = screen_dsl._truthy(env.get(c.plot_name))
        if v is None:
            unknown += 1
        elif v:
            hits.append(str(d))

    notes = []
    if unavailable:
        names = "、".join(screen_dsl.field_label_cn(f) or f for f in unavailable)
        notes.append(f"脚本用到没有历史的字段({names}),用到它们的条件每天都算不出")
    if unknown:
        notes.append(f"{unknown} 天字段算不出(日线不够长 / 缺高低量 / RS 排名池覆盖不足),不算命中也不算没命中")
    out = dict(base, **{"from": str(dates[idxs[0]]) if len(idxs) else None, "to": str(end),
                        "evaluated": len(idxs), "hits": hits, "unknown": unknown, "unavailable": unavailable,
                        "note": ";".join(notes) or None})
    with _lock:
        if len(_hit_cache) > 2000:
            _hit_cache.clear()
        _hit_cache[key] = (store["loaded_at"], out)
    return dict(out, _store_last=store["last"], _series=False)
