"""结果表列头排序:缓存上一次扫描的全部命中,重排不扣次数、不重新取数(2026-09-14 用户要求)。

## 为什么要有它

会员额度上线后,点列头排序原来是重新跑一次扫描 —— 扣 1 次扫描次数、再等 5 秒倒数、再打一次上游
(执行用例 GN-045 发现)。又不能只在前端排:返回体只带前 100 只,命中 5156 只时前端重排的只是这 100 只。

所以扫描成功后把**全部命中**按用户存一份(每个用户只存最近一张表),点列头时带 `resort: true`
来取:按新列排好、截前 N 只返回。不碰额度、不受 5 秒间隔限制、不取数。

## 边界

- **只认同一张表**:脚本 + 市场 + 回溯日都一样才算。条件改了、市场切了,缓存就对不上,返回「过期」,
  前端提示要重新运行(会计次数),**不自动重跑** —— 不许替用户花次数。
- **10 分钟过期**:数据本身就是快照,放太久用户会把旧表当新表排。api 重启缓存就没了,同样按过期处理。
- **内存有上限**:最多存 40 个用户,超过按最早的挤掉;单张表超过 1 万行不缓存(美股全市场也就 7 千多只)。
  api 是单进程 uvicorn,进程内缓存就够;以后改多 worker,这里要换成共享存储,否则重排会随机「过期」。
- **空值恒排最后**,与 run_script 的排序口径一致。
- 用户之间互相看不到:缓存按 uid 存,取的时候也按 uid 取。
"""
from __future__ import annotations

import threading
import time

TTL_S = 600
MAX_USERS = 40
MAX_ROWS = 10000

EXPIRED_MSG = "这张结果已经超过 10 分钟,或者服务重启过、条件改过 —— 排序要重新运行一次扫描(会计 1 次)。"

_lock = threading.Lock()
_cache: dict[str, dict] = {}


class Expired(Exception):
    """缓存里没有这张表(过期 / 重启 / 条件变了 / 太大没存)。路由转 409。"""

    def __init__(self, msg: str = EXPIRED_MSG):
        super().__init__(msg)


def key_of(script: str, market: str, as_of) -> tuple:
    return ((script or "").strip(), (market or "").strip().lower(), str(as_of) if as_of else "")


def _evict(now: float) -> None:
    for uid in [u for u, e in _cache.items() if now - e["at"] > TTL_S]:
        _cache.pop(uid, None)
    while len(_cache) > MAX_USERS:
        _cache.pop(min(_cache, key=lambda u: _cache[u]["at"]), None)


def put(uid: str, key: tuple, out: dict, all_picks: list, now: float | None = None) -> None:
    """扫描成功后存一份。out 是返回体(picks / quota 不存),all_picks 是全部命中(已按当次排序)。"""
    now = time.monotonic() if now is None else now
    with _lock:
        if len(all_picks) > MAX_ROWS:
            _cache.pop(uid, None)          # 太大不存,旧的那张也不能留(已经不是用户眼前这张表了)
            return
        base = {k: v for k, v in out.items() if k not in ("picks", "quota", "_all_picks")}
        _cache[uid] = {"key": key, "at": now, "out": base, "picks": list(all_picks)}
        _evict(now)


def _val(p: dict, sort_by: str):
    if sort_by == "close":
        return p.get("close")
    return (p.get("fields") or {}).get(sort_by)


def resort(uid: str, key: tuple, sort_by: str | None, descending: bool, limit: int,
           now: float | None = None) -> dict:
    """按新列重排缓存里的全部命中 → 与扫描同形状的返回体(多一个 resorted: true)。"""
    now = time.monotonic() if now is None else now
    with _lock:
        e = _cache.get(uid)
        if not e or e["key"] != key or now - e["at"] > TTL_S:
            raise Expired()
        base = dict(e["out"])
        picks = list(e["picks"])
    cols = base.get("columns") or []
    if sort_by and sort_by != "close" and sort_by not in cols:
        raise ValueError(f"排序字段 {sort_by!r} 不在这次结果的列里")
    if sort_by:
        has = [p for p in picks if _val(p, sort_by) is not None]
        none = [p for p in picks if _val(p, sort_by) is None]
        try:
            has.sort(key=lambda p: _val(p, sort_by), reverse=descending)
        except TypeError:                                   # 同一列里混了数字和文字,按文字排,不报错
            has.sort(key=lambda p: str(_val(p, sort_by)), reverse=descending)
        picks = has + none
    limit = max(1, min(int(limit), 500))
    base["picks"] = picks[:limit]
    base["returned"] = len(base["picks"])
    base["resorted"] = True
    base["sort_by"] = sort_by
    base["descending"] = bool(descending)
    return base


def clear() -> None:
    with _lock:
        _cache.clear()
