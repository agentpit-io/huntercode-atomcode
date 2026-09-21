"""扫描筛选结果的中文化 —— A 股、港股的名称与板块两列。

扫描源(TradingView)给 A 股 / 港股的 description 是英文公司名(「Guangdong Kingshine Electronic Tec」
「China Merchants Bank Co., Ltd. Cla」),sector 是它自己的 21 个英文大类。只在**展示层**换成中文:
求值早已结束,这里改的是结果行,不影响命中与否。A 股 2026-09-17、港股 2026-09-19 用户要求。

名称来源两级(每个市场各一套):
  1. 仓库自带的清单,随代码分发,零网络:
     A 股 data/stocks_catalog_baseline.json(5534 只)· 港股 data/hk_names_baseline.json
     (2026-09-19 由 akshare stock_hk_spot 生成,2803 只;对当天港股扫描池 2634 只覆盖 2618 只)
  2. 后台线程每 24 小时用 akshare 拉一次最新代码-名称表,补清单生成之后才上市的新股:
     A 股 stock_info_a_code_name(线上 5565 只 / 12 秒)· 港股 stock_hk_spot(新浪,线上 2803 只 / 23 秒;
     东财 stock_hk_spot_em 本机与 fin-r1 都连不上)。**绝不阻塞扫描**:没拉完就先用清单,拉失败只记日志。
两级都查不到的票保留扫描源的英文名 —— 英文名是真的,不能因为缺中文就留空或编一个。

板块是固定的 21 个英文大类,A 股港股同一套对照表;表里没有的新类别原样显示英文。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import unicodedata
from pathlib import Path

log = logging.getLogger(__name__)

_BASELINE = Path(__file__).resolve().parents[3] / "data" / "stocks_catalog_baseline.json"
_REFRESH_TTL = 24 * 3600
_RETRY_AFTER_FAIL = 30 * 60

# TradingView 的板块(sector)全集,21 个
SECTOR_CN = {
    "Commercial Services": "商业服务",
    "Communications": "通信",
    "Consumer Durables": "耐用消费品",
    "Consumer Non-Durables": "非耐用消费品",
    "Consumer Services": "消费服务",
    "Distribution Services": "分销服务",
    "Electronic Technology": "电子技术",
    "Energy Minerals": "能源矿产",
    "Finance": "金融",
    "Government": "政府机构",
    "Health Services": "医疗服务",
    "Health Technology": "医疗技术",
    "Industrial Services": "工业服务",
    "Miscellaneous": "其他",
    "Non-Energy Minerals": "非能源矿产",
    "Process Industries": "加工工业",
    "Producer Manufacturing": "生产制造",
    "Retail Trade": "零售",
    "Technology Services": "技术服务",
    "Transportation": "交通运输",
    "Utilities": "公用事业",
}

_HERE_DATA = Path(__file__).resolve().parents[3] / "data"


def _fetch_a() -> dict[str, str]:
    import akshare as ak
    df = ak.stock_info_a_code_name()
    return {str(r["code"]).zfill(6): _clean(str(r["name"])) for _, r in df.iterrows()}


def _fetch_hk() -> dict[str, str]:
    import akshare as ak
    df = ak.stock_hk_spot()              # 新浪 · 列:代码(5 位)/ 中文名称 / 英文名称 …
    return {str(r["代码"]).strip().zfill(5): _clean(str(r["中文名称"])) for _, r in df.iterrows()}


# 每个市场:清单文件、代码位数、刷新函数、少于多少只视为半截数据不采用
_MARKETS = {
    "a": {"baseline": _BASELINE, "width": 6, "fetch": _fetch_a, "min_rows": 3000, "label": "A 股"},
    "hk": {"baseline": _HERE_DATA / "hk_names_baseline.json", "width": 5, "fetch": _fetch_hk,
           "min_rows": 2000, "label": "港股"},
}
_state = {k: {"names": {}, "loaded_at": 0.0, "last_try": 0.0, "refreshing": False, "baseline_done": False}
          for k in _MARKETS}
_lock = threading.Lock()


def _clean(name: str) -> str:
    # 清单里有「万  科Ａ」这种全角字母 + 中间补空格的写法(交易所简称按 4 字宽对齐),NFKC 再去空白
    return "".join(unicodedata.normalize("NFKC", name or "").split())


def _load_baseline(mk: str) -> None:
    cfg, st = _MARKETS[mk], _state[mk]
    st["baseline_done"] = True
    try:
        d = json.loads(cfg["baseline"].read_text(encoding="utf-8"))
    except Exception as e:                                        # noqa: BLE001
        log.warning("[screen_cn] 读%s名称清单失败: %s", cfg["label"], e)
        return
    items = d if isinstance(d, list) else (d.get("items") or [])
    m = {}
    for x in items:
        c, n = str(x.get("code") or "").zfill(cfg["width"]), _clean(str(x.get("name") or ""))
        if n:
            m[c] = n
    with _lock:
        for c, n in m.items():
            st["names"].setdefault(c, n)


def _refresh(mk: str) -> None:
    cfg, st = _MARKETS[mk], _state[mk]
    try:
        m = {c: n for c, n in cfg["fetch"]().items() if n and c.isdigit()}
        if len(m) > cfg["min_rows"]:              # 半截数据不覆盖
            with _lock:
                st["names"].update(m)
                st["loaded_at"] = time.time()
            log.info("[screen_cn] akshare 刷新%s名称 %d 只", cfg["label"], len(m))
        else:
            log.warning("[screen_cn] akshare %s只返回 %d 只,不采用", cfg["label"], len(m))
    except Exception as e:                                        # noqa: BLE001
        log.warning("[screen_cn] akshare 刷新%s名称失败: %s", cfg["label"], e)
    finally:
        with _lock:
            st["refreshing"] = False


def _ensure(mk: str) -> None:
    st = _state[mk]
    if not st["baseline_done"]:
        _load_baseline(mk)
    now = time.time()
    with _lock:
        if st["refreshing"] or now - st["loaded_at"] < _REFRESH_TTL or now - st["last_try"] < _RETRY_AFTER_FAIL:
            return
        st["refreshing"], st["last_try"] = True, now
    threading.Thread(target=_refresh, args=(mk,), name=f"screen-cn-names-{mk}", daemon=True).start()


def localize_pick(pick: dict, market: str) -> dict:
    """A 股 / 港股结果行:名称、板块换成中文。原地改并返回。别的市场、查不到的保持原样。"""
    if market not in _MARKETS:
        return pick
    _ensure(market)
    code = str(pick.get("code") or "")
    with _lock:
        cn = _state[market]["names"].get(code.zfill(_MARKETS[market]["width"])) if code.isdigit() else None
    if cn:
        pick["name"] = cn
    f = pick.get("fields")
    if isinstance(f, dict):
        if cn:
            if "description" in f:
                f["description"] = cn
            if "name" in f and isinstance(f["name"], str) and not f["name"].isdigit():
                f["name"] = cn
        s = f.get("sector")
        if isinstance(s, str) and s in SECTOR_CN:
            f["sector"] = SECTOR_CN[s]
    return pick


def localize_a_pick(pick: dict) -> dict:
    """老接口,等于 localize_pick(pick, "a")。"""
    return localize_pick(pick, "a")
