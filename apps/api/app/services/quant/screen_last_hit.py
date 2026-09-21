"""扫描筛选 · 官方示例「最近一次命中是哪天」(用户 2026-09-16 要求)。

普通扫描 0 命中时,结果区提示「距离今日最近一次扫描命中的日子为 X,可以通过时间回溯查看详情」,
点「时间回溯」弹窗自动填好那天。**只做官方示例**:脚本固定、按 key 取,用户脚本不走这条(防止拿它当免费的逐日回测)。

## 口径:逐日跑真正的时间回溯,不做近似

每天调一次 `screen_source.run_script(..., as_of=那天)` —— 和用户点「时间回溯 → 跳转 → 运行扫描」同一条路。
时间序列引擎本来能一次算出整列,拿来预筛会快很多,但递归定义的起算点随窗口变,算出来的「命中日」
可能和用户跳过去看到的对不上 —— 提示说那天有、跳过去却 0 只,比慢更糟。

## 为什么放后台线程

时间序列类(猎杀FOMO做空)回溯一天约 1 秒,横截面类(上升趋势 / VCP 等)约 10~15 秒;
往回找几十天不能卡住扫描请求。所以:扫描返回时带上当前状态(ready / pending / none),
前端 pending 时轮询 `GET /screener/last-hit`。结果按(市场, 示例, 日线最新一天)缓存在进程内 ——
API 是单 worker(Dockerfile 里的 uvicorn 没开 --workers),重启丢了就再算一次。

## 预热(2026-09-16 用户:「其他几个官方筛选器也提前预热,不要等 0 命中才找」)

`prewarm_loop` 在 API 启动 1 分钟后、之后每 30 分钟把**全部官方示例**过一遍:缓存里没有「日线最新一天」这份结果的就排队。
每晚日线更新后下一轮自动重算,用户点运行扫描时通常已经算好。挂在 main.py 的 MINIMAL_BOOT 开关**之前**(生产开着那个开关)。
任务**串行**跑(一个后台线程排队),横截面类一天十几秒,几个一起并行会和用户的扫描抢 CPU。
没有日线的市场(开源用户没下载过)`_latest` 为空,直接跳过,不占资源。

## 从哪天开始找

- 时间序列类:普通扫描本身就是按日线最新一天算的,从**前一个交易日**开始;
- 横截面类:普通扫描用的是实时快照,日线最新一天是另一套口径,从**日线最新一天**开始。
最多往回 MAX_DAYS 个交易日,或者花满 BUDGET_S 秒就停,返回 none 并说明找到了哪天。
"""
from __future__ import annotations

import threading
import time
from datetime import date, timedelta

from loguru import logger as log     # 标准 logging 的 INFO 在容器日志里看不到,预热进度要能查

MAX_DAYS = 120
BUDGET_S = 15 * 60

PREWARM_EVERY_S = 30 * 60

_lock = threading.Lock()
_cache: dict = {}        # (市场, key, 日线最新一天) → 结果
_running: set = set()    # 排队中 + 正在算
_queue: list = []        # 待算任务(先进先出)
_worker_on = False


def _latest(market: str) -> date | None:
    from app.services.quant import screen_asof
    d = (screen_asof.history_range(market) or {}).get("max_date")
    return date.fromisoformat(str(d)) if d else None


def _search(market: str, key: str, latest: date) -> dict:
    from app.services.quant import screen_source
    p = screen_source.preset(key)
    script = p["script"]
    c = _compiled(market, script)
    series = c.series is not None
    # 用到没有历史值的字段(市值 / PE / 股息率等)的示例,回溯时这些条件每天都「算不出」,
    # 往回找 120 天也不可能命中 —— A 股「低估值超卖」实测白跑 346 秒。直接跳过,前端不显示提示
    from app.services.quant import screen_asof
    unavailable = sorted({f for f in c.fields if not screen_asof.reconstructable(f)})
    if unavailable:
        return {"status": "unsupported", "fields": unavailable}
    d = latest - timedelta(days=1) if series else latest
    t0 = time.time()
    searched = 0
    oldest = None
    while searched < MAX_DAYS and time.time() - t0 < BUDGET_S:
        try:
            r = screen_source.run_script(script, market, 1, None, True, d, False)
        except screen_source.ScreenError:
            break                                  # 早于日线起点 / 日线没建好 —— 再往前也没有
        actual = date.fromisoformat(str(r.get("as_of")))
        if series and actual >= latest:            # 周末挪回到最新一天:那天普通扫描已经算过
            d = latest - timedelta(days=1)
            continue
        searched += 1
        oldest = actual
        if r.get("matched"):
            return {"status": "ready", "date": str(actual), "matched": r.get("matched"),
                    "searched_days": searched}
        d = actual - timedelta(days=1)
    return {"status": "none", "searched_days": searched, "oldest": str(oldest) if oldest else None}


def _drain():
    """唯一的后台线程:按顺序把队列里的任务算完就退出,下次有任务再起。"""
    global _worker_on
    while True:
        with _lock:
            if not _queue:
                _worker_on = False
                return
            job = _queue.pop(0)
        _worker(*job)


def _worker(market: str, key: str, latest: date):
    k = (market, key, latest)
    t0 = time.time()
    try:
        res = _search(market, key, latest)
    except Exception as e:                           # noqa: BLE001
        log.warning(f"[last-hit] {market}/{key} 查找失败:{e}")
        res = {"status": "error"}
    res["latest"] = str(latest)
    log.info(f"[last-hit] {market}/{key} 截至 {latest}:{res} · {time.time() - t0:.0f}s")
    with _lock:
        _running.discard(k)
        for old in [x for x in _cache if x[0] == market and x[1] == key and x[2] != latest]:
            _cache.pop(old, None)                    # 日线更新过,前一天算的作废
        if res["status"] != "error":
            _cache[k] = res


def lookup(market: str, key: str, start: bool = True) -> dict:
    """→ {status: ready|pending|none|unsupported|error|idle, date?, matched?, searched_days?, oldest?}。不阻塞。

    start=False 只查缓存、不起后台任务(轮询接口用,避免有人拿轮询去反复触发)。
    """
    from app.services.quant import screen_source
    p = screen_source.preset(key)
    if p is None or p.get("market") != market:
        return {"status": "error"}
    try:
        latest = _latest(market)
    except Exception:                                # noqa: BLE001
        return {"status": "error"}
    if latest is None:
        return {"status": "error"}
    k = (market, key, latest)
    with _lock:
        if k in _cache:
            return _cache[k]
        if k in _running:
            return {"status": "pending"}
        if not start:
            return {"status": "idle"}
        _running.add(k)
        _queue.append((market, key, latest))
        global _worker_on
        spawn = not _worker_on
        _worker_on = True
    if spawn:
        threading.Thread(target=_drain, daemon=True, name="last-hit-worker").start()
    return {"status": "pending"}


def prewarm_all() -> int:
    """全部官方示例排队(已缓存 / 已在排队的跳过)。时间序列类先排 —— 一天 1 秒,先出结果。→ 新排进去几个。"""
    from app.services.quant import screen_source
    keys = [(p["market"], p["key"]) for p in screen_source.PRESETS if p.get("market")]
    def _fast(mk):
        try:
            return 0 if is_series(*mk) else 1
        except Exception:                            # noqa: BLE001
            return 1
    n = 0
    for market, key in sorted(keys, key=_fast):
        if lookup(market, key).get("status") == "pending":
            n += 1
    return n


async def prewarm_loop():
    import asyncio
    await asyncio.sleep(60)                          # 等启动时的其他初始化先走完
    while True:
        try:
            n = await asyncio.to_thread(prewarm_all)
            if n:
                log.info(f"[last-hit] 预热:{n} 个官方示例在排队找最近命中日")
        except Exception as e:                       # noqa: BLE001
            log.warning(f"[last-hit] 预热失败(非致命):{e}")
        await asyncio.sleep(PREWARM_EVERY_S)


def _compiled(market: str, script: str):
    """和 parse_script / run_script 同一个编译器。"""
    from app.services.quant import screen_dsl, screen_source
    meta = screen_source.get_meta(market)
    return screen_dsl.compile_script(script, lambda n: n in meta.names, meta.sma, meta.ema, meta.rsi)


def _series(market: str, script: str) -> bool:
    """这份脚本走不走时间序列引擎。"""
    return _compiled(market, script).series is not None


def is_series(market: str, key: str) -> bool:
    from app.services.quant import screen_source
    p = screen_source.preset(key)
    return bool(p) and p.get("market") == market and _series(market, p["script"])
