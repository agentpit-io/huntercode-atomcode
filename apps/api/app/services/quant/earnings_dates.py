"""美股财报日 · 小鹿突破买入线 v14 的 P-23(财报前不买)/ P-24(财报前减仓)用。

2026-09-15 用户:「在个股财报日前 5 个交易日内不得买入或加仓;已经买入的,财报前 2 天浮盈不大于 10% 清仓,
大于 10% 卖出一半,余仓沿用原来的止损止盈规则」。

## 为什么不复用 gm/earnings_cal.py

那份给首页「未来一周财报」卡片:每天只留市值前 30、存 Redis 6 小时。回测要**过去一年每一天的全量**,
而且必须分得清「这天没有这只票」和「这天没拉到」—— 把没拉到当成没有财报,就是静默放行。

## 数据源口径(2026-09-15 在 fin-r1 实测)

- Nasdaq 官方 `api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD`,免 key,fin-r1 直连。
  **过去的日期也给**(带实际 EPS),一天约 300 行、不分页;未来的日期给已排期的公司。
- 休市日 / 周末返回 HTTP 200、`rCode 200`、`rows: null` —— 这是「这天没人发」,不是失败。
  失败只认:请求异常、HTTP 非 200、`rCode` 非 200、`data` 为空。
- **过去日期的 `time` 全是 `time-not-supplied`**,盘前 / 盘后分不出,所以财报日只按日期算。
- 回测用的是事后看到的实际披露日。公司通常提前几周公告日期,离财报 5 个交易日时基本已公布;
  偶有改期的,回测里看到的是改期后的日子 —— 已知的前视偏差,量级很小。

## 「知道」与「不知道」

拉取成功的日子记进 `earnings_fetch_day`(0 行也记)。`EarningsView.gaps` = 往后 `HORIZON_DAYS` 天里
没拉成功的工作日;有缺口时,缺口之前查不到这只票的财报,也**不能**说它近期没有财报 → 引擎按「日历缺」不买。
未来的日子每晚重拉(日期会改);重拉失败保留上一次的结果(过期但不是空的),日志里有 warning。

表是本模块幂等建的(仓内铁律:db/migrations 对已有部署不生效)。
"""
from __future__ import annotations

import logging
import time
from bisect import bisect_left, bisect_right
from datetime import date, timedelta

log = logging.getLogger(__name__)

_URL = "https://api.nasdaq.com/api/calendar/earnings"
_HEADERS = {                     # 与 gm/earnings_cal.py 同一组;不 import 它,那边会带进 Redis 缓存模块
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}
HORIZON_DAYS = 10                # 看缺口的窗口(自然日):5 个交易日最多跨 7~9 个自然日,再留一点余量
FUTURE_DAYS = 21                 # 每晚往后拉多少个自然日

_DDL = """
CREATE TABLE IF NOT EXISTS earnings_date (
    symbol      TEXT        NOT NULL,
    report_date DATE        NOT NULL,
    time_code   TEXT,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, report_date)
);
CREATE INDEX IF NOT EXISTS earnings_date_day ON earnings_date (report_date);
CREATE TABLE IF NOT EXISTS earnings_fetch_day (
    day        DATE        PRIMARY KEY,
    n          INT         NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""
_ddl_done = False


def weekdays(a: date, b: date) -> list[date]:
    """[a, b] 里的周一到周五(含两端)。"""
    out, d = [], a
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


class EarningsView:
    """某个交易日 d 收盘后看得到的财报日。纯计算,不连库(引擎和用例都用它)。

    dates_of:代码 → 升序的财报日列表;fetched:拉取成功的日子;trade_days:升序交易日(基准日线的日期)。"""

    def __init__(self, d: date, dates_of: dict, fetched: set, trade_days: list[date], horizon: int = HORIZON_DAYS):
        self.d = d
        self._dates_of = dates_of
        self._td = trade_days
        self.gaps = [x for x in weekdays(d, d + timedelta(days=horizon)) if x not in fetched]

    def tdays(self, e: date) -> int:
        """d 之后到 e(含)有几个交易日;e == d → 0。超出交易日历的部分按工作日数(美股节假日会多算一天,偏保守)。"""
        td = self._td
        n0 = bisect_right(td, self.d)
        last = td[-1] if td else self.d
        if e <= last:
            return bisect_right(td, e) - n0
        return (len(td) - n0) + len(weekdays(max(last, self.d) + timedelta(days=1), e))

    def next_of(self, code: str) -> tuple[date, int] | None:
        """d 当天或之后的下一次财报 → (财报日, 还有几个交易日);库里没有 → None(要结合 gaps 看是不是「不知道」)。"""
        ds = self._dates_of.get(code)
        if not ds:
            return None
        i = bisect_left(ds, self.d)
        if i >= len(ds):
            return None
        return ds[i], self.tdays(ds[i])


def info(code: str, view: EarningsView | None) -> dict:
    """→ {known, date, tdays, text}。known=False = 不知道(日历缺),不是「没有财报」;known=True 且 date=None = 近期没有财报。"""
    if view is None:
        return {"known": False, "date": None, "tdays": None, "text": "没有财报日历(数据没接上)"}
    nx = view.next_of(code)
    if view.gaps and (nx is None or nx[0] > view.gaps[0]):
        return {"known": False, "date": None, "tdays": None,
                "text": f"{view.gaps[0]} 起有 {len(view.gaps)} 个工作日的财报日历没拉到,不知道这只票近期发不发财报"}
    if nx is None:
        return {"known": True, "date": None, "tdays": None, "text": "近期没有排期的财报"}
    e, n = nx
    return {"known": True, "date": e, "tdays": n,
            "text": f"今天({e})发财报" if n == 0 else f"下一次财报 {e}(还有 {n} 个交易日)"}


# ═══════════════════════════════════════════════════════════════
# 拉取与落库
# ═══════════════════════════════════════════════════════════════

def fetch_day(d: date) -> list[tuple[str, str]] | None:
    """一天的全量 → [(代码, time)];休市 / 周末 → [];拉取失败 → None。"""
    import requests
    err = None
    for k in range(3):
        try:
            r = requests.get(_URL, params={"date": d.isoformat()}, headers=_HEADERS, timeout=15)
            r.raise_for_status()
            j = r.json()
            rc = (j.get("status") or {}).get("rCode")
            data = j.get("data")
            if rc != 200 or data is None:
                raise ValueError(f"rCode={rc}, data={'空' if data is None else '有'}")
            out, seen = [], set()
            for x in data.get("rows") or []:
                s = (x.get("symbol") or "").strip().upper()
                if s and s not in seen:
                    seen.add(s)
                    out.append((s, x.get("time") or ""))
            return out
        except Exception as e:                  # noqa: BLE001
            err = e
            time.sleep(1.5 * (k + 1))
    log.warning("[earnings] %s 拉取失败:%s", d, err)
    return None


def _conn():
    """连库;DDL 每进程一次,带 5 秒 lock_timeout(rs_history 2026-09-15 事故:DDL 排在长查询后面把整条链堵死)。"""
    global _ddl_done
    from app.services.database import get_conn
    c = get_conn()
    if not _ddl_done:
        cur = c.cursor()
        try:
            cur.execute("SET LOCAL lock_timeout = '5s'")
            cur.execute(_DDL)
            c.commit()
            _ddl_done = True
        except Exception:                        # noqa: BLE001
            c.rollback()
            log.warning("[earnings] 建表拿不到锁或失败,先继续(表已存在时不影响读写)", exc_info=True)
        finally:
            cur.close()
    return c


def ensure(start: date, end: date, refresh_from: date | None = None, pause: float = 0.3) -> dict:
    """把 [start, end] 的工作日补齐:没拉过的拉;refresh_from 及之后的(未来的日子,日期会改)重拉。
    → {fetched, rows, skipped, failed:[日期]}。每天单独提交,中途断了下次接着补。"""
    out = {"fetched": 0, "rows": 0, "skipped": 0, "failed": []}
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT day FROM earnings_fetch_day WHERE day BETWEEN %s AND %s", (start, end))
        have = {r[0] for r in cur.fetchall()}
        conn.commit()                            # 别让连接停在 idle in transaction(agent_run 2026-09-12 事故)
        for d in weekdays(start, end):
            if d in have and (refresh_from is None or d < refresh_from):
                out["skipped"] += 1
                continue
            rows = fetch_day(d)
            if rows is None:
                out["failed"].append(str(d))
                continue
            cur.execute("DELETE FROM earnings_date WHERE report_date=%s", (d,))
            cur.executemany("INSERT INTO earnings_date (symbol, report_date, time_code) VALUES (%s, %s, %s) "
                            "ON CONFLICT (symbol, report_date) DO NOTHING", [(s, d, t) for s, t in rows])
            cur.execute("INSERT INTO earnings_fetch_day (day, n, fetched_at) VALUES (%s, %s, now()) "
                        "ON CONFLICT (day) DO UPDATE SET n=EXCLUDED.n, fetched_at=now()", (d, len(rows)))
            conn.commit()
            out["fetched"] += 1
            out["rows"] += len(rows)
            time.sleep(pause)
    finally:
        cur.close()
        conn.close()
    if out["failed"]:
        log.warning("[earnings] %s ~ %s 有 %d 天没拉到:%s", start, end, len(out["failed"]), out["failed"][:10])
    return out


def load() -> tuple[dict, set]:
    """→ (代码 → 升序财报日列表, 拉取成功的日子)。全表一年约 10 万行,一次读完。"""
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT symbol, report_date FROM earnings_date ORDER BY symbol, report_date")
        dates_of: dict = {}
        for s, d in cur.fetchall():
            dates_of.setdefault(s, []).append(d)
        cur.execute("SELECT day FROM earnings_fetch_day")
        fetched = {r[0] for r in cur.fetchall()}
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return dates_of, fetched
