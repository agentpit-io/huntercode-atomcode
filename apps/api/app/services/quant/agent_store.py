"""小鹿研究线 · 回测可复用数据落库(2026-09-15)。

用户原话:「计算出的通用数据比如 RS、收盘价、开盘价、均线之类后续统统保留,以后需要的时候优先调用,省去重新计算;
对于同一个研究,每一轮回测应该保留可以复用的数据,越跑越快才对」;又说「根本不需要全市场 4000 只,
研究用的选股器扫到哪些就保留哪些的数据」。

所以存两层,都只针对**研究用选股器扫到的票**:

1. `screen_hit_field`:选股器那天命中的票,选股器算出来的整行字段(收盘 / 均线 / RS 评级 / 资金逆势买入 …)。
   原来 agent_watch_pool 只存 [代码, 名称, RS]。回溯筛选每天 7~8 秒,结果本来就按 (池, 日期) 复用,
   这里把当时算出的字段一起留下,以后分析 / 画图 / 新规则要用直接查,不用再回溯一次。
2. `agent_ind_cache`:引擎对这些票算出的指标(`indicators()` 的整份结果,pickle + zlib)。
   键 = (引擎, 指标版本, 日期, 代码)。指标版本由引擎的 `ind_version()` 给:算法基准号 + 指标里用到的参数的哈希,
   **改了参数自动换版本;改了 indicators / grade_features 的算法要手动升基准号**,否则会读到旧算法的结果。

两条边界:
- 只存规则固定、按需算的研究线方向(agent_run 的 `_lazy_ok`)。选股条件变了,全市场仍要现算 —— 不算不知道谁命中。
- **读出来的指标先拿收盘价和当前日线核对**(`valid`)。日线复权基准会变(之后发生拆股、每晚整窗重写),
  对不上就重算这一条,不拿旧基准的价格和新基准的日子拼在一起(持仓跨过去会凭空多一次涨跌)。
"""
from __future__ import annotations

import json
import logging
import math
import pickle
import zlib

log = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS screen_hit_field (
    pool       TEXT  NOT NULL,
    trade_date DATE  NOT NULL,
    code       TEXT  NOT NULL,
    fields     JSONB NOT NULL,
    PRIMARY KEY (pool, trade_date, code)
);
CREATE TABLE IF NOT EXISTS agent_ind_cache (
    engine      TEXT        NOT NULL,
    ver         TEXT        NOT NULL,
    trade_date  DATE        NOT NULL,
    code        TEXT        NOT NULL,
    ind         BYTEA       NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (engine, ver, trade_date, code)
);
"""
_ddl_done = False


def ensure_tables() -> None:
    """建表每进程一次,单独连接、5 秒 lock_timeout(别让 DDL 排在长查询后面堵住回测那条事务)。"""
    global _ddl_done
    if _ddl_done:
        return
    from app.services.database import get_conn
    c = get_conn()
    cur = c.cursor()
    try:
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute(_DDL)
        c.commit()
        _ddl_done = True
    except Exception:                        # noqa: BLE001
        c.rollback()
        log.warning("[agent_store] 建表拿不到锁或失败,先继续", exc_info=True)
    finally:
        cur.close()
        c.close()


# ── 选股器命中字段 ───────────────────────────────────────────────

def clean(v):
    """jsonb 不收 NaN / Infinity:非有限数一律 None(算不出就是空,不是 0)。"""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    return v


def save_hits(cur, pool: str, d, hits: list[tuple[str, dict]]) -> int:
    """hits = [(代码, 选股器那天算出的字段)]。同一 (池, 日) 重跑覆盖。"""
    if not hits:
        return 0
    from psycopg2.extras import execute_values
    execute_values(cur, "INSERT INTO screen_hit_field (pool, trade_date, code, fields) VALUES %s "
                        "ON CONFLICT (pool, trade_date, code) DO UPDATE SET fields=EXCLUDED.fields",
                   [(pool, d, c, json.dumps(clean(f), ensure_ascii=False, default=str)) for c, f in hits])
    return len(hits)


# ── 引擎指标 ─────────────────────────────────────────────────────

def dump(ind: dict) -> bytes:
    return zlib.compress(pickle.dumps(ind, protocol=pickle.HIGHEST_PROTOCOL), 1)


def undump(blob) -> dict:
    return pickle.loads(zlib.decompress(bytes(blob)))


def valid(ind, close) -> bool:
    """缓存的指标还能不能用:当天收盘价和现在日线里的一致(相对差 < 1e-6)。复权基准变了就对不上 → 重算。"""
    if not ind or close is None:
        return False
    c0 = ind.get("close")
    return c0 is not None and close != 0 and abs(c0 / close - 1) < 1e-6


def load_day(cur, engine: str, ver: str, d) -> dict:
    """→ {代码: 压缩后的指标}(先不解压,用到哪只解哪只)。"""
    cur.execute("SELECT code, ind FROM agent_ind_cache WHERE engine=%s AND ver=%s AND trade_date=%s", (engine, ver, d))
    return {c: b for c, b in cur.fetchall()}


def save_inds(cur, engine: str, ver: str, d, items: list[tuple[str, dict]]) -> int:
    """items = [(代码, 指标)];指标为 None(日线不够)不存,下次现算也很便宜。"""
    rows = [(engine, ver, d, c, dump(v)) for c, v in items if v is not None]
    if not rows:
        return 0
    from psycopg2.extras import execute_values
    execute_values(cur, "INSERT INTO agent_ind_cache (engine, ver, trade_date, code, ind) VALUES %s "
                        "ON CONFLICT (engine, ver, trade_date, code) DO UPDATE SET ind=EXCLUDED.ind, computed_at=now()",
                   rows)
    return len(rows)
