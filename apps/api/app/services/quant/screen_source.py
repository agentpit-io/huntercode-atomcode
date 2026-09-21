"""全市场扫描 —— 取数通道 + 护栏。

对接上游扫描服务的内部端点(地址见 _BASE,那是它网页版筛选器自己调的接口)。
免 key、免登录,国内 IP 直连可用(2026-09-10 本机实测:中位 583ms,
连打 30 次全 200)。

## 定位:探索性初筛工具,不是数据源

**它不进 `factor_value` 表、不进回测、不进推送。** 三条理由:

1. **没有历史序列。** 返回的是当前时点的横截面快照。`close|1M` 不是"一个月前的
   收盘价",而是"月线周期上最新那根的收盘",实测 NVDA 的 close / close|1W /
   close|1M 三个值完全相同(223.67)。拿它算动量会得到恒等于 0 的因子。
   要涨跌幅得用 `Perf.W` / `Perf.1M` / `Perf.Y` 这类预算好的字段。
2. **不是官方 API,没有 SLA。** 反向工程来的内部端点,上游的服务条款
   禁止自动化访问。随时可能改字段或封 IP,不能让生产链路依赖它。
3. **口径与站内数据源不一致。** 见下面 MARKET_CAP_WARN。

## 必须下推的三个过滤 —— 这是正确性前提,不是优化

不加 `type=stock` + `is_primary`,结果里会混进 ETF、优先股份额、权证。
实测第一次拉美股就拿到 `NASDAQ:GOOGM` / `GOOGN`(Alphabet 的可转换优先股
存托份额):**市值字段直接继承母公司的 4.12 万亿,PE 却是 2.38**。
按市值排序时它们会插在 GOOG 前面。

加上过滤后字段填充率也跟着好转(A 股实测:市值 70%→100%,ROE 68%→97%)。

**但前两条挡不住普通优先股。** `NYSE:NEE/PW` 这类 type=stock、is_primary=True
全都满足,只有 `typespecs=["preferred"]` 能把它和普通股的 `["common"]` 区分开。
所以第三条 `typespecs has common` 同样是必须的 —— 少了它,2026-09-10 一次
真实扫描的前两名就是两只市值和 PE 全为 null 的优先股。
实测影响:美股 7486 → 7405(剔 81 只),A 股 / 港股本来就没有,不受影响。

## 时效与口径 —— 每次返回都要带上,不能只写在文档里

* **全市场都是延迟 15 分钟**(`update_mode = delayed_streaming_900`,
  美股/港股/A 股无一例外)。免订阅拿不到实时,盘中信号别用它。
* **财报字段按上市地货币计价**,港股是 HKD(腾讯营收 TTM 给的是 8814 亿 **HKD**)。
  跨市场混合排序不换汇就是错的,所以 `currency` 永远在返回列里。
* **市值字段口径与国内源不一致**,见 MARKET_CAP_WARN。
"""
from __future__ import annotations

import logging
import re
import threading
import time

import httpx

from app.services.quant import screen_cn, screen_dsl, screen_rs, vcp
from app.services.quant.screen_dsl import Compiled, ScreenError

log = logging.getLogger(__name__)


class NeedsAI(ScreenError):
    """本地两条路(脚本 / 关键词)都不通 —— 可以问用户要不要花 token 叫 AI。

    单独一个类型是为了让路由能把「可以试 AI」这个信号带给前端。
    用普通 ScreenError 的话前端只能靠匹配报错文本来猜,那种耦合迟早断。

    kind:"text" = 大白话本地没认出来(AI 翻译);"script" = 写好的脚本编译不过(AI 只修报错那几处)。
    """

    def __init__(self, msg: str, kind: str = "text"):
        super().__init__(msg)
        self.kind = kind


# ⚠️ 下面三个值是**上游要求的**,不是可配项:换掉 Origin / Referer 会被直接拒。
# 对外文案、报错、注释一律不再点名上游是谁(2026-09-10 产品要求),
# 但这三行藏不住 —— 本仓是公开仓,谁都读得到。真要隐藏得把整个通道
# 做成可插拔的私有实现,那是另一件事。
_BASE = "https://scanner.tradingview.com"
_TIMEOUT = 25.0
_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
}

# 一页拉多少 —— 实测单请求拿全美股 7487 只只要 ~3s,分页纯粹是防上游哪天加上限。
_PAGE = 2000
_MAX_ROWS = 20000

# 翻页**必须**带一个稳定排序 —— 这不是优化,是正确性。
#
# 2026-09-10 实测:不带 sort 分四页拉美股(每页 2000),累计 8000 条里只有
# 4914 只唯一,重叠 3086 —— 也就是说约 2500 只票**一次都没被扫到**。
# 上游默认顺序在请求之间不稳定,页与页会互相重叠+错漏。
# 症状极隐蔽:结果看起来正常(有命中、有数据),只是悄悄少了三分之一的池子。
# 加上 sortBy=name 后 7487/7487 零重叠。
_SORT = {"sortBy": "name", "sortOrder": "asc"}


# ═══════════════════════════════════════════════════════════════
# 市场
# ═══════════════════════════════════════════════════════════════

class MarketDef:
    def __init__(self, key: str, tv: str, label: str, currency: str, note: str = ""):
        self.key, self.tv, self.label, self.currency, self.note = key, tv, label, currency, note


# 只开 A 股 / 港股 / 美股(2026-09-10 用户指定)。
#
# 上游那边日/韩/印/英股同样能扫(实测都是 200,覆盖 4386 / 4302 / 8659 / 9447 只),
# 但站内没有任何配套能力去接:代码归一化(market_source.market_of)只认
# A/港/美三种形态,自选、K线、财报、深度分析全都不支持别的市场。
# 扫得出来却什么也做不了,只会让人以为站内支持这些市场。
# 真要开的时候,先补 market_of 与下游链路,再往这里加。
MARKETS: dict[str, MarketDef] = {
    "a":  MarketDef("a", "china", "A股", "CNY",
                    "站内 A 股已有腾讯 / AKShare 直连通道,数据更贴国内口径。"
                    "这里主要用于快速初筛,不要用它替代站内数据。"),
    "hk": MarketDef("hk", "hongkong", "港股", "HKD"),
    "us": MarketDef("us", "america", "美股", "USD"),
}

# 顺序与 source_catalog.MARKET_ORDER 一致(A 股在最前)
MARKET_ORDER = ["a", "hk", "us"]

# 永远下推的过滤 —— 见模块 docstring
BASE_FILTER = [
    {"left": "type", "operation": "equal", "right": "stock"},
    {"left": "is_primary", "operation": "equal", "right": True},
    # typespecs 这条不能省。`type=stock` + `is_primary=True` **挡不住优先股** ——
    # 2026-09-10 一次真实扫描的结果里出现了 NYSE:NEE/PW 和 OAK/PA,
    # 实测它们 type=stock、is_primary=True,只有 typespecs=["preferred"] 能区分
    # (普通股是 ["common"])。它们的市值/PE 全是 null,混在选股结果里毫无意义。
    # 实测影响:美股 7486 → 7405(剔 81 只),A 股和港股本来就没有,不受影响。
    {"left": "typespecs", "operation": "has", "right": ["common"]},
]

# 港股不能用 is_primary(2026-09-18 给小鹿准备港股研究数据时发现)。上游把「主上市地」判在别处的
# 港股线全标成 is_primary=False:A+H 的 H 股(工行 1398 / 比亚迪 1211 / 宁德 3750 / 中国平安 2318)、
# 第二上市与双重主要上市(阿里 9988 / 京东 9618 / 网易 9999)、汇丰 0005 …… 实测 255 只,
# 恒指权重股一大半在里面,原来港股池只有 2398 只、这些一只都没有。
# 那 255 只里 232 只是港元柜台(真股票),23 只是人民币柜台(80700 / 89988 这类,和港元柜台是同一只股,
# 必须剔掉,否则 RS 排名池里同一家公司算两次)。所以港股把 is_primary 换成 currency = HKD:
# 实测 primary 那 2398 只里 2397 只是 HKD(剩 1 只人民币计价),新口径 = 2397 + 232。
_HK_FILTER = [f for f in BASE_FILTER if f["left"] != "is_primary"] + [
    {"left": "currency", "operation": "equal", "right": "HKD"},
]


def base_filter(market_key: str) -> list:
    """按市场取永远下推的过滤。港股见 _HK_FILTER 上面的说明。"""
    return list(_HK_FILTER if market_key == "hk" else BASE_FILTER)

# 永远带回来的列(不管脚本用不用)。currency 是给跨市场比较兜底的,
# description 是股票名 —— 只给代码的结果没法看。
ALWAYS_COLS = ["name", "description", "close", "currency", "volume"]

# ⚠️ 这两条的**开头几个字**被前端 screener.html 的 DROP_PREFIX 用来过滤显示
# (产品要求这两条不出现在筛选器页面上)。改文案要同步改那里,
# 否则它们会悄悄冒回页面。MCP 与 API 响应仍然带着它们 —— 模型需要知道数据是延迟的。
DELAY_WARN = "免订阅通道数据延迟 15 分钟,盘中信号请勿依赖。"

MARKET_CAP_WARN = (
    "market_cap_basic 是扫描源口径,与站内国内源实测有系统性差异:"
    "A 股 10 只抽样里中芯国际差 -37%、比亚迪 -8%、格力 -7%(A+H 两地上市股尤其大);"
    "且它与 total_shares_outstanding_current 自身对不上(close×股本 / 市值 = 0.36~0.63)。"
    "可以用来排序和粗筛,不要当作市值真值,更不要写进因子。"
)


# ═══════════════════════════════════════════════════════════════
# metainfo 缓存 —— 字段白名单跟着上游走,不在代码里维护会过期的副本
# ═══════════════════════════════════════════════════════════════

class _Meta:
    def __init__(self, names: set[str], sma: list[int], ema: list[int], rsi: list[int]):
        self.names, self.sma, self.ema, self.rsi = names, sma, ema, rsi


_META_TTL = 6 * 3600
_meta_cache: dict[str, tuple[float, _Meta]] = {}
_meta_lock = threading.Lock()

_PERIOD_RE = {
    "SMA": re.compile(r"^SMA(\d+)$"),
    "EMA": re.compile(r"^EMA(\d+)$"),
    "RSI": re.compile(r"^RSI(\d+)$"),
}


def get_meta(market_key: str) -> _Meta:
    md = _market(market_key)
    now = time.time()
    with _meta_lock:
        hit = _meta_cache.get(md.tv)
        if hit and now - hit[0] < _META_TTL:
            return hit[1]
    try:
        with httpx.Client(timeout=_TIMEOUT, headers=_UA) as cli:
            r = cli.get(f"{_BASE}/{md.tv}/metainfo")
            r.raise_for_status()
            raw = r.json().get("fields") or []
    except Exception as e:                                        # noqa: BLE001
        raise ScreenError(f"拉扫描源字段表失败:{type(e).__name__} · {e}") from e

    names = {f.get("n") for f in raw if f.get("n")}
    # name / description 不在 metainfo 里但实际可用(实测能取到值),补进白名单,
    # 否则脚本里写 description 会被判成"不认识的字段"
    names.update(ALWAYS_COLS)
    # RS 相对强度是我们在全市场快照上自己算的(screen_rs),扫描源没有这两个字段。
    # 放进白名单才能在脚本里写、在「可用字段」里搜到。
    names.update(screen_rs.RS_FIELDS)
    # VCP 字段同理:每晚用全市场日线算好(rs_history + vcp.py),扫描源没有。
    # vcp_depths 是展示用的文字,不进白名单 —— 写进条件里拿文字比大小没有意义
    names.update(vcp.FIELDS)

    def periods(prefix: str) -> list[int]:
        rx = _PERIOD_RE[prefix]
        return sorted(int(rx.match(n).group(1)) for n in names
                      if isinstance(n, str) and rx.match(n))

    meta = _Meta(names, periods("SMA"), periods("EMA"), periods("RSI"))
    with _meta_lock:
        _meta_cache[md.tv] = (now, meta)
    return meta


def _market(key: str) -> MarketDef:
    md = MARKETS.get((key or "").lower())
    if md is None:
        raise ScreenError(
            f"不支持的市场 {key!r}。可选:"
            + " · ".join(f"{k}({MARKETS[k].label})" for k in MARKET_ORDER))
    return md


# ═══════════════════════════════════════════════════════════════
# 取数
# ═══════════════════════════════════════════════════════════════

def _normalize_code(tv_symbol: str, market_key: str, name: str) -> str:
    """上游符号 → 站内代码格式。

    站内格式见 market_source.market_of:6 位纯数字 = A 股,5 位 = 港股,
    含字母 = 美股。港股上游给的是 `HKEX:700`,站内要 `00700` ——
    **必须补零到 5 位**,否则 market_of('700') 会判成 A 股然后去深交所找。
    """
    bare = tv_symbol.split(":", 1)[-1] if ":" in tv_symbol else tv_symbol
    bare = (name or bare or "").strip() or bare
    if market_key == "hk" and bare.isdigit():
        return bare.zfill(5)
    return bare


# ─── 上游保护(2026-09-14 · 魔法筛选器按 5000 会员放开)────────────────────
# 估算:5000 人 × 每天最多 20 次,高峰按 1/5 的人同时在线、每人 5 分钟扫一次 ≈ 每秒 3 次扫描,
# 美股一次 4 页 ≈ 每秒 13 个上游请求。上游是免费的非官方接口,没有 SLA、限流阈值不公开
# (实测连打 30 次没限流,更高没测过),不能把这个量原样打过去。四层:
#
#   ① 快照缓存:同一市场 + 同一组列 + 同一过滤,90 秒内共用一份。上游本身延迟 15 分钟,
#      再多 90 秒对「探索性初筛」没有实质影响。示例脚本 / 回溯快照这类列组合高度重复,命中率高。
#   ② 同 key 合并(single-flight):缓存失效那一刻涌进来的 N 个相同请求,只有一个真去拉,其余等它。
#   ③ 同时最多 3 路去上游(_UPSTREAM_SLOTS),排队超过 40 秒明说「人多」,不无限挂着。
#   ④ 页与页之间全局至少隔 0.15 秒 —— 任何时刻打上游不超过每秒约 6~7 页。
#
# 失败也缓存 10 秒:上游挂了的时候,不让排队的人一个接一个再去撞一遍 25 秒超时。
# 返回的是**行的副本**:run_script 会往行里补 RS / VCP 字段(screen_rs.inject),直接给缓存里那份会串到下一个请求。
_ROWS_TTL = 90.0
_ROWS_MAX_KEYS = 16
_ROWS_FAIL_TTL = 10.0
_QUEUE_WAIT_S = 40.0
_PAGE_GAP_S = 0.15
_UPSTREAM_SLOTS = threading.BoundedSemaphore(3)
_rows_lock = threading.Lock()
_rows_cache: dict[tuple, tuple[float, list[dict], int]] = {}
_rows_fail: dict[tuple, tuple[float, str]] = {}
_inflight: dict[tuple, threading.Event] = {}
_page_lock = threading.Lock()
_last_page = [0.0]
ROWS_CACHE_NOTE = "同一市场、同一组字段 90 秒内的扫描共用一次取数(保护上游),数据本身仍是延迟 15 分钟的快照。"


def _rows_copy(rows: list[dict]) -> list[dict]:
    return [dict(r) for r in rows]


def fetch_rows(market_key: str, columns: list[str], limit_scan: int = _MAX_ROWS,
               extra_filter: list | None = None) -> tuple[list[dict], int]:
    """拉全市场。→ (行, 上游 totalCount)

    行是 dict:上游字段名 → 值,外加 `_symbol` / `_code`。缓存 / 合并 / 限流见上面那段注释。
    """
    md = _market(market_key)
    cols: list[str] = []
    for c in list(ALWAYS_COLS) + list(columns):
        if c not in cols:
            cols.append(c)
    key = (md.key, tuple(sorted(cols)), int(limit_scan), repr(extra_filter or []))

    deadline = time.monotonic() + _QUEUE_WAIT_S + _TIMEOUT * 2
    while True:
        now = time.monotonic()
        with _rows_lock:
            hit = _rows_cache.get(key)
            if hit and now - hit[0] < _ROWS_TTL:
                rows, total = hit[1], hit[2]
                ev = None
            else:
                rows = None
                fail = _rows_fail.get(key)
                if fail and now - fail[0] < _ROWS_FAIL_TTL:
                    raise ScreenError(fail[1])
                ev = _inflight.get(key)
                owner = ev is None
                if owner:
                    ev = threading.Event()
                    _inflight[key] = ev
        if rows is not None:
            return _rows_copy(rows), total
        if not owner:
            # 别人正在拉同一份 —— 等它拉完再回头读缓存(或读到它的失败)
            if not ev.wait(max(0.1, deadline - time.monotonic())):
                raise ScreenError("扫描排队超时:现在用的人比较多,请稍后再试。")
            continue
        try:
            if not _UPSTREAM_SLOTS.acquire(timeout=_QUEUE_WAIT_S):
                raise ScreenError(f"现在扫描的人比较多,排队超过 {int(_QUEUE_WAIT_S)} 秒,请稍后再试。")
            try:
                rows, total = _fetch_upstream(md, market_key, cols, limit_scan, extra_filter)
            finally:
                _UPSTREAM_SLOTS.release()
        except ScreenError as e:
            with _rows_lock:
                _rows_fail[key] = (time.monotonic(), str(e))
            raise
        else:
            with _rows_lock:
                t = time.monotonic()
                _rows_cache[key] = (t, rows, total)
                _rows_fail.pop(key, None)
                for k in [k for k, v in _rows_cache.items() if t - v[0] >= _ROWS_TTL]:
                    _rows_cache.pop(k, None)
                while len(_rows_cache) > _ROWS_MAX_KEYS:
                    _rows_cache.pop(min(_rows_cache, key=lambda k: _rows_cache[k][0]), None)
            return _rows_copy(rows), total
        finally:
            with _rows_lock:
                _inflight.pop(key, None)
            ev.set()


def _page_throttle() -> None:
    with _page_lock:
        wait = _PAGE_GAP_S - (time.monotonic() - _last_page[0])
        if wait > 0:
            time.sleep(wait)
        _last_page[0] = time.monotonic()


def _fetch_upstream(md: MarketDef, market_key: str, cols: list[str], limit_scan: int,
                    extra_filter: list | None) -> tuple[list[dict], int]:
    rows: list[dict] = []
    seen: set[str] = set()
    total = 0
    offset = 0
    with httpx.Client(timeout=_TIMEOUT, headers=_UA) as cli:
        while offset < limit_scan:
            body = {
                "filter": base_filter(md.key) + list(extra_filter or []),
                "options": {"lang": "en"},   # zh_CN 实测也只回英文名,没有中文名可拿
                "markets": [md.tv],
                "symbols": {"query": {"types": []}, "tickers": []},
                "columns": cols,
                "sort": _SORT,
                "range": [offset, min(offset + _PAGE, limit_scan)],
            }
            _page_throttle()
            try:
                r = cli.post(f"{_BASE}/{md.tv}/scan", json=body)
            except Exception as e:                                # noqa: BLE001
                raise ScreenError(
                    f"连扫描源失败:{type(e).__name__} · {e}。"
                    f"这是免费的非官方通道,没有 SLA —— 稍后重试,或改用站内数据源。") from e
            if r.status_code != 200:
                raise ScreenError(
                    f"扫描源返回 HTTP {r.status_code}。"
                    + ("被限流了,等一会儿再试。" if r.status_code == 429 else
                       f"响应片段:{r.text[:200]}"))
            try:
                data = r.json()
            except ValueError as e:
                # 上游 200 却回了 HTML(维护页 / 验证页):原来 JSONDecodeError 直接冒成 500 纯文本,
                # 也进不了 10 秒失败缓存(2026-09-14 执行用例 KK-001 注入发现)
                raise ScreenError(
                    "扫描源返回的不是数据(可能在维护或临时拦截),稍后重试。"
                    f"响应片段:{r.text[:120]}") from e
            total = data.get("totalCount") or total
            batch = data.get("data") or []
            if not batch:
                break
            for item in batch:
                sym = item.get("s") or ""
                # 去重是兜底 —— _SORT 已经让翻页不再重叠,但上游一旦改行为,
                # 宁可少算也不要把同一只票重复计进命中数
                if sym in seen:
                    continue
                seen.add(sym)
                d = dict(zip(cols, item.get("d") or []))
                d["_symbol"] = sym
                d["_code"] = _normalize_code(sym, market_key, d.get("name") or "")
                rows.append(d)
            if len(batch) < (body["range"][1] - body["range"][0]):
                break
            offset += _PAGE
    return rows, total


# ═══════════════════════════════════════════════════════════════
# 跑脚本
# ═══════════════════════════════════════════════════════════════

# 结果表里额外展示的列(有就带,没有不报错)—— 让命中结果不用再点进去看
_DISPLAY_EXTRA = ["market_cap_basic", "price_earnings_ttm", "RSI", "change",
                  "sector", "average_volume_30d_calc"]


def run_script(script: str, market_key: str = "us", limit: int = 100,
               sort_by: str | None = None, descending: bool = True,
               as_of=None, keep_all: bool = False) -> dict:
    """编译 → 拉数 → 本地求值。返回体结构见 docs-hunter / 前端 screener.html。

    as_of(date)= 时间回溯:不用今天的快照,用自家日线重算「那天收盘」的字段再求值
    (screen_asof)。快照里没有历史的字段(市值 / 财务)整批为空并在 warnings 里点名。
    """
    md = _market(market_key)
    if not (script or "").strip():
        raise ScreenError("脚本是空的。至少要有一句 `plot scan = <条件>;`")
    if len(script) > 20000:
        raise ScreenError("脚本太长(上限 20000 字符)")
    # 不能写 `limit or 100`:limit=0 是假值,会被当成「没传」变成 100(2026-09-14 执行用例 LB-009 发现)
    limit = max(1, min(int(100 if limit is None else limit), 500))

    meta = get_meta(market_key)
    script = screen_dsl.fix_case(script, meta.names)[0]      # 不分大小写,同 parse_script

    def has_field(n: str) -> bool:
        return n in meta.names

    c: Compiled = screen_dsl.compile_script(
        script, has_field, meta.sma, meta.ema, meta.rsi)
    if c.series is not None:
        # 时间序列模式(K 线偏移 / 递归 / if / 滚动窗口):按自家全市场日线逐根算,不查快照。
        # 必须在 build_resolver_cache 之前分流 —— 那一步会把 Sum() 当扫描源字段去映射
        return _run_series(c, md, market_key, has_field, limit, sort_by, descending, as_of, keep_all, t_start=time.time())
    cache = screen_dsl.build_resolver_cache(
        c, has_field, meta.sma, meta.ema, meta.rsi)

    want = list(c.fields)
    for extra in _DISPLAY_EXTRA:
        if extra not in want and has_field(extra):
            want.append(extra)

    # RS 字段**不能**原样发给扫描源(它没有这两列,会整批报错),
    # 换成算 RS 需要的四个来源列,拉回来之后在全市场上算好再补进每一行。
    uses_rs = any(f in screen_rs.RS_FIELDS for f in c.fields)
    vcp_used = [f for f in c.fields if f in vcp.FIELDS]
    uses_vcp = bool(vcp_used)
    if uses_vcp and vcp.DISPLAY not in want:
        want.append(vcp.DISPLAY)      # 结果表里顺带显示「25.7→13.0→6.0」,一眼看出每次多深
    ours = set(screen_rs.RS_FIELDS) | set(vcp.FIELDS) | {vcp.DISPLAY}
    # 扫描源没有的 N 日均量(Average(volume, 50))由自家日线算好补进来,不发给扫描源(发了整批报错)
    own_vol = [f for f in want if screen_dsl.own_avgvol_days(f) and not has_field(f)]
    ours |= set(own_vol)
    req_cols = [f for f in want if f not in ours]
    if uses_rs:
        for col in screen_rs.RS_SOURCE_COLS:
            if col not in req_cols:
                req_cols.append(col)
    if own_vol and as_of is None:
        # 自家日线第一次载入时要用 Perf.* 做拆股修正的锚点(和时间回溯同一口径)
        from app.services.quant import rs_history as _rh
        for col in _rh._PERF_COLS:
            if has_field(col) and col not in req_cols:
                req_cols.append(col)

    t0 = time.time()
    asof_info = None
    if as_of is not None:
        from app.services.quant import screen_asof, rs_history
        # 只算得出的字段进结果列 —— 市值 / PE 这些展示列在回溯里恒为空,摆着只会让人误以为"这天没数据"
        want = [f for f in want if f in c.fields or screen_asof.reconstructable(f)]
        # 快照只拿静态列 + 拆股锚点 + 排名池判断用的两列;今天的价格 / 市值一列都不进回溯行
        snap_cols = [x for x in screen_asof.STATIC_COLS if x not in ALWAYS_COLS and has_field(x)]
        snap_cols += list(rs_history._PERF_COLS) + ["exchange", "market_cap_basic"]
        snap_rows, total = fetch_rows(market_key, snap_cols)
        perf = {r["_code"]: r for r in snap_rows}
        try:
            rows, asof_info = screen_asof.build_rows(md.key, as_of, want, snap_rows, perf)
        except ValueError as e:
            raise ScreenError(f"时间回溯:{e}") from e
        total = asof_info["n"]
    else:
        rows, total = fetch_rows(market_key, req_cols)
    fetch_ms = (time.time() - t0) * 1000

    rs_stat = None
    vcp_stat = None
    hist = None
    if as_of is not None:
        uses_rs = uses_vcp = False           # 回溯行里 RS / VCP 已经按那天算好,不再用今天的统计去盖
    if uses_rs or uses_vcp:
        # 每晚落库的全市场日线统计(RS 线上涨天数、精确 RS Raw、VCP)。读的是一张
        # 每市场几千行的小表,不是逐日明细;表还没建 / 读失败 → 空
        from app.services.quant import rs_history
        hist, _ = rs_history.load_stats(md.key)
    if uses_rs:
        # **在求值之前**、对全市场算 —— 评级的分母是全市场,不是命中结果
        rs_stat = screen_rs.inject(rows, md.key, hist)
    if uses_vcp:
        # 与 RS 线同一套新鲜度规则:超过 HIST_STALE_DAYS 天没更新就整批给空,
        # 拿一周前的形态判断「现在是不是在收缩」会给错答案
        from datetime import date as _date
        v_as_of = max((v["as_of"] for v in (hist or {}).values()), default=None)
        v_stale = v_as_of is None or (_date.today() - v_as_of).days > screen_rs.HIST_STALE_DAYS
        fresh_n = sum(1 for v in (hist or {}).values() if v["as_of"] == v_as_of)
        vcp_stat = {"as_of": v_as_of, "stale": v_stale, "fresh": fresh_n,
                    "n": vcp.inject(rows, hist, v_stale, vcp_used)}

    vol_stat = None
    if own_vol and as_of is None:
        from app.services.quant import screen_asof
        vol_stat = screen_asof.inject_avg_volume(rows, md.key, own_vol, {r["_code"]: r for r in rows})
        fetch_ms = (time.time() - t0) * 1000       # 日线冷启动约 10 秒,算进取数耗时,别让它藏在求值里

    t1 = time.time()
    hits, skipped, missing = screen_dsl.evaluate_detail(c, rows, cache)
    eval_ms = (time.time() - t1) * 1000

    sort_note = None
    if sort_by and asof_info is not None and sort_by not in want and sort_by not in screen_dsl._PRICE:
        # 界面默认按市值排序,而回溯里没有市值这一列(2026-09-12 浏览器实测当场 400)。
        # 换成按收盘价排,并告诉用户 —— 不能静默换,用户会以为看到的还是按市值排的
        sort_note = (f"回溯模式没有「{screen_dsl.field_label_cn(sort_by) or sort_by}」这一列,"
                     f"结果改按收盘价{'降' if descending else '升'}序。")
        sort_by = "close"
    if sort_by:
        if sort_by not in want and sort_by not in screen_dsl._PRICE:
            raise ScreenError(f"排序字段 {sort_by!r} 不在这次请求的列里")
        # 空值不论升序降序都排最后 —— 原来用 (is None, 值) 当 key 再 reverse,
        # 降序时空值整批跑到最前面,用户点「降序」看到的第一屏全是 —
        has = [r for r in hits if r.get(sort_by) is not None]
        has.sort(key=lambda r: r.get(sort_by), reverse=descending)
        hits = has + [r for r in hits if r.get(sort_by) is None]

    warnings = [DELAY_WARN, ROWS_CACHE_NOTE] if as_of is None else []
    if md.note:
        warnings.append(md.note)
    if asof_info is not None:
        a = asof_info["as_of"]
        warnings.append(
            f"时间回溯:按 {a} 收盘的自家日线重算(不是扫描源快照)。股票池是 RS 排名池"
            f"({asof_info['n']} 只有当天收盘;美股剔 OTC 与微盘)。"
            + (f"你选的 {asof_info['requested']} 不是交易日或还没有日线,取了它之前最近的一天。"
               if asof_info["requested"] != a else "")
            + "固定窗口按 5 / 21 / 63 / 126 / 252 个交易日算;EMA / RSI 从可用日线起点递推,"
              "周期越长、回溯越远,与快照的差异越大;日线不够长的字段给空,不拿短窗口冒充。")
        if sort_note:
            warnings.append(sort_note)
        if asof_info["unavailable"]:
            names = "、".join(screen_dsl.field_label_cn(f) or f for f in asof_info["unavailable"])
            warnings.append(
                f"这些字段没有历史值(只有当天快照),回溯时整批为空:{names}。"
                f"用到它们的条件全部「算不出」—— 要回溯就把它们换成价格 / 成交量类字段或先停用。")
        if asof_info["rs"] is not None:
            ri = asof_info["rs"]
            if ri["gated"]:
                warnings.append(
                    f"回溯到 {a} 时排名池里(不含 {ri['young']} 只上市不足一年的次新股)只有 {ri['coverage']:.0%} 的票"
                    f"有满 253 根日线可算精确 RS,低于 {screen_rs.RS_UNIVERSE_THRESHOLD:.0%} 的门槛 —— RS 评级这次全部不给。"
                    f"日线保留约两年半,回溯太远就算不出 RS,这是数据边界,不是故障。")
            else:
                warnings.append(screen_rs.METHOD_NOTE_EXACT.format(as_of=a)
                                + f"(回溯:排名池 {ri['universe']} 只,另有 {ri['young']} 只上市不足一年的次新股不参与排名)")
    if rs_stat is not None:
        uses_rating = any(f in ("rs_rating", "rs_raw") for f in c.fields)
        uses_line = "rs_line_up_days" in c.fields
        as_of = rs_stat["hist_as_of"]
        if uses_line:
            if as_of is None:
                warnings.append(
                    f"{md.label}的全市场日线还没建好,「RS线上涨天数」这次全部为空"
                    f"(算不出,不是不满足)。日线由每晚的定时任务拉取。")
            elif rs_stat["hist_stale"]:
                warnings.append(
                    f"{md.label}的日线停在 {as_of},已超过 {screen_rs.HIST_STALE_DAYS} 天没更新"
                    f"(每晚的定时任务可能坏了)—— 用过期的数据判断「连续上涨多少天」会给错答案,"
                    f"所以这次「RS线上涨天数」全部为空。")
            else:
                warnings.append(screen_rs.line_note(md.key, as_of))
        if uses_rating:
            warnings.append(screen_rs.METHOD_NOTE_EXACT.format(as_of=as_of)
                            if rs_stat["method"] == "exact" else screen_rs.METHOD_NOTE)
        if uses_rating and rs_stat["gated"]:
            warnings.append(
                f"本次全市场只有 {rs_stat['coverage']:.0%} 的股票能算出 RS,"
                f"低于 {screen_rs.RS_UNIVERSE_THRESHOLD:.0%} 的门槛 —— 在残缺的股票池里"
                f"排出来的 1–99 没有意义,所以这次 RS 评级全部不给。稍后重试。")
        else:
            pool_desc = ("交易所上市(不含 OTC 场外)、市值 ≥5000 万美元" if md.key == "us"
                         else "市值约 5000 万美元以上")
            msg = (f"RS 排名池:{pool_desc}的 {rs_stat['universe']} 只"
                   f"(与原项目口径一致,剔除 {rs_stat['excluded']} 只微盘股"
                   + ("与 OTC" if md.key == "us" else "") + ")")
            if uses_rating and rs_stat["young"]:
                msg += (f";其中 {rs_stat['young']} 只上市不足 250 个交易日的次新股"
                        f"没有评级(没有真正的 12 个月涨幅,和别人不可比)")
            warnings.append(msg + "。")
    if vol_stat is not None:
        names = "、".join(screen_dsl.field_label_cn(f) or f for f in own_vol)
        va = vol_stat["as_of"]
        if va is None:
            warnings.append(f"{md.label}的全市场日线还没建好,{names}这次全部为空(算不出,不是不满足)。"
                            f"日线由每晚的定时任务拉取。")
        elif vol_stat["stale"]:
            warnings.append(f"{md.label}的日线停在 {va},已超过 {screen_rs.HIST_STALE_DAYS} 天没更新"
                            f"(每晚的定时任务可能坏了)—— 不拿过期的量当现在的均量,{names}这次全部为空。")
        else:
            pool = ("RS 排名池:交易所上市、不含 OTC、市值 ≥5000 万美元" if md.key == "us"
                    else "RS 排名池:市值约 5000 万美元以上")
            # 「不含今天」不能写死:A 股每晚任务在上海 17:30 后会把当天也落库,那时截至日就是今天(执行用例 GN-061 发现)
            from app.services.quant import screen_quota as _sq
            when = (f"截至今天({va})收盘" if va == _sq.today_sh()
                    else f"截至 {va} 收盘、不含之后的交易日")
            warnings.append(f"{names}扫描源没有,由自家日线计算:{when};"
                            f"{vol_stat['n']} 只算得出,其余不在自家日线的覆盖范围里({pool})、"
                            f"上市不足对应天数或当天停牌,计入「算不出」。")
    if vcp_stat is not None:
        a = vcp_stat["as_of"]
        if a is None:
            warnings.append(f"{md.label}的全市场日线还没建好,VCP 字段这次全部为空"
                            f"(算不出,不是不满足)。日线由每晚的定时任务拉取。")
        elif vcp_stat["stale"]:
            warnings.append(f"{md.label}的日线停在 {a},已超过 {screen_rs.HIST_STALE_DAYS} 天没更新"
                            f"(每晚的定时任务可能坏了)—— 用过期的形态判断「现在是不是在收缩」"
                            f"会给错答案,所以这次 VCP 字段全部为空。")
        elif vcp_stat["n"] < vcp_stat["fresh"] * 0.5:
            # 2026-09-11 上线当天就是这种情况:老日线只存了收盘价,没有最高/最低/成交量
            warnings.append(f"VCP 与量价字段要用日线里的最高价、最低价和成交量。这次只有 {vcp_stat['n']} 只"
                            f"算得出(日线里带着这三项的),其余 {vcp_stat['fresh'] - vcp_stat['n']} 只"
                            f"要等下一轮每晚定时任务整窗重拉之后才有 —— 它们是「算不出」,不是「不满足」。")
        else:
            warnings.append(vcp.NOTE.format(as_of=a))
    if any("market_cap" in f for f in want):
        warnings.append(MARKET_CAP_WARN)
    # 算不出的票具体缺哪个字段 —— 只说一个总数的话,用户没法判断
    # 该改哪条条件(2026-09-11 用户问:「2547 只具体缺少哪个字段?」)。
    missing_list = [
        {"field": f, "label": screen_dsl.field_label_cn(f), "count": n,
         "reason": ("回溯模式没有这个字段的历史值(只有当天快照)"
                    if asof_info is not None and f in asof_info["unavailable"]
                    else ("回溯到那天时这只票的日线不够长" if asof_info is not None
                          else screen_dsl.missing_reason(f)))}
        for f, n in sorted(missing.items(), key=lambda kv: -kv[1])
    ]
    if skipped:
        parts = []
        for m in missing_list[:4]:
            nm = m["label"] or m["field"]
            parts.append(f"缺「{nm}」{m['count']} 只" +
                         (f"({m['reason']})" if m["reason"] else ""))
        warnings.append(
            f"{skipped} 只满足了其余条件,但缺数据无法判断,已排除在结果之外"
            f"(是「算不出」,不是「不满足」):" + ";".join(parts) + "。"
            + "如果某条条件缺得特别多,可以考虑去掉或换一个覆盖更全的字段。")

    def _pick(r: dict) -> dict:
        p = {
            "code": r.get("_code"),
            "symbol": r.get("_symbol"),
            "name": r.get("description") or r.get("name"),
            "close": r.get("close"),
            "currency": r.get("currency") or md.currency,
            "fields": {k: v for k, v in r.items() if not k.startswith("_")},
        }
        # A 股 / 港股名称、板块换中文(扫描源给的是英文)· 只动展示,求值已经结束
        return screen_cn.localize_pick(p, md.key)

    # keep_all:把全部命中一起带回去,路由存进 screen_resort,点列头排序时直接重排、不扣次数
    all_picks = [_pick(r) for r in hits] if keep_all else None
    picks = all_picks[:limit] if keep_all else [_pick(r) for r in hits[:limit]]

    result = {
        "market": md.key,
        "market_label": md.label,
        "universe_total": total,
        "scanned": len(rows),
        "matched": len(hits),
        "skipped_incomplete": skipped,
        "missing_fields": missing_list,
        "rs": rs_stat,
        "returned": len(picks),
        "picks": picks,
        "columns": want,
        # 结果表列头的中文名(拿不准的为 None,前端照旧显示英文原名)。2026-09-14 执行用例时发现列头全是
        # market_cap_basic / price_earnings_ttm 这类原名,而条件行早就是中文 —— 同一个字段两处叫法不一
        "column_labels": {f: screen_dsl.field_label_cn(f) for f in want},
        "notes": c.notes,
        "warnings": warnings,
        "source": ("自家全市场日线 · 时间回溯" if asof_info is not None
                   else "全市场扫描源(非官方接口 · 延迟 15 分钟)"),
        "as_of": str(asof_info["as_of"]) if asof_info is not None else None,
        "as_of_requested": str(asof_info["requested"]) if asof_info is not None else None,
        "asof_unavailable": asof_info["unavailable"] if asof_info is not None else [],
        "timing_ms": {"fetch": round(fetch_ms), "evaluate": round(eval_ms)},
    }
    if keep_all:
        result["_all_picks"] = all_picks          # 下划线开头:路由必须 pop 掉,不进响应
    return result


# ─── 时间序列模式(2026-09-15)────────────────────────────────────────
# 标准 ThinkScript 选股脚本(x[n] / if-then-else / input / 递归 def / Sum·Highest 等)
# 靠一行快照算不出,按自家全市场日线(rs_daily,与时间回溯同一份)做「股票 × K 线」二维求值。
# 引擎在 screen_series.py;这里只负责取数、拼行、拼 warnings,返回体和横截面模式**同一形状**。

_SERIES_MISSING_LABEL = {"_bars": "日线根数不足", "open": "开盘价", "high": "最高价", "low": "最低价",
                         "close": "收盘价", "volume": "成交量"}


def _run_series(c: Compiled, md: MarketDef, market_key: str, has_field, limit: int,
                sort_by: str | None, descending: bool, as_of, keep_all: bool, t_start: float) -> dict:
    import numpy as np
    from bisect import bisect_right
    from app.services.quant import screen_asof, rs_history, screen_series

    plan = c.series
    # 快照只借:静态列(名字 / 交易所 / 板块)、拆股锚点、排名池判断的两列、脚本直接写的快照字段、
    # 今天扫描时的展示列。今天的价格**不进**求值 —— 求值全部来自日线
    snap_cols = [x for x in screen_asof.STATIC_COLS if x not in ALWAYS_COLS and has_field(x)]
    snap_cols += list(rs_history._PERF_COLS) + ["exchange", "market_cap_basic"]
    for f in c.fields:
        if has_field(f) and f not in snap_cols:
            snap_cols.append(f)
    display: list[str] = []
    if as_of is None:
        for extra in _DISPLAY_EXTRA:
            if has_field(extra):
                display.append(extra)
                if extra not in snap_cols:
                    snap_cols.append(extra)
    snap_rows, total = fetch_rows(market_key, snap_cols)
    perf = {r["_code"]: r for r in snap_rows}
    store = screen_asof.get_store(md.key, perf)
    bench_dates = sorted(store["bench"])
    if not bench_dates:
        raise ScreenError(f"{md.label}的全市场日线还没建好(由每晚的定时任务拉取),这类逐根求值的脚本暂时跑不了")
    target = as_of or store["last"]
    k_b = bisect_right(bench_dates, target)
    if k_b == 0:
        raise ScreenError(f"时间回溯:{target} 早于日线的起点 {bench_dates[0]}")
    as_of_actual = bench_dates[k_b - 1]
    codes, bars, short = screen_series.bars_matrix(store, as_of_actual, plan.window)
    snap_map = {r["_code"]: r for r in snap_rows}

    def _f(v):
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return None
        return None if fv != fv else fv

    snap_arr: dict = {}
    for f in c.fields:
        if as_of is not None:
            snap_arr[f] = None                       # 回溯:快照字段没有历史,整批算不出
            continue
        snap_arr[f] = np.array([_f((snap_map.get(code) or {}).get(f)) if _f((snap_map.get(code) or {}).get(f)) is not None
                                else np.nan for code in codes], dtype=float)
    fetch_ms = (time.time() - t_start) * 1000

    t1 = time.time()
    if codes:
        # 快照列先铺成和 K 线同形的 (N, W) 再交给引擎。screen_series 的 name() 把 (N,) 原样返回,
        # and / or / 比较 / 四则直接走 numpy 广播:(N,) 对 (N, W) 会沿「列」方向广播 ——
        # N≠W(线上常态:几千只 × 几根)直接 ValueError,整次扫描 500;N==W 时静默错位(第 j 只票的市值用在第 j 根 K 线上)。
        # 例:`def up = close > close[1]; plot scan = up and market_cap_basic > 1e9;`(tests/test_screen_series_run.py)
        # snap_arr 本身保持 (N,),下面统计「缺哪个字段」按行取值要用
        w = bars["close"].shape[1]
        snap_eval = {f: (None if a is None else np.repeat(a[:, None], w, axis=1)) for f, a in snap_arr.items()}
        res = screen_series.evaluate(c, bars, snap_eval)
        verdict, last = res["verdict"], res["last"]
    else:
        verdict, last = np.zeros(0), {}
    eval_ms = (time.time() - t1) * 1000

    # 结果列:求值那根的收盘 / 成交量 + 脚本里的数值型定义(bullStreak / cumRet 这类,一眼看出为什么命中)
    # + 今天的展示列。布尔条件不列(命中的行每条都为真),input 常量不列(每行都一样)
    numeric_defs = [st.name for st in c.stmts
                    if st.kind != "plot" and st.name not in plan.consts
                    and (st.rec or not screen_dsl._is_boolean(st.node))]
    want = ["close", "volume"] + numeric_defs + [d for d in display if d not in ("close", "volume")]
    need_series = [s for s in screen_series._PRICE if s in plan.needs]
    span = plan.depth + 1
    rows: list[dict] = []
    hits: list[dict] = []
    skipped = 0
    missing: dict[str, int] = {}
    for i, code in enumerate(codes):
        s = snap_map.get(code) or {}
        row = {"_code": code, "_symbol": s.get("_symbol") or code}
        for col in screen_asof.STATIC_COLS:
            if col in s:
                row[col] = s.get(col)
        row["close"] = _f(bars["close"][i, -1])
        row["volume"] = _f(bars["volume"][i, -1])
        for nm in numeric_defs:
            row[nm] = _f(last[nm][i])
        for extra in display:
            row[extra] = s.get(extra)
        v = verdict[i]
        if v != v:
            skipped += 1
            if short[i] < span:
                missing["_bars"] = missing.get("_bars", 0) + 1
            for sname in need_series:
                if np.isnan(bars[sname][i, -min(span, bars[sname].shape[1]):]).any():
                    missing[sname] = missing.get(sname, 0) + 1
            for f in c.fields:
                arr = snap_arr.get(f)
                if arr is None or np.isnan(arr[i]):
                    missing[f] = missing.get(f, 0) + 1
        elif v:
            hits.append(row)
        rows.append(row)

    sort_note = None
    if sort_by and sort_by not in want and sort_by not in ("close", "volume"):
        sort_note = (f"这次结果没有「{screen_dsl.field_label_cn(sort_by) or sort_by}」这一列"
                     f"(时间序列模式{'回溯时' if as_of is not None else ''}按日线求值),"
                     f"结果改按收盘价{'降' if descending else '升'}序。")
        sort_by = "close"
    if sort_by:
        has = [r for r in hits if r.get(sort_by) is not None]
        has.sort(key=lambda r: r.get(sort_by), reverse=descending)
        hits = has + [r for r in hits if r.get(sort_by) is None]

    # ── warnings:怎么算的、数据到哪天、池子是谁、缺什么 ──
    # 「时间序列模式怎么算 / 求值截至哪天」不再放进提示(2026-09-16 用户:不用显示);回溯日被挪动这类要用户知道的照留
    warnings = []
    pool = ("RS 排名池:交易所上市、不含 OTC、市值 ≥5000 万美元" if md.key == "us"
            else "RS 排名池:市值约 5000 万美元以上")
    if as_of is None and c.fields:
        # 5540bce 删「求值截至」那条时把同一句里的这半句一起删了。这句不是「怎么算」的说明,是口径声明:
        # 市值 / PE / 财务没有历史,整段 K 线都拿今天的值比,不说用户会以为是逐日历史值(空的比假的好的同一类诚实问题)
        names = "、".join(screen_dsl.field_label_cn(f) or f for f in c.fields)
        warnings.append(f"这些字段只有当天快照、没有历史:{names}。脚本里它们按今天的值当常量参与每一根 K 线的计算,不是当时的历史值。")
    if as_of is not None:
        warnings.append(
            f"时间回溯:按 {as_of_actual} 收盘的自家日线逐根求值。股票池是{pool}的 {len(codes)} 只(当天有收盘)。"
            + (f"你选的 {as_of} 不是交易日或还没有日线,取了它之前最近的一天。" if as_of != as_of_actual else ""))
        if c.fields:
            names = "、".join(screen_dsl.field_label_cn(f) or f for f in c.fields)
            warnings.append(f"这些字段没有历史值(只有当天快照),回溯时整批为空:{names}。用到它们的条件全部「算不出」。")
    if "open" in plan.needs and codes:
        have = int((~np.isnan(bars["open"][:, -1])).sum())
        if have < len(codes) * 0.9:
            warnings.append(
                f"日线里的开盘价还没补齐:{as_of_actual} 这天 {len(codes)} 只里只有 {have} 只有开盘价"
                f"(开盘价 2026-09-15 起入库,老行要等每晚整窗重拉之后才有)。用到 open 的条件在其余票上「算不出」,不是「不满足」。")
    if sort_note:
        # 原来只算了 sort_note 没加进 warnings:按市值排序的请求被静默改成按收盘价排,用户以为看到的还是按市值排的
        warnings.append(sort_note)
    if md.note:
        warnings.append(md.note)
    for n in plan.notes:
        warnings.append(n)
    if any("market_cap" in f for f in want):
        warnings.append(MARKET_CAP_WARN)
    missing_list = [
        {"field": f, "label": _SERIES_MISSING_LABEL.get(f) or screen_dsl.field_label_cn(f), "count": n,
         "reason": ("上市不足这份脚本要求的根数,或不在自家日线的覆盖范围里" if f == "_bars"
                    else ("日线里还没有开盘价(每晚整窗重拉后才有)" if f == "open"
                          else ("老日线只存了收盘,这几项要等整窗重拉后才有" if f in ("high", "low", "volume")
                                else ("回溯模式没有这个字段的历史值(只有当天快照)" if as_of is not None
                                      else screen_dsl.missing_reason(f)))))}
        for f, n in sorted(missing.items(), key=lambda kv: -kv[1])
    ]
    if skipped:
        parts = []
        for m in missing_list[:4]:
            nm = m["label"] or m["field"]
            parts.append(f"缺「{nm}」{m['count']} 只" + (f"({m['reason']})" if m["reason"] else ""))
        warnings.append(
            f"{skipped} 只没能判断(是「算不出」,不是「不满足」):" + ";".join(parts) + "。")

    def _pick(r: dict) -> dict:
        p = {"code": r.get("_code"), "symbol": r.get("_symbol"),
             "name": r.get("description") or r.get("name"),
             "close": r.get("close"), "currency": r.get("currency") or md.currency,
             "fields": {k: v for k, v in r.items() if not k.startswith("_")}}
        return screen_cn.localize_pick(p, md.key)

    all_picks = [_pick(r) for r in hits] if keep_all else None
    picks = all_picks[:limit] if keep_all else [_pick(r) for r in hits[:limit]]
    labels = {f: screen_dsl.field_label_cn(f) for f in want}
    for nm in numeric_defs:
        labels[nm] = nm
    result = {
        "market": md.key, "market_label": md.label,
        "universe_total": total, "scanned": len(rows), "matched": len(hits),
        "skipped_incomplete": skipped, "missing_fields": missing_list, "rs": None,
        "returned": len(picks), "picks": picks, "columns": want, "column_labels": labels,
        "notes": c.notes, "warnings": warnings,
        "source": "自家全市场日线 · 时间序列逐根求值" + (" · 时间回溯" if as_of is not None else ""),
        "as_of": str(as_of_actual), "as_of_requested": str(as_of) if as_of is not None else None,
        "asof_unavailable": list(c.fields) if as_of is not None else [],
        "series": {"needs": sorted(plan.needs), "depth": plan.depth, "window": plan.window,
                   "rec": list(plan.rec), "funcs": list(plan.funcs)},
        "timing_ms": {"fetch": round(fetch_ms), "evaluate": round(eval_ms)},
    }
    if keep_all:
        result["_all_picks"] = all_picks
    return result


# ─── 官方示例原样运行不扣扫描次数(2026-09-14 用户要求)─────────────────
# 「原样」按**条件语义**判,不按原文比:界面加载示例后会把条件重新拼成脚本(注释没了、换行变了、
# plot 里的顺序可能不同),逐字比会把没改过的也判成改过。所以两边都解析成语法树再比:
#   · 每个 def 的名字 → 表达式节点(数字节点去掉源码位置,只比数值)
#   · plot 是纯 and 链时比名字集合(顺序无关);否则比整个表达式节点
#   · 市场必须是示例自己的市场
# 关掉一条(plot 集合少一个)、改一个数、加一条、换市场、改名 —— 都不再是官方示例,照常扣次数。
# 判不出来(脚本编译不过)一律当「不是官方示例」,宁可扣也不能漏。
_PRESET_CANON: dict[str, object] = {}


def _strip_pos(node):
    if isinstance(node, (tuple, list)):
        if node and node[0] == "num":
            return ("num", node[1])
        return tuple(_strip_pos(x) for x in node)
    return node


def _mentions(node, name: str) -> bool:
    """表达式里有没有直接用到某个序列名(不分大小写)。"""
    if not isinstance(node, (tuple, list)):
        return False
    if node and node[0] == "name" and str(node[1]).lower() == name:
        return True
    return any(_mentions(x, name) for x in node[1:] if isinstance(x, (tuple, list)))


def _canon_script(script: str):
    try:
        stmts, _plot_name = screen_dsl._Parser(script or "").parse()
    except ScreenError:
        return None
    defs: dict[str, object] = {}
    plot = None
    for st in stmts:
        if st.kind == "plot":
            plot = st.node
        else:
            defs[st.name] = _strip_pos(st.node)
    if plot is None:
        return None
    names = screen_dsl._flatten_and_names(plot)
    return (tuple(sorted(defs.items(), key=lambda kv: kv[0])),
            ("and", frozenset(names)) if names else ("expr", _strip_pos(plot)))


def official_preset_of(script: str, market_key: str) -> dict | None:
    """这份脚本是不是「原样的官方示例」→ {key, name, market} | None。"""
    mine = _canon_script(script)
    if mine is None:
        return None
    mk = (market_key or "").strip().lower()
    for p in PRESETS:
        if p.get("market") != mk:
            continue
        if p["key"] not in _PRESET_CANON:
            _PRESET_CANON[p["key"]] = _canon_script(p["script"])
        if _PRESET_CANON[p["key"]] is not None and _PRESET_CANON[p["key"]] == mine:
            return {"key": p["key"], "name": p["name"], "market": mk}
    return None


def parse_script(script: str, market_key: str = "us", allow_ai: bool = False,
                 user_id: str | None = None, on_ai=None, context: str | None = None) -> dict:
    """只解析、不拉数 —— 界面上点「生成」走这条,把脚本变成可视化条件行。

    和 run_script 共用同一个编译器,所以**界面上看到的条件就是真正会跑的条件**。
    另起一套解析会立刻漂移(前端认为的条件和后端跑的不是一回事),那种 bug
    极难发现,因为两边单独看都"对"。
    """
    md = _market(market_key)
    if not (script or "").strip():
        raise ScreenError("脚本是空的。至少要有一句 `plot scan = <条件>;`")
    if len(script) > 20000:
        raise ScreenError("脚本太长(上限 20000 字符)")
    meta = get_meta(market_key)

    def has_field(n: str) -> bool:
        return n in meta.names

    def _compile(src: str) -> Compiled:
        return screen_dsl.compile_script(src, has_field, meta.sma, meta.ema, meta.rsi)

    # ThinkScript 不分大小写:字段名 / 自己的 def 名按原名改写(screen_dsl.fix_case,2026-09-19)
    script, case_changes = screen_dsl.fix_case(script, meta.names, context or "")
    ai = None
    kw = None
    original_text = script
    # 条件行「复制这一条」复制出来的片段(没有 plot)—— 补成完整脚本;追加模式带着当前脚本当上下文(2026-09-17)
    frag = None
    frag_err: ScreenError | None = None
    if context is not None and len(context) > 20000:
        context = None
    try:
        frag = screen_dsl.complete_fragment(script, context, _compile)
    except ScreenError as fe:
        frag_err = fe
        if not context and "不认识" in str(fe):
            frag_err = ScreenError(
                f"{fe}\n这像是从条件行「复制这一条」复制出来的片段,它用到的定义在原脚本里 —— "
                f"用「追加」模式粘到那份脚本上,或者用「复制脚本」拿完整脚本。")
    try:
        if frag is not None:
            script = frag["src"]
            c = frag["c"]
        else:
            c: Compiled = _compile(script)
    except ScreenError as script_err:
        if frag_err is not None:
            script_err = frag_err
        # 解析不了 —— 按"最省"的顺序往下试。
        #
        # ① 写着 def/plot 却解析不过 = 他的脚本有错,真实报错原样给他,**同时**给「AI 修错」按钮。
        #    2026-09-13 以前这里直接 raise、不给按钮:用户粘 ThinkScript 脚本 17 句只错一处
        #    (average_volume_50d_calc),只能自己对着报错改。怕的是 AI「悄悄改」——
        #    现在要点了才改、只许改报错那几句、改了哪几句逐句列出来(screen_nl.fix_script)。
        # ② 本地关键词匹配 —— **零 token**,覆盖「字段+比较符+数字」这类规整描述。
        # ③ 都不行才轮到 AI,而且**必须 allow_ai=True**(前端弹按钮、用户点了才传)。
        #    默认不花钱是这条链路的设计目标。
        from app.services.quant import screen_kw, screen_nl
        if screen_nl.looks_like_script(script):
            if not allow_ai:
                raise NeedsAI(str(script_err), kind="script") from script_err
            # on_ai:真要调模型的这一刻才扣会员的 AI 次数(screen_quota)。用满了在这里抛,模型一次都不调
            if on_ai:
                on_ai()
            fixed = screen_nl.fix_script(script, str(script_err), md.label, meta.sma,
                                         meta.ema, meta.rsi, validate=_compile)
            script = fixed["script"]
            c = _compile(script)
            ai = {k2: fixed[k2] for k2 in
                  ("model", "attempts", "tokens_in", "tokens_out", "changes")}
            ai["mode"] = "fix"
            ai["error"] = str(script_err)
            ai["source_text"] = original_text
            ai["script"] = script
        else:
            from app.services.quant import screen_learned
            try:
                # names:字段原名不分大小写(perf.y → Perf.Y)。learned:之前 AI 识别学来的对照表,
                # 只补规则认不出的句子。表读不到时是空 dict,本地规则照常工作。
                k = screen_kw.translate(script, has_field, meta.sma, meta.ema, meta.rsi,
                                        names=meta.names,
                                        learned=screen_learned.table_for(screen_kw.candidate_keys(script)))
                script = k["script"]
                # 对照表里的表达式可能已经过时(字段下线、周期不支持)—— 这一步编译不过
                # 就整体落回 AI,AI 的新结果会覆盖掉那条旧的
                c = _compile(script)
                kw = {"matched": k["matched"], "notes": k.get("notes") or [],
                      "source_text": original_text,
                      "script": script}
                hit_ids = sorted({m["learned"]["id"] for m in k["matched"]
                                  if m.get("learned") and m["learned"].get("id")})
                if hit_ids:
                    screen_learned.record_hits(hit_ids)
            except ScreenError as kw_err:
                # 只写了字段、没写怎么比:阈值得用户自己定,不给 AI 按钮(AI 补的数字就是编的)
                if isinstance(kw_err, screen_kw.MissingComparison):
                    raise
                if not allow_ai:
                    # 不抛普通 ScreenError —— 路由要据此告诉前端"可以试试 AI"
                    raise NeedsAI(str(kw_err)) from kw_err
                if on_ai:
                    on_ai()
                translated = screen_nl.translate(
                    script, md.label, meta.sma, meta.ema, meta.rsi, validate=_compile)
                script = translated["script"]
                c = _compile(script)         # translate 里已经 validate 过,这里必成功
                ai = {k2: translated[k2] for k2 in
                      ("model", "attempts", "tokens_in", "tokens_out")}
                ai["source_text"] = original_text
                ai["script"] = script

    d = screen_dsl.decompose(script, c, has_field, meta.sma, meta.ema, meta.rsi)
    if frag is not None and frag["context"]:
        # 追加:上下文(当前脚本)只用来让片段编译得过,交给前端的只能是片段自己的条件行 ——
        # 否则 mergeAppend 会把原脚本的条件再追加一遍。term 行的名字是「plot名#k」或「宿主#k」
        own = frag["frag_names"] | {frag["plot_name"]}

        def _mine(n: str) -> bool:
            return n in frag["frag_names"] or n.split("#", 1)[0] in own and "#" in n
        d["conditions"] = [x for x in d.get("conditions") or [] if _mine(x["name"])]
        d["plot_refs"] = [n for n in d.get("plot_refs") or [] if n in frag["frag_names"]]
        d["plot_order"] = [n for n in d.get("plot_order") or [] if _mine(n)]
        d["combine"] = "all"
        d["term_host"] = ""
        d["extra_plots"] = []
        d["plot_name"] = frag["plot_name"]
        d["plot_expr"] = ""

    warnings = [DELAY_WARN]
    if c.series is not None:
        # 时间序列模式:在「生成」这一步就把「不支持 / 还没有的数据」说清楚(2026-09-15 用户要求),
        # 不等到点扫描才 400。这两条不给 AI 按钮 —— 数据没有,模型改脚本也改不出来
        from app.services.quant import screen_asof, screen_series
        plan = c.series
        avail = screen_asof.series_availability(md.key)
        if not avail["has_bars"]:
            raise ScreenError(
                f"这份脚本用到了 K 线偏移 / 递归 / 滚动窗口,要按逐日 K 线计算;"
                f"但{md.label}的全市场日线还没建好(由每晚的定时任务拉取)。目前只能写只用当天快照字段的条件。")
        if "open" in plan.needs and avail["open_ratio"] < 0.9:
            lines = [str(st.line) for st in c.stmts if _mentions(st.node, "open")]
            raise ScreenError(
                f"当前{md.label}日线只有收盘价、最高价、最低价和成交量,**还没有开盘价** —— "
                f"这份脚本第 {'、'.join(lines) or '?'} 行用到了 open(阳线 / 阴线判断)。"
                f"开盘价 2026-09-15 起入库,老行要等每晚整窗重拉之后才有"
                f"(目前 {avail['last']} 这天 {avail['open_have']}/{avail['n']} 只有开盘价);届时同一份脚本不用改。"
                f"现在想先跑,可以把 close > open 换成 close > close[1](收阳 → 收涨,口径不同,自己权衡)。")
        # 「时间序列模式怎么算 / 求值截至哪天」两条说明不再放进提示(2026-09-16 用户:不用显示),只留真正要用户处理的
        warnings = []
        for n in plan.notes:
            warnings.append(n)
        d["series"] = {"needs": sorted(plan.needs), "depth": plan.depth, "window": plan.window,
                       "rec": list(plan.rec), "funcs": list(plan.funcs), "as_of": str(avail["last"])}
    if md.note:
        warnings.append(md.note)
    if any("market_cap" in f for f in c.fields):
        warnings.append(MARKET_CAP_WARN)

    d["market"] = md.key
    d["market_label"] = md.label
    d["fields"] = c.fields
    # 界面据此提示「官方示例 · 不计扫描次数」;真正是否扣次数由 /screener/run 再判一次(前端的话不算数)
    d["official_preset"] = official_preset_of(script, md.key)
    if case_changes:
        warnings.append(screen_dsl.CASE_NOTE.format(changes="、".join(case_changes[:8])))
    if frag is not None:
        warnings.extend(frag["notes"])
    d["warnings"] = warnings
    if kw:
        # 本地关键词匹配出来的 —— 同样要可核对:哪一句变成了哪个表达式。
        # 规则匹配不会像模型那样瞎编,但会**理解偏**(比如把"量"当成成交量而不是量比),
        # 所以逐句对照必须摆出来。
        d["kw"] = kw
        # 有损近似(上穿按"当前在上方"处理)要单独亮出来 —— 混在条件里用户看不出来
        for n in kw.get("notes") or []:
            warnings.append(n)
        n_learned = sum(1 for m in kw.get("matched") or [] if m.get("learned"))
        if n_learned:
            warnings.append(
                f"其中 {n_learned} 句来自之前的 AI 识别(已记在对照表里,这次没花 token)。"
                "对照表是 AI 学来的,**请核对**;不对的话点那句旁边的「忘掉它」。")
        warnings.append(
            "以上条件由本地关键词匹配得出(未使用 AI,零成本),"
            "已通过语法与字段校验。逐句对照见上方折叠区,不对的话可直接改或改用 AI 识别。")
    if ai and ai.get("mode") == "fix":
        # 修脚本不进对照表:那张表学的是「大白话 → 表达式」,拿脚本当原文会学出一堆垃圾键
        d["ai"] = ai
        warnings.append(
            f"你的脚本编译不过,{ai['model']} 按报错改了 {len(ai.get('changes') or [])} 句"
            f"(逐句对照见上方,由程序比对得出),其余原样保留。"
            f"**改动的周期 / 数字不是你写的**,跑扫描前请确认。")
    elif ai:
        # ── 学:把这次 AI 识别记进对照表(2026-09-11 用户要求)──────────
        # 下次同样的说法(数字可以不同)直接本地识别、零 token。
        # 学失败只记日志,绝不影响这次的结果 —— 用户要的是条件,不是对照表。
        ai["learned"] = 0
        try:
            # plot 里有直接写的表达式项(decompose 的 kind='term')时不学:plot_refs 只含裸名字那几项,
            # 学进去的是整句话 → 一部分条件,下次同一句话会静默少条件
            has_term = any(x.get("kind") == "term" for x in d.get("conditions") or [])
            exprs = screen_kw.inline_conditions(
                d.get("conditions") or [],
                d.get("plot_refs") or [] if d.get("combine") == "all" and not has_term else [])
            if exprs:
                # 每条都得能**单独**编译 —— 下次是拆开、换了数字再用的
                for e in exprs:
                    _compile(f"def c_ = {e};\nplot scan = c_;")
                def _rule_ok(cl: str) -> bool:
                    return screen_kw.rule_match(cl, has_field, meta.sma, meta.ema, meta.rsi,
                                                meta.names) is not None
                entries = screen_kw.learn_entries(original_text, exprs, _rule_ok)
                ai["learned"] = screen_learned.learn(
                    entries, original_text, ai.get("model"), md.key, user_id)
        except Exception as e:                                    # noqa: BLE001
            # 本模块用标准库 logging(第 54 行 log),不是 loguru —— 写成 logger 的话
            # except 里会再抛 NameError,把这次 AI 识别的结果整个打成 500
            log.warning("[screen_learned] 这次 AI 识别没记进对照表: %s", e)

        # 让前端能明确标出"这几条是 AI 翻的",并且把生成的脚本亮出来给人核对。
        # AI 产出的东西必须可审计 —— 用户至少要能看见它到底写了什么才敢用。
        d["ai"] = ai
        warnings.append(
            f"以上条件由 {ai['model']} 根据你的描述自动翻译,已通过语法与字段校验,"
            f"但**是否符合你的本意需要你自己确认**。跑扫描前请逐条核对。")
    return d


# ═══════════════════════════════════════════════════════════════
# 预置脚本 —— 前端「示例」与 MCP 的 preset 参数共用一份
# 2026-09-11 用户精简:删了「强势股 RS≥80」(rs_leaders)与「VCP 波动收缩」(vcp · 五条规则那份,
# 脚本在 git show 136bb35 里);「RS线持续向上」改名「精选强势股」、「VCP 区间收缩」改名「VCP 波段收缩」。
# **改名只改 name,key 不动**:用户隐藏示例(screener.html · localStorage)和 MCP 的 preset 参数都认 key。
# 增删示例要同步 huntercode mcp/screener_mcp.py 的 preset 清单(本仓 scripts/opencode-mcp/ 是它的副本)。
# ═══════════════════════════════════════════════════════════════

PRESETS = [
    {
        "key": "uptrend",
        "name": "上升趋势",
        "market": "us",
        "desc": "均线多头排列 + 逼近 52 周高点 + 有量。thinkorswim 经典 Stock Hacker 脚本。",
        "script": """# ===== 上升趋势 =====
# 均量条件(90 日均量 > 100 万股)
# 注:扫描源只有 10/30/60/90 天均量,原脚本的 Average(volume,100) 映射不了
def avgVol90 = Average(volume, 90);
def cond_avgVol = avgVol90 > 1000000;

# 当前成交量 > 100 万股
def cond_curVol = volume > 1000000;

# 股价 > 20
def cond_price = close > 20;

# 距 52 周高点 10% 以内
def high52 = Highest(high, 252);
def cond_52week = (high52 - close) / high52 <= 0.10;

# 均线多头排列
def sma20 = Average(close, 20);
def sma50 = Average(close, 50);
def sma200 = Average(close, 200);
def cond_sma20_50 = sma20 > sma50;
def cond_sma50_200 = sma50 > sma200;
def cond_price_sma50 = close > sma50;

plot scan = cond_avgVol
        and cond_curVol
        and cond_price
        and cond_52week
        and cond_sma20_50
        and cond_sma50_200
        and cond_price_sma50;
""",
    },
    {
        "key": "value_oversold",
        "name": "低估值超卖",
        "market": "a",
        "desc": "PE 与 PB 双低 + RSI 超卖 + 盈利能力为正。用来找左侧候选票。",
        "script": """# ===== 低估值 + 超卖 =====
def cond_pe  = price_earnings_ttm > 0 and price_earnings_ttm < 15;
def cond_pb  = price_book_fq < 2;
def cond_roe = return_on_equity > 8;
def cond_rsi = RSI() < 35;
def cond_liq = Average(volume, 30) > 5000000;

plot scan = cond_pe and cond_pb and cond_roe and cond_rsi and cond_liq;
""",
    },
    {
        "key": "breakout_volume",
        "name": "放量突破",
        "market": "us",
        "desc": "今日相对成交量放大 + 站上 20/50 日线 + 距 3 月高点 3% 以内。",
        "script": """# ===== 放量突破 =====
def cond_rvol  = relative_volume_10d_calc > 2;
def cond_ma    = close > Average(close, 20) and close > Average(close, 50);
def high3m     = Highest(high, 63);
def cond_near  = (high3m - close) / high3m <= 0.03;
def cond_price = close > 10;

plot scan = cond_rvol and cond_ma and cond_near and cond_price;
""",
    },
    {
        "key": "hk_dividend",
        "name": "港股高股息",
        "market": "hk",
        "desc": "股息率 > 5% + 低负债 + 盈利为正。注意金额字段是 HKD 计价。",
        "script": """# ===== 高股息 + 低负债 =====
def cond_div  = dividends_yield_current > 5;
def cond_debt = debt_to_equity < 1;
def cond_roe  = return_on_equity > 5;
def cond_liq  = Average(volume, 30) > 1000000;

plot scan = cond_div and cond_debt and cond_roe and cond_liq;
""",
    },
]


PRESETS.append({
    "key": "rs_line_up",
    "name": "精选强势股",            # 2026-09-11 用户改名(原「RS线持续向上」),key 不变
    "market": "us",
    "desc": "RS 评级 ≥80,且 RS 线(个股 ÷ 标普500)已连续 50 个交易日以上站在自身 21 日均线之上。",
    "script": """# ===== 强势且持续跑赢大盘 =====
# RSLineUpDays():RS 线(收盘 ÷ 基准指数)连续站在自身 21 日均线之上的交易日数
def cond_rs   = RS() >= 80;
def cond_line = RSLineUpDays() > 50;
def cond_liq  = Average(volume, 30) > 500000;

plot scan = cond_rs and cond_line and cond_liq;
""",
})

# 2026-09-11 用户对快照版 VCP 脚本(git log --grep=VCP 那份)提的四点,改好的版本。
# 1) 扫描源的 High.5D / High.3M 实测是 4 / 61 根,不是 5 / 63 —— 换成自家日线的精确窗口;
# 2) 区间分母用最高价(= 回撤深度,和 vcp_*_depth 同一口径,最大 100%)。
#    用户建议用收盘价,理由是「低价股会失真」—— 比值与股价高低无关,这条理由不成立;
#    真正的问题是除以最低价在大回调时会放大(跌 50% 显示成 100%)。除以收盘价会让同一个区间
#    随今天收在区间哪里而变,所以取最高价;
# 3) 加低点抬高;4) 枢轴用 vcp_pivot_dist —— 拿近 1 月最高当枢轴,「≤ 枢轴 × 1.03」恒成立。
PRESETS.append({
    "key": "vcp_range",
    "name": "VCP 波段收缩",          # 2026-09-11 用户改名(原「VCP 区间收缩」),key 不变
    "market": "us",
    "desc": "3 个月 → 1 个月 → 5 天价格区间逐级收紧 + 低点抬高 + 在枢轴附近。"
            "窗口严格按最近 63 / 21 / 5 个交易日(自家日线),趋势模板与流动性来自扫描源。",
    "script": """# ===== VCP 波段收缩 · 精确交易日窗口 =====
# high_/low_Nd 严格是最近 N 根日线(扫描源的 High.5D / High.3M 实测只有 4 / 61 根)
# 区间 = (最高 - 最低) ÷ 最高 = 从高点回撤的深度,和 VCP 收缩深度同一口径
def rng3m = (high_63d - low_63d) / high_63d;
def rng1m = (high_21d - low_21d) / high_21d;
def rng5d = (high_5d - low_5d) / high_5d;

# 一、流动性与趋势模板
def c_price = close > 10;
def c_liq   = average_volume_30d_calc > 500000;
def c_trend = close > SMA50 and SMA50 > SMA150 and SMA150 > SMA200;
def c_rs    = rs_rating >= 70;

# 二、波动逐级收紧:前面有过像样回调 → 近 1 月收紧(但不是被收购锁价)→ 近 5 天更紧
def c_depth   = rng3m >= 0.15;
def c_tight1m = rng1m <= 0.10 and rng1m >= 0.02;
def c_shrink  = rng1m <= rng3m * 0.5;
def c_tight5d = rng5d <= rng1m * 0.6;

# 三、低点抬高:3 个月的最低点在一个月以前,最近一个月的低点至少高 2%
def c_higher_low = low_21d > low_63d * 1.02;

# 四、在枢轴附近:最后一次收缩的高点下方 5% 以内,或刚突破不超过 3%
def c_pivot = vcp_pivot_dist >= -3 and vcp_pivot_dist <= 5;

# 五、量能萎缩
def c_vdry = average_volume_10d_calc < average_volume_90d_calc;

plot scan = c_price and c_liq and c_trend and c_rs
        and c_depth and c_tight1m and c_shrink and c_tight5d
        and c_higher_low and c_pivot and c_vdry;
""",
})


# 2026-09-15 用户:「顶部信号和进场触发存成一个官方示例,合并一下,方便观察效果」+「一旦触发了信号 5 天之内都保存在选股器列表里」。
# 规则是用户定的(连续收阳 · 小盘涨 80%+ · 逐日加速 · 几乎无阴线 · 递增放量 + 顶部天量 · 进场只在跌破前一日收盘),
# 参数经 2022-01 ~ 2026-09 美股全市场验证后放宽四处(阴线 1 根 · 5 日涨幅 · 3 倍均量且 60 日天量 · 成交额 5000 万),
# 结论与口径写在脚本注释里、仓内 CLAUDE.md「猎杀FOMO做空示例」节。
#   · 列表条件用 `signal within keepDays + 1 bars`,**不要改成递归计数** daysSinceSignal <= 5:
#     递归定义在热身期(信号算不出)是 NaN 并一路传下去,几千只从没出过信号的票会全部计入「算不出」。
#   · 进场价只引用自己的上一根(IsNaN(entryPrice[1])),不引用别的递归定义当天的值 —— 不依赖引擎里递归定义之间的求值顺序。
#   · 大小盘用 20 日均成交额代替市值:快照市值没有历史,时间回溯 / 悬停命中日里整批为空。
PRESETS.append({
    "key": "fomo_short",
    "name": "猎杀FOMO做空",
    "market": "us",
    "desc": "散户狂热顶部:连续收阳 + 5 日暴涨(小盘 80% 起)+ 逐日加速 + 递增放量与 60 日天量。"
            "信号出现后留在列表 5 天,stage 列看有没有跌破前一日收盘(= 进场),retSinceEntry 看进场后涨跌。"
            "2022~2026 验证:跌破后第 5 天中位 -9.4%,样本少、须设止损。",
    "script": """# ===== 猎杀FOMO 做空 · 顶部信号 + 5 天跟踪 =====
# 收盘后扫描。列表 = 最近出过「散户狂热顶部」信号的票:信号当天起留 keepDays 天,方便看后续走势。
# 顶部信号**不是进场点**。进场只有一个时机:之后盘中跌破前一日收盘价(看 stage 列)。
#
# 结果表里要看的列:
#   daysSinceSignal  距信号第几天(0 = 今天刚出信号)
#   signalClose      信号日收盘价
#   stage            1 = 还没跌破前一日收盘,等;2 = 已跌破,进场
#   entryPrice       进场价 = 第一次跌破那天的 min(开盘价, 前一日收盘)
#   retSinceEntry    进场后到今天收盘的涨跌(负数 = 做空在赚)
# 其余数值列(cumRet、amount 等)是**今天**的值,不是信号日的。
#
# 参数来自 2022-01 ~ 2026-09 美股全市场验证:信号次日跌破昨收进场,43 笔触发后第 5 天中位 -9.4%、
# 72% 下跌、每一年中位都为负;没跌破昨收的信号之后 5 天中位 +16%(不跌破就别空)。
# 样本少、有幸存者偏差、未计融券成本,5 天内最高价相对进场中位约 +17% —— 实际做空必须设止损。

input lookback = 5;             # 看最近几根 K 线
input minBullDays = 3;          # 连续阳线至少几天
input maxBearInWindow = 1;      # 窗口内最多几根阴线
input smallAmt = 20000000;      # 20 日均成交额低于它算小盘(没有历史市值,用成交额代替)
input largeAmt = 200000000;     # 20 日均成交额高于它算大盘
input minRetSmall = 0.80;       # 小盘:5 日至少涨多少
input minRetMid = 0.50;         # 中盘
input minRetLarge = 0.30;       # 大盘
input volMult = 3;              # 顶部当天成交量至少是 20 日均量的几倍
input minAmount = 50000000;     # 顶部当天成交额下限(美元)
input keepDays = 5;             # 信号出现后在列表里留几天

# ── 顶部信号 ──
def isBull = close > open;
def isBear = close < open;
def bullStreak = if isBull then bullStreak[1] + 1 else 0;
def bearCount = Sum(isBear, lookback);
def cumRet = close / close[lookback] - 1;

# 大小盘:20 日均成交额(不含今天)分三档,各档涨幅门槛不同
def avgAmt = Average(close[1] * volume[1], 20);
def needRet = if avgAmt < smallAmt then minRetSmall else if avgAmt < largeAmt then minRetMid else minRetLarge;

# 加速:今天涨幅是窗口内最大且比昨天大;涨幅一天比一天大
def ret = close / close[1] - 1;
def maxRet = Highest(ret, lookback);
def accel = ret >= maxRet and ret > ret[1];
def rising = ret > ret[1] and ret[1] > ret[2];

# 量:连续放大,顶部当天是 60 日天量且远超均量
def volUp3 = volume > volume[1] and volume[1] > volume[2];
def volMA20 = Average(volume[1], 20);
def volHigh60 = Highest(volume, 60);
def topVol = volume >= volHigh60 and volume > volMA20 * volMult;
def amount = close * volume;

def signal = bullStreak >= minBullDays
    and bearCount <= maxBearInWindow
    and cumRet >= needRet
    and accel
    and rising
    and volUp3
    and topVol
    and amount >= minAmount;

# ── 信号后跟踪 ──
def daysSinceSignal = if signal then 0 else daysSinceSignal[1] + 1;
def signalClose = if signal then close else signalClose[1];
# 进场:信号之后第一次盘中跌破前一日收盘(低开就按开盘价成交)
def entryPrice = if signal then Double.NaN
    else if IsNaN(entryPrice[1]) and low < close[1] then (if IsNaN(open) then close[1] else Min(open, close[1]))
    else entryPrice[1];
def stage = if IsNaN(entryPrice) then 1 else 2;
def retSinceEntry = close / entryPrice - 1;

plot scan = signal within keepDays + 1 bars;
""",
})

def preset(key: str) -> dict | None:
    for p in PRESETS:
        if p["key"] == key:
            return p
    return None


def listable_fields(market_key: str) -> list[str]:
    """「可用字段」列表里给用户看、给用户点的字段 —— **每一个都必须能原样写进脚本**。

    原来只排 `|`(多周期)和 `[`,带 `-` `+` / 数字开头的 27 个名字照样列出,点进生成框必定报不认识
    (2026-09-17 探针实测,见 screen_dsl.is_writable_name)。界面上的字段个数也按这个数,不写死。
    """
    meta = get_meta(market_key)
    return sorted(n for n in meta.names if screen_dsl.is_writable_name(n))


def field_search(market_key: str, q: str, limit: int = 50) -> list[dict]:
    """字段搜索 —— 前端「可用字段」用。3777 个字段不可能列全,只能搜。

    返回 [{name, label}]:
      · name  字段名(英文)。**脚本里要写的就是它**,点击插入的也是它。
      · label 中文名,拿不准时为 None —— 界面上就显示英文原名。
              半吊子翻译比不翻更误导,详见 screen_dsl.field_label_cn。

    中文名也参与搜索:用户搜「成交量」应该能找到 volume。
    """
    meta = get_meta(market_key)
    q = (q or "").strip().lower()
    base = listable_fields(market_key)
    labels = {n: screen_dsl.field_label_cn(n) for n in base}

    def pack(names):
        return _collapse([{"name": n, "label": labels.get(n)} for n in names], limit)

    if not q:
        return pack(base)
    exact = [n for n in base if n.lower() == q]
    prefix = [n for n in base if n.lower().startswith(q) and n.lower() != q]
    sub = [n for n in base if q in n.lower() and not n.lower().startswith(q)]
    hit = exact + prefix + sub
    # 中文命中排在英文子串命中之后 —— 搜英文时不希望被中文结果挤掉
    seen = set(hit)
    cn = [n for n in base if n not in seen and (labels.get(n) or "").lower().find(q) >= 0]
    return pack(hit + cn)


# 同族折叠 —— 只有**数字**不同的字段算一族(EMA10/EMA12/…/EMA300 共 31 个)。
#
# 不折叠的话搜一个 "e" 就被 31 个 EMA 刷满整屏,别的字段一个都看不见。
#
# **判据只看数字**,这一点是刻意的:`return_on_equity_fq / _fy / _ttm` 差的是
# 报告期字母,它们是三个**真正不同**的字段(最近季 / 最近年 / 滚动12个月),
# 折叠掉就没法选了。而 EMA10 与 EMA20 只是同一个指标的参数不同,
# 收起来让用户点开再挑周期,信息一点没少。
_FAMILY_MIN = 3


def _collapse(items: list[dict], limit: int) -> list[dict]:
    import re as _re
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for it in items:
        key = _re.sub(r"\d+", "#", it["name"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(it)

    out: list[dict] = []
    for key in order:
        g = groups[key]
        if len(g) < _FAMILY_MIN:
            out.extend(g)
            continue
        # 族标签:拿成员标签把数字换成 N(10日EMA → N日EMA)。
        # 成员没有中文名时退回族键(EMA# → EMA#),照旧是英文。
        first = g[0]
        fam_label = None
        if first.get("label"):
            fam_label = _re.sub(r"\d+", "N", first["label"])
        out.append({
            "name": first["name"],          # 代表项 · 前端不会直接插它
            "label": fam_label,
            "family": key.replace("#", "N"),
            "count": len(g),
            "members": g,
        })
    return out[:limit]
