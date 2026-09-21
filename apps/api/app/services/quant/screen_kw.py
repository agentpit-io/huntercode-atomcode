"""关键词匹配:自然语言 → 筛选脚本(**不花 token**)。

绝大多数筛选描述其实是很规整的三段式:**字段 + 比较符 + 数字**
(「成交量大于100万」「市盈率低于15」「RSI 小于 30」),再加上少数几个
成句的行话(「站上50日均线」「均线多头排列」)。这些用规则就能完整覆盖,
没必要每次都掏钱调模型。

## 位置:脚本 → 本地关键词 → (用户点了才)AI

`parse_script` 的顺序是:
  1. 当脚本解析。成功就结束。
  2. 本地关键词匹配。成功就结束 —— **零 token**。
  3. 都不行,返回 `can_try_ai`,前端弹出「AI 识别」按钮。
     **用户点了才花钱**,不点就一分不花。

## 全中或全不中,不做"部分识别"

一句话里有 4 个条件、只认出 3 个的时候,**不产出那 3 个**。
偷偷少一个条件,用户看到的是一份"看起来正常"的结果,而他以为的筛选
和实际跑的筛选不是一回事 —— 这种错最难发现。所以只要有一段没认出来,
就整体判失败,把没认出来的原文列出来,让用户自己决定是改写还是叫 AI。

## 数字单位

「100万」= 1000000,「1亿」= 100000000。
百分号**不换算**:扫描源的百分比字段本身就是以百分数计的
(return_on_equity 给的 15 就是 15%),写成 0.15 反而错。
"""
from __future__ import annotations

import re
from itertools import combinations

from app.services.quant.screen_dsl import ScreenError

# ═══════════════════════════════════════════════════════════════
# 词表
# ═══════════════════════════════════════════════════════════════

# 固定字段:中文/英文说法 → 扫描源字段名。
# 同一个字段可以有多种说法,长的写在前面(匹配时按长度倒序,避免「市盈率」被「市」截胡)。
_FIELD_WORDS: dict[str, str] = {
    # 「成交额」不在这里(2026-09-18):原来写成 "成交额": "volume",「成交额大于2000万」静默变成成交量 > 2000万股。
    # 成交额是金额、成交量是股数,单位差一个股价。金额写法统一走 _AMOUNT_RE(见 _Vocab._candidates)
    "成交量": "volume", "volume": "volume", "量能": "volume",
    "收盘价": "close", "股价": "close", "价格": "close", "现价": "close",
    "close": "close", "price": "close", "收盘": "close",
    "开盘价": "open", "open": "open",
    "最高价": "high", "high": "high",
    "最低价": "low", "low": "low",

    "市盈率ttm": "price_earnings_ttm", "市盈率": "price_earnings_ttm",
    "pe": "price_earnings_ttm", "pettm": "price_earnings_ttm",
    "市净率": "price_book_fq", "pb": "price_book_fq",
    "净资产收益率": "return_on_equity", "roe": "return_on_equity",
    "市值": "market_cap_basic", "总市值": "market_cap_basic",
    "marketcap": "market_cap_basic",
    "股息率": "dividends_yield_current", "分红率": "dividends_yield_current",
    "毛利率": "gross_margin_ttm",
    "营收增长": "total_revenue_yoy_growth_ttm",
    "营收同比": "total_revenue_yoy_growth_ttm",
    "收入增长": "total_revenue_yoy_growth_ttm",
    "负债权益比": "debt_to_equity", "负债率": "debt_to_equity",
    "流动比率": "current_ratio",
    "每股收益": "earnings_per_share_diluted_ttm", "eps": "earnings_per_share_diluted_ttm",
    "贝塔": "beta_1_year", "beta": "beta_1_year",

    # 光秃秃的「涨幅」= 当日涨跌幅。前面带了区间(近一个月 / 近20日 / 年内)的走 _PERF_RE,
    # 区间对不上字段就拒绝,**不许退回当日**(2026-09-18:「近一个月涨幅超过10%」曾产出 change > 10)。
    # 「跌幅」的方向在 _clause_to_expr 里翻转:「跌幅超过10%」= change < -10,不是 change > 10
    "涨跌幅": "change", "涨幅": "change", "跌幅": "change", "change": "change",
    "相对成交量": "relative_volume_10d_calc", "量比": "relative_volume_10d_calc",
    # 「换手率」不收(2026-09-18):原来映射成量比(今日量 ÷ 10 日均量),换手率是量 ÷ 流通股本,
    # 「换手率大于5%」静默变成「量比 > 5」。扫描源没有换手率字段,认不出比认错好

    "52周最高": "price_52_week_high", "52周新高": "price_52_week_high",
    "52周最低": "price_52_week_low", "52周新低": "price_52_week_low",
    "历史最高": "all_time_high", "历史新高": "all_time_high",

    "rsi": "RSI", "adx": "ADX", "atr": "ATR",
    # RS 相对强度评级(IBD 口径,1–99,见 screen_rs)。
    # "rs" 走英文词边界匹配,所以不会吃掉 "rsi" 里的 rs。
    #
    # **光秃秃的「相对强度」「相对强弱」不收**:中文里 RSI 就叫「相对强弱指数」,
    # 也常译作「相对强度指数」。「相对强度大于80」—— 80 既是常见的 RS 门槛,
    # 也是常见的 RSI 超买线,两种理解都说得通,猜哪个都可能静默出错。
    # 只收带「评级」「指数/指标」后缀、没有歧义的说法;光秃秃的交给用户改写或 AI。
    "rs": "rs_rating", "rs_rating": "rs_rating", "rs评级": "rs_rating",
    "rs值": "rs_rating", "ibd rs": "rs_rating", "rs rating": "rs_rating",
    "相对强度评级": "rs_rating", "rs相对强度": "rs_rating",
    "相对强弱指数": "RSI", "相对强弱指标": "RSI",
    "相对强度指数": "RSI", "相对强度指标": "RSI",

    # 常用技术指标的中文/缩写说法(2026-09-11 补;之前一个都不认)
    "macd柱": "MACD.hist", "macd红柱": "MACD.hist", "macd绿柱": "MACD.hist",
    "macd柱状": "MACD.hist", "macd": "MACD.macd",
    "dif": "MACD.macd", "diff": "MACD.macd", "dea": "MACD.signal",
    "macd信号线": "MACD.signal",
    # KDJ 的 K/D 必须带「值」或「线」—— 裸 k 会和「100k」这类单位撞车
    "k值": "Stoch.K", "d值": "Stoch.D", "k线值": "Stoch.K",
    "kdj的k": "Stoch.K", "kdj的d": "Stoch.D",
    "布林上轨": "BB.upper", "布林带上轨": "BB.upper", "boll上轨": "BB.upper",
    "布林中轨": "BB.basis", "布林带中轨": "BB.basis", "boll中轨": "BB.basis",
    "布林下轨": "BB.lower", "布林带下轨": "BB.lower", "boll下轨": "BB.lower",
    "vwap": "VWAP", "成交量加权均价": "VWAP", "均价线": "VWAP",
    "近1周涨幅": "Perf.W", "近1月涨幅": "Perf.1M", "近3月涨幅": "Perf.3M",
    "近6月涨幅": "Perf.6M", "近1年涨幅": "Perf.Y", "年初至今涨幅": "Perf.YTD",

    # VCP(2026-09-11)。**光秃秃的「收缩深度」不收** —— 第一次还是最后一次?
    # 两种理解数值差好几倍,猜哪个都可能静默出错,交给用户说清楚或走 AI
    "vcp收缩次数": "vcp_contractions", "收缩次数": "vcp_contractions",
    "第一次收缩深度": "vcp_first_depth", "首次收缩深度": "vcp_first_depth",
    "最后一次收缩深度": "vcp_last_depth", "最后收缩深度": "vcp_last_depth",
    "末次收缩深度": "vcp_last_depth",
    "最后一次收缩量比": "vcp_last_vol_ratio", "收缩量比": "vcp_last_vol_ratio",
    "距枢轴点": "vcp_pivot_dist", "距离枢轴": "vcp_pivot_dist", "距枢轴": "vcp_pivot_dist",
    "离枢轴": "vcp_pivot_dist", "底部天数": "vcp_base_days",
    "最低点量比": "vcp_low_vol_ratio", "低点量比": "vcp_low_vol_ratio",
    # 量价(同日第二批)。**光秃秃的「上涨天数」不收** —— 词表里已有「RS线上涨天数」,
    # 用户说「上涨天数大于50」时指的是哪个说不准;窗口必须带着「20日」说出来
    "近20日上涨天数": "up_days_20d", "20日上涨天数": "up_days_20d",
    "近20日下跌天数": "down_days_20d", "20日下跌天数": "down_days_20d",
    "近20日涨跌日均量比": "ud_vol_ratio_20d", "涨跌日均量比": "ud_vol_ratio_20d",
}

# 比较符。**长的必须排在短的前面**:「大于等于」不能被「大于」先吃掉。
#
# 带否定的说法必须**逐个**列出来 —— 漏一个,它就会被里面的肯定词命中,意思整个反过来。
# 2026-09-11 查出:「不少于」不在表里,「成交量不少于100万」命中「少于」→ volume < 1000000;
# 「未超过」「没超过」同理命中「超过」→ >。
_OP_WORDS: list[tuple[str, str]] = [
    ("大于等于", ">="), ("不小于", ">="), ("不低于", ">="), ("不少于", ">="), ("至少", ">="),
    ("小于等于", "<="), ("不大于", "<="), ("不高于", "<="), ("不超过", "<="), ("最多", "<="),
    ("不多于", "<="), ("没有超过", "<="), ("未超过", "<="), ("没超过", "<="),
    ("大于", ">"), ("高于", ">"), ("超过", ">"), ("多于", ">"), ("超出", ">"),
    ("小于", "<"), ("低于", "<"), ("少于", "<"), ("不到", "<"), ("低过", "<"),
    ("等于", "=="),
    (">=", ">="), ("<=", "<="), ("=>", ">="), ("=<", "<="),
    (">", ">"), ("<", "<"), ("==", "=="), ("=", "=="),
    ("greater than or equal", ">="), ("less than or equal", "<="),
    ("greater than", ">"), ("more than", ">"), ("above", ">"), ("over", ">"),
    ("less than", "<"), ("below", "<"), ("under", "<"),
    ("equals", "=="), ("equal to", "=="),
]

_UNIT = {"万": 1e4, "亿": 1e8, "k": 1e3, "m": 1e6, "b": 1e9}

_NUM_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*(万亿|亿|万|[kmb]|%|％)?", re.I)


def _parse_number(m: re.Match) -> float:
    v = float(m.group(1))
    u = (m.group(2) or "").lower()
    if u == "万亿":
        return v * 1e12
    if u in _UNIT:
        return v * _UNIT[u]
    # 百分号不换算 —— 扫描源的百分比字段本身就是百分数计的
    return v


def _fmt(v: float) -> str:
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return repr(round(v, 10)).rstrip("0").rstrip(".")


# ═══════════════════════════════════════════════════════════════
# 单句解析
# ═══════════════════════════════════════════════════════════════

#  `、` 必须切 —— 漏了它,「毛利率大于40%、营收同比大于20%」会被当成一句,
#  字段匹配只认出后一个,**前一个条件被悄悄丢掉**(实测)。静默少一个条件
#  正是这个模块最要避免的失败方式。
_SPLIT_RE = re.compile(r"[,，;；。、\n]|(?:\s+and\s+)|并且|而且|同时|以及", re.I)
# 「且」单独切一刀 —— 但不能切「而且」(上面已经处理),也不能切在词中间
_AND_RE = re.compile(r"且")

# 均线要认**两种语序**:
#   中文习惯  数字在前  20日均线 / 20日线 / 20日MA
#   技术分析  数字在后  MA20 / SMA20 / EMA20 / MA(20)
# 2026-09-11 之前只认前一种,「EMA20大于EMA50」整句识别不了;更糟的是
# 「收盘价大于MA20」会把 MA20 里的 20 当成阈值,静默产出 `close > 20`。
#
# `(?<![a-z])s?ma(?![a-z])` 两侧的断言不能省:
#   左边 —— 挡住 EMA 里的 ma(否则 EMA20 会同时被当成 SMA20)
#   右边 —— 挡住 MACD 里的 ma
_MA_RE = re.compile(
    r"(?:(\d+)\s*(?:日|天)?\s*(?:均线|线|(?<![a-z])s?ma(?![a-z])|移动平均))"
    r"|(?:(?<![a-z])s?ma\s*\(?\s*(\d+)\s*\)?)", re.I)
_EMA_RE = re.compile(
    r"(?:(\d+)\s*(?:日|天)?\s*ema)|(?:ema\s*\(?\s*(\d+)\s*\)?)", re.I)
_AVGVOL_RE = re.compile(r"(\d+)\s*(?:日|天)\s*(?:均量|平均成交量|均成交量)", re.I)
_RSI_N_RE = re.compile(r"rsi\s*\(?\s*(\d+)\s*\)?", re.I)

# ── 成交额(金额)与区间涨幅 · 2026-09-18 ─────────────────────────
# 用户实测「股价站上50日均线,成交额大于2000万,近一个月涨幅超过10%」:
#   成交额 → volume > 20000000(金额被当成股数)、近一个月涨幅 → change > 10(区间被当成当日)。
# 两句都「看起来正常」,扫描照跑 —— 本模块最怕的静默理解错。按写法类别整类处理:
#
# 成交额:当日 / N日均 / 没说几日 / 合计还是日均说不清,四种写法分开判。
#   · 当日成交额 → `close * volume`。扫描源 metainfo 里**没有**当日成交额字段(Value.Traded 不在白名单,
#     脚本里写了报不认识),收盘价 × 成交量是能写进脚本的唯一口径,盘中是最新价 × 当日累计量;写进 notes
#   · N日均成交额 → AvgValue.Traded_{N}d,扫描源只有 10/30/60/90 天;其余周期拒绝(不拿近的周期冒充)
#   · 「日均成交额」没说几天、「20日成交额」说不清是合计还是日均 → 拒绝,报错里给能用的写法
_AMOUNT_EXPR = "close * volume"
_AVG_AMT_DAYS = (10, 30, 60, 90)
_AMOUNT_RE = re.compile(
    r"(?:(?:近|最近|过去|前)\s*)?"
    r"(?:(\d+|半)\s*(个交易日|交易日|日|天|个星期|星期|周|个月|月|年)\s*(?:内|以来)?\s*(?:的)?\s*)?"
    r"(日均|平均|均)?\s*成交(?:金额|额度|额)", re.I)

# 区间涨幅:区间词紧挨在「涨幅 / 跌幅 / 涨跌幅」前面(中间可以有 内 / 以来 / 的)。
# 扫描源的区间涨幅按**自然日历**算(Perf.1M = 近一个月,不是近 21 个交易日),所以只收一一对应的说法,
# 「近20日」「近两周」「近30天」「本月」一律不换算 —— 和 RS 线「周/月不按 5/21 天换算」同一条理由。
_PERF_RE = re.compile(
    r"(?:(?:近|最近|过去|前)\s*)?"
    r"(?:(\d+|半)\s*(个交易日|交易日|日|天|个星期|星期|周|个月|月|年)"
    r"|(年初至今|年初以来|今年以来|今年|年内|本年度|本年|上市以来|本周|本月|上周|上月|本季度|本季"
    r"|当日|今日|今天|当天|日内))"
    r"\s*(?:内|以来|来|之内)?\s*(?:的)?\s*(涨跌幅|涨幅|跌幅)", re.I)
_PERF_BY_MONTH = {1: "Perf.1M", 3: "Perf.3M", 6: "Perf.6M", 12: "Perf.Y"}
_PERF_BY_YEAR = {1: "Perf.Y", 3: "Perf.3Y", 5: "Perf.5Y", 10: "Perf.10Y"}
_PERF_CHOICES = ("近5日 / 近1周 / 近1个月 / 近3个月 / 近6个月(半年) / 近1年 / 近3年 / 近5年 / 近10年 / "
                 "年初至今(年内) / 上市以来")
# 「涨幅」旁边出现、又没被 _PERF_RE 认走的区间说法 —— 一律拒绝,不许退回当日涨跌幅。
# 数字 + 日/天 后面接 均 / 线 的是均线周期,不算
_PERIOD_HINT_RE = re.compile(
    r"(?<![接靠附])近|最近|过去|年内|今年|年初|本年|上市|本周|本月|本季|上周|上月|以来"
    r"|(?:\d+|半)\s*(?:个交易日|交易日|个星期|星期|周|个月|月|年)"
    r"|\d+\s*(?:日|天)(?!\s*(?:均|线|ema|ma))", re.I)


def _perf_field(n: str | None, unit: str | None, kw: str | None) -> tuple[str | None, str]:
    """区间 → (字段, 说明)。字段为 None 表示对应不上,说明里写原因。"""
    if kw:
        if kw in ("当日", "今日", "今天", "当天", "日内"):
            return "change", ""
        if kw in ("年初至今", "年初以来", "今年以来", "今年", "年内", "本年度", "本年"):
            return "Perf.YTD", ""
        if kw == "上市以来":
            return "Perf.All", ""
        return None, f"「{kw}」是自然周 / 月 / 季,扫描源的区间涨幅是滚动窗口,不换算"
    unit = unit or ""
    if n == "半":
        return ("Perf.6M", "") if unit == "年" else (None, f"「半{unit}」没有对应的区间涨幅")
    k = int(n)
    if unit in ("个交易日", "交易日", "日", "天"):
        if k == 1:
            return "change", ""
        if k == 5:
            return "Perf.5D", ""
        return None, f"扫描源没有近{k}{unit}涨幅,按天只有近5日;不拿近1个月(自然月)去冒充近{k}{unit}"
    if unit in ("个星期", "星期", "周"):
        return ("Perf.W", "") if k == 1 else (None, f"扫描源没有近 {k} 周涨幅,按周只有近1周")
    if unit in ("个月", "月"):
        f = _PERF_BY_MONTH.get(k)
        return (f, "") if f else (None, f"扫描源没有近 {k} 个月涨幅,按月只有 1 / 3 / 6 / 12 个月")
    if unit == "年":
        f = _PERF_BY_YEAR.get(k)
        return (f, "") if f else (None, f"扫描源没有近 {k} 年涨幅,按年只有 1 / 3 / 5 / 10 年")
    return None, f"「{n}{unit}」对应不上区间涨幅"


def _amount_field(n: str | None, unit: str | None, avg: str | None) -> tuple[str | None, str]:
    """成交额写法 → (字段或表达式, 说明)。"""
    if not n and not avg:
        return _AMOUNT_EXPR, ""
    days = unit in ("个交易日", "交易日", "日", "天")
    if n and n != "半" and days and avg:
        k = int(n)
        if k in _AVG_AMT_DAYS:
            return f"AvgValue.Traded_{k}d", ""
        return None, (f"扫描源没有 {k} 日均成交额,只有 10 / 30 / 60 / 90 日;"
                      f"不拿别的周期冒充")
    if avg and not n:
        return None, "没说几天 —— 请写「10日均成交额」「30日均成交额」「60日均成交额」或「90日均成交额」"
    if not days:
        return None, "按周 / 月 / 年的成交额扫描源没有,只有当日成交额和 10 / 30 / 60 / 90 日均成交额"
    if n and not avg:
        return None, (f"「{n}{unit}成交额」是 {n}{unit}合计还是日均?说不清就不猜 —— "
                      f"日均请写「30日均成交额」这类(扫描源有 10 / 30 / 60 / 90 日),当天请写「成交额」")
    return None, "对应不上成交额字段"


# 用户直接写的**字段原名**:rs_line_up_days / market_cap_basic / Perf.Y / MACD.hist / close|1W
# 字母或下划线开头,段与段之间可以用 . 或 | 连接。两头都不许紧挨着标识符字符 ——
# 否则会从「rs_line_up_days」中间切出一个「line」来。
_IDENT_RE = re.compile(
    r"(?<![A-Za-z0-9_.|])[A-Za-z_][A-Za-z0-9_]*(?:[.|][A-Za-z0-9_]+)*(?![A-Za-z0-9_])")


def _looks_like_field_name(tok: str) -> bool:
    """像字段原名(带 _ . |),而不是普通英文单词(above / below / and)。"""
    return any(ch in tok for ch in "_.|")


# 中文没有词边界,短词会从别的复合词里被截出来(英文那边靠 \b,见 _candidates)。
# 这里登记「前面接了这些字,意思就不是这个字段了」的情况 —— 命中就当没看见这个词。
# 2026-09-11 实测:「涨跌量比大于1」产出 relative_volume_10d_calc > 1(今日量比),静默错。
# 词表里能认的长说法(收缩量比 / 涨跌日均量比)按长度优先先被认走,不受这里影响。
_SHADOW: dict[str, tuple[str, ...]] = {
    "量比": ("涨跌", "升降", "上下", "多空", "买卖", "内外", "内外盘", "日均", "均"),
}


_LABEL_CACHE: dict[tuple[int, int], tuple] = {}


def _label_aliases(names, has_field) -> tuple[list[tuple[str, str]], list[tuple[str, str]], dict[str, list[str]]]:
    """字段全集 → (长名 [(中文名小写, 字段名)] 按长度倒序, 两字短名 同结构, 同名多字段 {中文名: [字段…]})。

    收紧规则,都是为了不引入「静默理解错」:
      · **同名多字段不收**(历史最低 = Low.All 和 all_time_low):拿不准指哪个,宁可认不出,
        但报错里点名是哪几个字段(见 translate),用户改写字段名即可
      · **两字短名只在句首、且紧跟比较词时才认**(「跳空大于0」):两个字的词会从别的复合词里被截出来,
        放到句中任意位置就是 _SHADOW 那段的前科
      · **不和词表重名**:词表是逐条测过的说法,同名以词表为准
    只收能写进脚本的字段名(screen_dsl.is_writable_name),和「可用字段」列表同一口径。
    names 在一次部署里是同一个集合对象,按 (id, 长度) 缓存,标签拼装只算一次。
    """
    if not names:
        return [], [], {}
    key = (id(names), len(names))
    hit = _LABEL_CACHE.get(key)
    if hit is not None:
        return hit
    from app.services.quant.screen_dsl import field_label_cn, is_writable_name
    words = {w.lower() for w in _FIELD_WORDS}
    owners: dict[str, set[str]] = {}
    for n in names:
        if not is_writable_name(n) or not has_field(n):
            continue
        lab = field_label_cn(n)
        if not lab or lab.isascii():
            continue
        k = lab.strip().lower()
        if len(k) < 2 or k in words:
            continue
        owners.setdefault(k, set()).add(n)
    uniq = [(k, next(iter(v))) for k, v in owners.items() if len(v) == 1]
    long_ = sorted((kv for kv in uniq if len(kv[0]) >= 3), key=lambda kv: -len(kv[0]))
    short = [kv for kv in uniq if len(kv[0]) < 3]
    amb = {k: sorted(v) for k, v in owners.items() if len(v) > 1}
    if len(_LABEL_CACHE) > 8:
        _LABEL_CACHE.clear()
    _LABEL_CACHE[key] = (long_, short, amb)
    return long_, short, amb


def _bare_field(clause: str, vocab: "_Vocab") -> tuple[str, str | None] | None:
    """整句只有一个字段名 / 中文名、没有比较 → (字段名, 中文名);否则 None。

    2026-09-17 用户:在「可用字段」里点了 MACD.hist,生成框里就这一个词,点生成报「本地识别没看懂」——
    像是自己的产品不认识自己的字段。其实字段认得,缺的是「怎么比」。报错必须说到点子上,
    而且**不给 AI 按钮**:阈值只能用户自己定,AI 补一个数字就是在编数字。
    """
    t = clause.strip()
    if not t:
        return None
    from app.services.quant.screen_dsl import field_label_cn
    c = vocab.canon(t)
    if c:
        return c, field_label_cn(c)
    low = t.lower()
    for w, fld in _FIELD_WORDS.items():
        if w.lower() == low and vocab.has_field(fld):
            return fld, t
    for lab, fld in vocab.labels + vocab.short_labels:
        if lab == low:
            return fld, t
    return None


class MissingComparison(ScreenError):
    """认出了字段,但没写比较 —— 路由按普通 400 报给用户,不走「可以试 AI」。"""


class _Vocab:
    """把可用周期带进来 —— 能不能用 SMA37 由扫描源说了算,不在这里硬编码。"""

    def __init__(self, has_field, sma, ema, rsi, names=None):
        self.has_field = has_field
        self.sma, self.ema, self.rsi = set(sma), set(ema), set(rsi)
        # 按长度倒序,保证「市盈率ttm」先于「市盈率」、「52周最高」先于「最高价」
        self.words = sorted(_FIELD_WORDS.items(), key=lambda kv: -len(kv[0]))
        # 字段原名不分大小写:用户写 perf.y、RS_LINE_UP_DAYS 也要认。
        # 扫描源的名字大小写混用(Perf.Y / RSI / rs_rating),没有全集就只能精确匹配。
        self._lc = {n.lower(): n for n in names} if names else None
        self.labels, self.short_labels, self.ambiguous = _label_aliases(names, has_field)

    def canon(self, tok: str) -> str | None:
        """字段原名 → 规范写法;不是可用字段返回 None。"""
        if self.has_field(tok):
            return tok
        if self._lc is not None:
            return self._lc.get(tok.lower())
        return None

    def _candidates(self, text: str) -> list[tuple[int, int, str, str, str]]:
        """文本里所有可能的字段命中 → [(起点, -长度, 字段名, 原文, 错误)]。

        `错误` 非空表示"认出来了但用不了"(如 37 日均线),排序时照样参与 ——
        它排在最左就该报错,而不是被右边一个能用的字段顶掉。
        """
        low = text.lower()
        low_ns = low.replace(" ", "")
        out: list[tuple[int, int, str, str, str]] = []

        for m in _AVGVOL_RE.finditer(text):
            n = int(m.group(1))
            # 10/30/60/90 天来自扫描源,其余 2~250 天由自家日线算(screen_dsl.OWN_VOL_MAX,2026-09-14)
            err = "" if 2 <= n <= 250 else \
                f"「{m.group(0)}」算不了 —— 扫描源有 10/30/60/90 天均量,其余周期由自家日线计算,最长 250 天"
            out.append((m.start(), -len(m.group(0)),
                        f"average_volume_{n}d_calc", m.group(0), err))
        for m in _EMA_RE.finditer(text):
            n = int(m.group(1) or m.group(2))     # 两种语序,数字在不同分组
            err = "" if n in self.ema else f"没有 {n} 日 EMA"
            out.append((m.start(), -len(m.group(0)), f"EMA{n}", m.group(0), err))
        for m in _RSI_N_RE.finditer(text):
            n = int(m.group(1))
            fld = "RSI" if n == 14 else f"RSI{n}"
            err = "" if (n == 14 or n in self.rsi) else f"没有 RSI({n})"
            out.append((m.start(), -len(m.group(0)), fld, m.group(0), err))
        for m in _MA_RE.finditer(text):
            n = int(m.group(1) or m.group(2))
            err = "" if n in self.sma else \
                f"「{m.group(0)}」映射不了 —— 扫描源没有 SMA{n}"
            out.append((m.start(), -len(m.group(0)), f"SMA{n}", m.group(0), err))
        # 成交额 / 区间涨幅(2026-09-18):候选从区间词开始、比里面的「涨幅」「成交额」更长,
        # 最左最长的规则会让它们顶掉光秃秃的词表命中
        for m in _AMOUNT_RE.finditer(text):
            fld, why = _amount_field(m.group(1), m.group(2), m.group(3))
            if fld and fld != _AMOUNT_EXPR and not self.has_field(fld):
                fld, why = None, f"当前市场的扫描源没有 {fld}"
            err = "" if fld else f"「{m.group(0)}」映射不了 —— {why}"
            out.append((m.start(), -len(m.group(0)), fld or _AMOUNT_EXPR, m.group(0), err))
        for m in _PERF_RE.finditer(text):
            fld, why = _perf_field(m.group(1), m.group(2), m.group(3))
            if fld and not self.has_field(fld):
                fld, why = None, f"当前市场的扫描源没有 {fld}"
            err = "" if fld else (f"「{m.group(0)}」映射不了 —— {why}。能用的区间涨幅:{_PERF_CHOICES};"
                                  f"要按交易日算可以直接写脚本,例如 close / close[20] > 1.1")
            out.append((m.start(), -len(m.group(0)), fld or "change", m.group(0), err))

        for w, fld in self.words:
            if not self.has_field(fld):
                continue
            if w.isascii():
                # 纯英文词必须按**词边界**匹配。裸 `in` 会让 "RSI below 30"
                # 里的 "be(low)" 命中字段 low —— 实测真踩到,产出 `low < 30`。
                #
                # 边界里必须有 `_` `.` `|`(2026-09-11):原来只挡字母数字,
                # 「rs_line_up_days」开头的 rs 被认成 RS 评级,差一点静默产出 rs_rating > 50。
                m = re.search(r"(?<![a-z0-9_.|])" + re.escape(w) + r"(?![a-z0-9_]|[.|][a-z0-9])", low)
                if m:
                    out.append((m.start(), -len(w), fld, w, ""))
            else:
                i = low.find(w)
                if i >= 0 and any(low[max(0, i - len(p)):i] == p for p in _SHADOW.get(w, ())):
                    continue          # 「涨跌量比」里的「量比」不是今日量比 —— 意思变了,宁可认不出
                if i >= 0:
                    out.append((i, -len(w), fld, w, ""))
                elif w in low_ns:
                    # 原文里夹了空格(「市 盈 率」)。位置没法精确还原,
                    # 排到最后 —— 只在没有别的候选时才用它。
                    out.append((len(text), -len(w), fld, w, ""))

        # ── 字段原名(2026-09-11)────────────────────────────────
        # 词表只收了常用说法,而用户能直接写的字段有 3777 个。写原名的一律直接认 ——
        # 这是最没有歧义的写法,认不出来反而说不过去。
        # ── 字段中文名(2026-09-17)──────────────────────────────
        # 「可用字段」列表里显示的是中文名(K线·三只乌鸦、布林带中轨(50)…),用户照着打进来却认不出 ——
        # 探针实测列表里 380 个中文名写成「中文名大于0」全部报「没看懂」。列表上看得到的名字必须认得。
        # 只收唯一、至少 3 个字、且不和词表重名的(见 _label_aliases);标签里的数字不会被当阈值:
        # 通用规则从字段**结尾之后**才找数字。
        for lab, fld in self.labels:
            i = low.find(lab)
            if i >= 0:
                out.append((i, -len(lab), fld, text[i:i + len(lab)], ""))
        for lab, fld in self.short_labels:
            if low.startswith(lab) and _STARTS_CMP_RE.match(low, len(lab)):
                out.append((0, -len(lab), fld, text[:len(lab)], ""))

        # 同时记下**不认识**的标识符:落在它内部的候选全部作废。
        # 「rs_score」「close_price」「ema20_slope」都不是字段,里面的 rs / close / ema20
        # 不能拿出来猜 —— 那正是本模块最要避免的「静默理解错」。
        unknown: list[tuple[int, int]] = []
        # 同名多字段的中文名(盘前变动(绝对值) = pre_change_abs / premarket_change_abs)按设计不收,
        # 但它**内部**的子串同样不能拿出来猜:2026-09-18 探针「盘前变动(绝对值)大于0」产出 change_abs > 0,
        # 截的是里面「变动(绝对值)」—— 和「涨跌量比」里截出量比同一类。整段按不认识的标识符处理,
        # 句子认不出后由 translate 报「同时是哪几个字段的中文名」。
        for lab in self.ambiguous:
            for m in re.finditer(re.escape(lab), low):
                unknown.append((m.start(), m.end()))
        for m in _IDENT_RE.finditer(text):
            tok = m.group(0)
            c = self.canon(tok)
            if c:
                out.append((m.start(), -len(tok), c, tok, ""))
            else:
                unknown.append((m.start(), m.end()))
        if unknown:
            out = [cd for cd in out
                   if cd[0] >= len(text) or not any(
                       a <= cd[0] and cd[0] + (-cd[1]) <= b and (b - a) > (-cd[1])
                       for a, b in unknown)]
        return out

    def field(self, text: str) -> tuple[str, str] | None:
        """在文本里找字段 —— **取最左出现的那个**,同位置取更长的。

        2026-09-10 修:原来是按"模式类别"顺序返回(均线正则整体排在词表前面),
        于是「收盘价高于50日均线」的左操作数被判成 SMA50 而不是 close。
        句子里谁先出现谁就是左操作数,这是唯一说得通的规则。
        """
        cands = self._candidates(text)
        if not cands:
            return None
        cands.sort(key=lambda c: (c[0], c[1]))
        start, _neg, fld, word, err = cands[0]
        if err:
            raise ScreenError(err)
        return fld, word

    def ma_field(self, text: str) -> str | None:
        """只找均线类字段 —— 「站上/跌破 X 日均线」这类模板专用。

        不能用 field():那句话里 `收盘价` 出现在更左边,会被优先返回。
        """
        for _s, _n, fld, _w, err in sorted(self._candidates(text),
                                           key=lambda c: (c[0], c[1])):
            if err:
                raise ScreenError(err)
            if fld.startswith("SMA") or fld.startswith("EMA"):
                return fld
        return None

    def fields_in(self, text: str) -> list[tuple[int, int, str, str]]:
        """句子里**全部**字段,从左到右、互不重叠 → [(起, 止, 字段, 原文)]。

        贪心取最左最长:「macd柱」与「macd」同在 0 位时取前者,
        然后跳到它的末尾再找下一个。重叠的候选(EMA20 里的 ma)自然被跳过。
        """
        cands = sorted(self._candidates(text), key=lambda c: (c[0], c[1]))
        out: list[tuple[int, int, str, str]] = []
        pos = -1
        for start, neg, fld, word, err in cands:
            if start < pos:
                continue
            if err:
                raise ScreenError(err)
            end = start + (-neg)
            out.append((start, end, fld, word))
            pos = end
        return out


# 字段的"单位"。字段对字段比较时两边必须同单位,否则就是在比苹果和橘子。
#
# 2026-09-11 实测:「成交量大于50日均线」产出 `volume > SMA50` ——
# 成交量是股数(千万量级),均线是价格(几十块),这个比较对所有股票都成立,
# 等于一条废条件混进了筛选里,而且看起来还挺像回事。
def _unit(fld: str) -> str | None:
    if fld in ("close", "open", "high", "low", "VWAP", "price_52_week_high",
               "price_52_week_low", "all_time_high", "all_time_low",
               "high_5d", "low_5d", "high_21d", "low_21d", "high_63d", "low_63d") \
            or fld.startswith(("SMA", "EMA", "BB.", "High.", "Low.",
                               "KltChnl.", "DonchCh", "HullMA")):
        return "价格"
    if fld == "volume" or fld.startswith("average_volume_"):
        return "成交量"
    # 成交额是金额,和成交量(股数)不能互相比(2026-09-18)
    if fld == _AMOUNT_EXPR or fld.startswith("AvgValue.Traded_"):
        return "成交额"
    # 当日与区间涨幅都是百分数,可以互相比(「近1个月涨幅大于近3个月涨幅」)
    if fld == "change" or re.fullmatch(r"Perf\.(?:5D|W|1M|3M|6M|YTD|Y|3Y|5Y|10Y|All)", fld):
        return "涨幅"
    if fld.startswith("MACD."):
        return "MACD"
    if fld.startswith("Stoch."):
        return "随机指标"
    # 「大于50天」里的天是阈值单位 —— 只对这类字段成立(见 _clause_to_expr 末尾)
    if fld == "rs_line_up_days" or fld.endswith("_days") or fld in ("up_days_20d", "down_days_20d"):
        return "天数"
    # 两个 VCP 量比的分母是同一个(收缩前 50 天日均量),可以互相比:
    # 「最低点量比小于最后一次收缩量比」= 越接近低点量越小。今日量比、涨跌日均量比分母不同,不归这类
    if fld in ("vcp_last_vol_ratio", "vcp_low_vol_ratio"):
        return "VCP量比"
    return None


def _check_comparable(a: tuple, b: tuple, notes: list | None = None) -> tuple[tuple, tuple]:
    """字段对字段:单位不同 / 单位未知 → 拒绝,并尽量给出"你是不是想写…"。→ (a, b),可能改写过一边。

    唯一的改写:「成交量大于50日均线」。成交量不可能和价格均线比,这里的「均线」只能是均量 ——
    2026-09-11 以前产出 volume > SMA50(废条件),之后改成拒绝并问「是不是想写 50日均量」;
    2026-09-14 用户确认按 50 日均量理解、要本地直接识别(扫描源没有的周期由自家日线算)。
    只认「N日均线 / N日线」这种写法对应的 SMA,EMA / MA50 / 布林这些不猜;改写写进 notes。
    """
    ua, ub = _unit(a[2]), _unit(b[2])
    if ua and ua == ub:
        return a, b
    if {ua, ub} == {"成交量", "价格"}:
        ma = a if ua == "价格" else b
        mm = re.match(r"^SMA(\d+)$", ma[2])
        if mm and 2 <= int(mm.group(1)) <= 250 and re.search(r"均线|日线", ma[3] or ""):
            n = mm.group(1)
            fixed = (ma[0], ma[1], f"average_volume_{n}d_calc", ma[3])
            if notes is not None:
                notes.append(f"「{ma[3]}」按「{n}日均量」理解 —— 成交量不能和价格均线比")
            return (fixed, b) if ma is a else (a, fixed)
    hint = ""
    # 最常见的手误:把「均量」写成「均线」
    if {ua, ub} == {"成交量", "价格"}:
        ma = a if ua == "价格" else b
        n = re.search(r"\d+", ma[3] or "")
        hint = (f"是不是想写「{n.group(0)}日均量」?" if n else "")
    raise ScreenError(
        f"「{a[3]}」和「{b[3]}」不能直接比较"
        f"({ua or '单位未知'} 对 {ub or '单位未知'})。{hint}")


# ── 区间与双边比较(2026-09-14 · 第一轮评审 GN-006:「股价在10到20之间」「收盘价大于10小于20」本地要能处理)──
# 原来这两类一律拒绝:通用分支只认一个比较符,不拦就会丢掉上限(「市盈率大于10小于20」曾产出 pe > 10)。
# 现在专门认,但只认**骨架完全对得上**的:一个字段在前、后面恰好两个数、数字之间只有连接词。
# 骨架对不上一律返回 None,交给后面的拒绝逻辑 —— 宁可认不出,也不能认一半。
_OP_EXACT = {w: op for w, op in _OP_WORDS}
_NUM_STRICT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(万亿|亿|万|[kmb](?![a-z])|%|％)?", re.I)
_RANGE_SKEL = re.compile(r"(?:在|从|介于|位于|处于)?#(?:到|至|和|与|~|-|—)#(?:之间|区间内|区间|范围内|范围)?")


def _range_expr(t: str, fs: list) -> str | None:
    """一个字段 + 一个区间 → 两个比较用 and 连成一个条件;认不全返回 None。

    · 「在10到20之间」按闭区间(>= 与 <=),两个数颠倒写也按大小排好
    · 「大于10小于20」保留原比较符;方向必须一大一小、且真的是个非空区间,否则拒绝(「大于20小于10」是写错了,不替用户猜)
    · 两个数的单位必须一致:「10到20万」可能是 10万~20万,也可能是 10~200000,不猜
    · 数字必须都在字段**后面**:「10到20之间的股价」不认;标识符里的数字(MA20)不算阈值
    · 天数类字段不在这里认(「大于50天」的天有自己的规则,见 _clause_to_expr 末尾)
    """
    if len(fs) != 1:
        return None
    start, end, fld = fs[0][0], fs[0][1], fs[0][2]
    if _unit(fld) == "天数" or re.search(r"\d", t[:start]):
        return None
    tail = t[end:]
    # 英文整词单位(10 billion)在这里不猜 —— 通用分支里 _NUM_RE 会把它当 b 算,区间里两边单位就对不齐了
    if re.search(r"\d\s*(?:billion|million|thousand|bn|mn)", tail, re.I):
        return None
    # 严格版数字:k/m/b 后面紧跟字母时不是单位 ——「price above 10 below 20」里 10 后面的 b 是 below 的 b,
    # 用 _NUM_RE 会被读成 10 billion(2026-09-14 补英文用例时发现)
    nums = list(_NUM_STRICT_RE.finditer(tail))
    if len(nums) != 2:
        return None
    for m in nums:
        if m.start() > 0 and re.match(r"[A-Za-z(_]", tail[m.start() - 1]):
            return None
    if (nums[0].group(2) or "").lower() != (nums[1].group(2) or "").lower():
        return None
    a, b = _parse_number(nums[0]), _parse_number(nums[1])
    between = tail[nums[0].end():nums[1].start()]
    if nums[1].group(1).startswith("-") and not between.strip():
        # 「10-20之间」:连字符被数字正则吃成了负号
        between, b = "-", -b
    skel = re.sub(r"\s+|元", "", tail[:nums[0].start()] + "#" + between + "#" + tail[nums[1].end():])
    if _RANGE_SKEL.fullmatch(skel):
        if a == b:
            return None
        lo, hi = min(a, b), max(a, b)
        return f"{fld} >= {_fmt(lo)} and {fld} <= {_fmt(hi)}"
    m = re.fullmatch(r"(.+?)#(.+?)#", skel)
    if not m:
        return None
    op1 = _OP_EXACT.get(m.group(1))
    op2 = _OP_EXACT.get(re.sub(r"^(?:但是|但|并|而)", "", m.group(2)))
    if not op1 or not op2:
        return None
    big, small = (">", ">="), ("<", "<=")
    if not ((op1 in big and op2 in small and a < b) or (op1 in small and op2 in big and a > b)):
        return None
    return f"{fld} {op1} {_fmt(a)} and {fld} {op2} {_fmt(b)}"


# 英文比较词必须按**词边界**找,边界里算上 `_ . |`(和 _candidates 里英文字段词同一口径)。
# 2026-09-18 全量探针:「recommendation_under > 0」产出 recommendation_under < 0 —— 裸 find 从字段原名内部读出了 under;
# 同类还有 *_over_* / *_below_* / *_above_* 这些字段,以及 overbought / undervalued 这类普通英文单词。
def _op_re(w: str) -> re.Pattern:
    if w[0].isascii() and w[0].isalpha():
        return re.compile(r"(?<![a-z0-9_.|])" + re.escape(w) + r"(?![a-z0-9_])")
    return re.compile(re.escape(w))


_OP_RES = [(w, op, _op_re(w)) for w, op in _OP_WORDS]


def _find_op(text: str) -> tuple[str, str] | None:
    """最靠前的比较词 → (符号, 原词)。

    **调用方要传把字段名挖掉之后的文字**(_clause_core 的 outside):字段名和中文名内部的比较词不是比较,
    英文靠这里的词边界、中文靠挖掉字段 —— 两道一起才封得住。
    """
    low = text.lower()
    best = None
    for w, op, rx in _OP_RES:
        m = rx.search(low)
        if not m:
            continue
        # 取最靠前的那个;同位置取更长的(_OP_WORDS 已按长度组织)
        if best is None or m.start() < best[0]:
            best = (m.start(), op, w)
    return (best[1], best[2]) if best else None


# RS 线(个股收盘 ÷ 基准指数)。「相对强度线」是 IBD 文章的中文译法;
# 「相对强弱线」不收 —— 和 RSI(相对强弱指数)太近,同上面「相对强弱」不收的理由。
_RSL_RE = re.compile(r"rs\s*线|rs\s*line|相对强度线", re.I)
_RSL_UP_RE = re.compile(r"上涨|向上|上升|上行|走高|走强|站上|站稳|(?<![a-z])(?:up|rising|uptrend)(?![a-z])", re.I)
_RSL_DN_RE = re.compile(r"下跌|向下|下降|下行|走低|走弱|跌破|(?<![a-z])(?:down|falling)(?![a-z])", re.I)
_RSL_NUM_RE = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])\s*"
    r"(个交易日|交易日|天|日|trading\s*days?|days?|个星期|星期|周|个月|月|年|%)?", re.I)


def _rs_line_expr(t: str, vocab: _Vocab) -> str | None:
    """「RS线上涨时间大于50天」→ `rs_line_up_days > 50`。认不全返回 None。

    口径(2026-09-11 用户选定):RS 线连续站在自身 21 日均线之上的交易日数。
    所以句子里提到别的均线周期、提到下跌、单位是周/月 —— 一律不认,
    宁可让用户改写,也不能静默换成另一个意思。
    """
    if not vocab.has_field("rs_line_up_days"):
        return None
    if _RSL_DN_RE.search(t) or not _RSL_UP_RE.search(t):
        return None
    # 句子里的均线周期:只接受 21(我们的口径);均线里的数字不是阈值,先挖掉
    for m in _MA_RE.finditer(t):
        if int(m.group(1) or m.group(2)) != 21:
            return None
    t2 = _MA_RE.sub("〔均线〕", t)
    nums = list(_RSL_NUM_RE.finditer(t2))
    if len(nums) != 1:
        return None                                   # 没有天数,或者不止一个数 —— 不猜
    m = nums[0]
    unit = (m.group(2) or "").lower()
    if "." in m.group(1) or unit in ("个星期", "星期", "周", "个月", "月", "年", "%"):
        return None                                   # 周/月不按 5/21 天换算(节假日)
    n = int(m.group(1))

    # 比较符取离数字**最近**的那个(结束位置最靠后,同位置取更长)——
    # 「RS线高于21日均线超过50天」里真正的比较符是「超过」,不是「高于」
    head = t2[:m.start()].lower()
    best = None
    for w, op, rx in _OP_RES:
        hits = list(rx.finditer(head))              # 英文词同样按词边界(见 _op_re)
        if not hits:
            continue
        i = hits[-1].start()
        key = (i + len(w), len(w))
        if best is None or key > best[0]:
            best = (key, op)
    if best is None:
        rest = t2[m.end():].lower()
        if re.match(r"\s*(?:及以上|以上|or\s*more)", rest):
            best = (None, ">=")
        elif re.match(r"\s*(?:及以下|以下|以内|or\s*less)", rest):
            best = (None, "<=")
        else:
            return None
    return f"rs_line_up_days {best[1]} {n}"


def _clause_to_expr(clause: str, vocab: _Vocab, notes: list[str] | None = None) -> str | None:
    """见 _clause_core。说明先记在本句自己的列表里,**认出来了才并进 notes** ——
    认不出的句子会被对照表补上,留着这句的说明就成了对另一个表达式的错误解释。"""
    mine: list[str] = []
    expr = _clause_core(clause, vocab, mine)
    if expr is not None and notes is not None:
        notes.extend(mine)
    return expr


def _clause_core(clause: str, vocab: _Vocab, notes: list[str] | None = None) -> str | None:
    """一小句 → 表达式。认不出来返回 None(**不猜**)。

    `notes` 收集"认出来了但有损"的说明(如上穿按"当前在上方"处理),
    最终会进返回体给用户看。
    """
    if notes is None:
        notes = []
    t = clause.strip()
    if not t:
        return None
    low = t.lower()

    # ── 像字段原名、却不是可用字段(拼错 / 不存在)—— 整句拒绝 ──────
    # 不能忽略它、拿句子里剩下的部分去猜:「rs_line_up_day大于50天」少打一个 s,
    # 用户以为自己写的是 RS 线天数,拿剩下的去猜只会得到一个别的意思。
    for m in _IDENT_RE.finditer(t):
        tok = m.group(0)
        if _looks_like_field_name(tok) and not vocab.canon(tok):
            return None

    # ── RS 线上涨天数 —— 必须排在所有规则前面 ──────────────
    # 提到 RS 线的句子只走这个模板;认不全就返回 None,**不许落到下面的通用规则**:
    # 通用规则会把开头的 rs 认成 RS 评级,「RS线上涨时间大于50天」就成了 rs_rating > 50。
    if _RSL_RE.search(t):
        return _rs_line_expr(t, vocab)

    # ── 成句的行话,先于通用规则 ──────────────────────────
    # VCP「量能逐次递减」是个是非判断,不是字段比大小。
    # 只认带「量能」的说法 ——「成交量递减」可能是说近几天缩量,不一定是指每次收缩的量能,
    # 两者不是一回事;否定句(量能没有递减)本地不拆,交给用户或 AI
    if re.search(r"量能(?:逐次|依次|逐步)?递减", t):
        if re.search(r"不|没|未|非", t) or not vocab.has_field("vcp_vol_declining"):
            return None
        return "vcp_vol_declining == 1"

    if re.search(r"多头排列|均线多头|多头趋势", t):
        for n in (20, 50, 200):
            if n not in vocab.sma:
                return None
        return "SMA20 > SMA50 and SMA50 > SMA200"

    # 距离/接近 52 周最高 X%
    # 数字必须从「最高/新高」**之后**找 —— 直接在整句上搜会先抓到「52周」的 52,
    # 「距离52周最高不到10%」被算成 52%(实测)。
    m = re.search(r"(最高|新高)", t)
    if m and re.search(r"距离?|接近|靠近|以内|之内|不到", t):
        mm = _NUM_RE.search(t[m.end():])
        if mm:
            pct = _parse_number(mm) / 100.0
            return ("(price_52_week_high - close) / price_52_week_high <= "
                    + _fmt(pct))

    fs = vocab.fields_in(t)

    # ── 本地处理不了的句型:直接拒绝,交给用户或 AI ─────────────
    #
    # 这些句型**认得出字段和比较符**,所以不拦的话会走到下面的通用分支,
    # 产出一个"少了一半意思"的条件 —— 比拒绝危险得多(2026-09-11 对抗测试实测):
    #   「收盘价大于20日均线的1.05倍」 → close > SMA20       (1.05 倍被静默丢掉)
    #   「市盈率大于10小于20」         → pe > 10             (上限被静默丢掉)
    # 用户看到的是一个看起来很正常的条件,完全不会意识到少了东西。
    # 只看字段名**之外**的文字:「利息保障倍数(TTM)大于3」里的倍数是字段中文名的一部分,不是倍数句型
    # (2026-09-17 探针:8 个带「倍数」的中文名因此一律认不出)。字段名以外出现倍数照样拒绝。
    outside = "".join(" " if any(a <= i < b for a, b, _f, _w in fs) else ch for i, ch in enumerate(t))
    if re.search(r"\d\s*倍|倍数|百分之", outside):
        return None

    # ── 涨幅 / 成交额(2026-09-18)──────────────────────────────
    # 「涨幅」认成了当日涨跌幅,但句子里还有没被 _PERF_RE 认走的区间说法(「涨幅近一个月超过10%」
    # 「过去一段时间涨幅」)—— 用户说的是区间,退回当日就是静默错,拒绝并说清楚能写哪些
    if any(f[2] == "change" for f in fs):
        mh = _PERIOD_HINT_RE.search(outside)
        if mh:
            raise ScreenError(
                f"「{t}」里的「{mh.group(0)}」像是在说区间涨幅,但对应不上扫描源的字段 —— "
                f"能用的区间涨幅:{_PERF_CHOICES}(写在「涨幅」前面,如「近1个月涨幅大于10%」);"
                f"不写区间就是当日涨跌幅")
    # 「跌幅」:方向和字段相反。只在「一个字段 + 一个数」的通用句型里翻转(见末尾),
    # 区间、字段对字段、站上跌破这些句型里翻转规则说不清,一律拒绝
    drop = any(re.search(r"(?<!涨)跌幅$", f[3]) for f in fs)
    if drop and len(fs) != 1:
        return None
    for f in fs:
        if f[2] == "change" and f[3] in ("涨幅", "涨跌幅", "跌幅", "change"):
            notes.append(f"「{f[3]}」按当日涨跌幅理解;区间涨幅请写「近1个月涨幅」「年内涨幅」这类")
        elif f[2] == _AMOUNT_EXPR:
            notes.append(f"「{f[3]}」按 收盘价 × 成交量 计算(扫描源没有当日成交额字段,盘中是最新价 × 当日累计量)")

    # 区间 / 双边比较骨架完全对得上才认(见 _range_expr);对不上继续往下走,由下面两条拒绝
    rng = None if drop else _range_expr(t, fs)
    if rng:
        return rng
    # 一个字段后面跟着两个阈值数字、区间规则又没认出来 → 一定有一半意思落不进通用分支,拒绝。
    # 下面那条「数比较词」的检查不认「等于」(认了会把「大于等于」数成两个),
    # 「收盘价等于10小于20」曾因此产出 close == 10(2026-09-14 补区间用例时挖出来的)
    if len(fs) == 1:
        tail_nums = [m for m in _NUM_RE.finditer(t[fs[0][1]:])
                     if not (m.start() > 0 and re.match(r"[A-Za-z(_]", t[fs[0][1]:][m.start() - 1]))]
        if len(tail_nums) >= 2:
            return None
    if len(re.findall(r"大于|小于|高于|低于|超过|不到|不低于|不高于|不超过|[<>]=?", t)) >= 2 \
            and len(fs) <= 1:
        return None                     # 一个字段两个比较 = 区间,本地不拆
    # 「到」前面必须是数字才是区间(10到20、10%到20%)。原来写的是 `到\s*\d`,
    # 把「不到5」也当成区间拒掉了 ——「市盈率不到15」「股价不到20」一直识别不了,
    # 「不到」明明在比较符表里却从来走不到(2026-09-11 加 VCP 距枢轴时查出)
    if re.search(r"之间|区间|介于|\d\s*(?:%|％|万亿|亿|万)?\s*到\s*-?\d", t):
        return None

    # 「A比B高/低/大/小」—— 中文最常见的比较句式,比较符在句尾
    m = re.search(r"比.+?(高|低|大|小|多|少)\s*$", t)
    if m and len(fs) >= 2:
        f0, f1 = _check_comparable(fs[0], fs[1], notes)
        op = ">" if m.group(1) in ("高", "大", "多") else "<"
        return f"{f0[2]} {op} {f1[2]}"

    # ── 上穿 / 下穿 / 金叉 / 死叉 / 站上 / 跌破 ────────────────
    #
    # 两种句型,**按句中字段个数区分**,不能按关键词区分:
    #   一个字段  「站上50日均线」          主语省略 = 收盘价 vs 那条线
    #   两个字段  「5日均线上穿20日均线」    就是这两条线互相比
    #
    # 2026-09-10 修过一次同类 bug(「高于」被当成站上),当时只把「高于」挪出去,
    # 没有堵住句型本身 —— 于是「5日均线上穿20日均线」仍然被当成一个字段的句型,
    # 产出 `close > SMA5`,和用户说的毫无关系。这次按字段个数分流,整类封死。
    #
    # 快照没有历史,**判断不了"刚刚交叉"**,只能判"现在在上方/下方"。
    # 这是有损近似,必须写进 notes 让用户看见,不能静默当成金叉处理。
    up = re.search(r"站上|站稳|升破|突破|上穿|金叉", t)
    dn = re.search(r"跌破|下穿|失守|跌穿|死叉", t)
    if up or dn:
        word = (up or dn).group(0)
        op = ">" if up else "<"
        if len(fs) >= 2:
            f0, f1 = _check_comparable(fs[0], fs[1], notes)
            # 「股价突破52周新高」同样要 >=(见下方单字段分支的说明)
            if up and f1[2] in ("price_52_week_high", "all_time_high"):
                op = ">="
            if word in ("上穿", "下穿", "金叉", "死叉"):
                notes.append(f"「{t}」按「{f0[3]} 当前在 {f1[3]} "
                             f"{'之上' if up else '之下'}」处理 —— "
                             f"快照数据判断不了是不是**刚刚**发生交叉")
            return f"{f0[2]} {op} {f1[2]}"
        if len(fs) == 1 and _unit(fs[0][2]) == "价格" \
                and fs[0][2] not in ("close", "open", "high", "low"):
            tgt = fs[0][2]
            # 「突破 52 周新高」要用 >=:收盘价**等于**52 周最高正是创新高的那一天,
            # 写成 > 永远是 0 只 —— 一条静默失效的条件
            if up and tgt in ("price_52_week_high", "all_time_high"):
                op = ">="
            return f"close {op} {tgt}"
        return None

    # ── 通用:字段 + 比较符 + (字段 | 数字) ─────────────────
    op = _find_op(outside)          # 字段名内部的比较词不算(recommendation_under 里的 under)
    if not fs or not op:
        return None
    left = fs[0]
    if len(fs) >= 2:
        # 右边也是字段(「20日均线大于50日均线」)—— 必须同单位
        left, right = _check_comparable(left, fs[1], notes)
        return f"{left[2]} {op[0]} {right[2]}"

    tail = t[left[1]:]
    mnum = _NUM_RE.search(tail)
    if not mnum:
        return None
    # **标识符里的数字绝不能当阈值。**
    # 「收盘价大于MA20」在 MA20 还不认识的年代产出了 `close > 20` ——
    # 词表总有漏的(CCI20、MA(20) 的各种变体),漏认时宁可拒绝也别编一个阈值。
    a = left[1] + mnum.start()
    b = left[1] + mnum.end()
    before = t[a - 1] if a > 0 else ""
    if re.match(r"[A-Za-z(_]", before):
        return None
    # 数字后面跟的是什么,决定它是不是阈值(2026-09-11 重写这段):
    #
    #   「大于50天」「大于50个交易日」—— 天是**阈值的单位**。只对「天数」类字段成立,
    #       且后面不能再接东西(接了「均线」就说明 50日 是别的指标的周期)。
    #   「收盘价大于50日均线」—— 50 是均线周期,不是阈值(这条原来就挡着)。
    #   「大于50周」「大于50月」—— 周/月不按 5/21 天换算(有节假日),不猜。
    #
    # 原来是「数字后跟 日/天 就一律拒绝」,把「rs_line_up_days大于50天」也挡掉了。
    rest = t[b:]
    days_field = _unit(left[2]) == "天数"
    mu = re.match(r"\s*(个交易日|交易日|天|日)", rest)
    if mu:
        if not days_field or not re.fullmatch(
                r"\s*(?:及以上|以上|及以下|以下|以内|之内|内|左右)?\s*", rest[mu.end():]):
            return None
    elif re.match(r"\s*[周线均月年]", rest):
        return None
    if days_field and mnum.group(2):
        return None          # 天数字段带 % / 万 / 亿:单位对不上,不猜
    if drop:
        # 「跌幅超过10%」= 跌了 10% 以上 = 涨跌幅 < -10。原来直接产出 change > 10(涨了 10% 以上),方向整个反了。
        # 「跌幅超过-5%」这种负负得正的写法说不清,不猜
        v = _parse_number(mnum)
        if v < 0:
            return None
        flip = {">": "<", ">=": "<=", "<": ">", "<=": ">=", "==": "=="}[op[0]]
        notes.append(f"「{left[3]}{op[1]}{_fmt(v)}%」按「涨跌幅 {flip} -{_fmt(v)}%」理解(跌幅是跌掉的百分比)")
        return f"{left[2]} {flip} {_fmt(-v)}"
    return f"{left[2]} {op[0]} {_fmt(_parse_number(mnum))}"


# 「收盘价不低于10且不高于20」—— 后半句省略了主语,只剩「比较词 + 数字」。
# 单独一句「不高于20」永远认不出(没有字段),原来整句因此被拒;现在拼回前一句,交给 _range_expr 判区间。
# 前一句必须以「比较词 + 数字」结尾:「RS线连涨超过50天」「收盘价大于50日均线」这类不拼(天数 / 两字段,「小于20」不知道比谁)。
_CMP_WORDS = "|".join(rx.pattern for _w, _op, rx in _OP_RES)      # 英文词带词边界,同 _find_op
_CMP_NUM = r"\s*-?\d+(?:\.\d+)?\s*(?:万亿|亿|万|[kmb]|%)?\s*元?"
_STARTS_CMP_RE = re.compile(rf"\s*(?:{_CMP_WORDS}|[<>]=?|==|!=)", re.I)   # 两字短名后面必须紧跟比较
_BARE_CMP_RE = re.compile(rf"(?:{_CMP_WORDS}){_CMP_NUM}", re.I)
_ENDS_CMP_RE = re.compile(rf"(?:{_CMP_WORDS}){_CMP_NUM}$", re.I)


def _split(text: str) -> list[str]:
    """切句。**translate / candidate_keys / learn_entries 都用它** —— 对照表的 key 必须和识别同一套切分。"""
    parts: list[str] = []
    for seg in _SPLIT_RE.split(text or ""):
        if not seg:
            continue
        for sub in _AND_RE.split(seg):
            sub = sub.strip(" 　的了呢吧啊·、")
            if sub:
                parts.append(sub)
    merged: list[str] = []
    for p in parts:
        if merged and _BARE_CMP_RE.fullmatch(p) and _ENDS_CMP_RE.search(merged[-1]):
            merged[-1] = merged[-1] + " " + p       # 空格:英文「above 10」「below 20」不粘在一起
        else:
            merged.append(p)
    return merged


# ═══════════════════════════════════════════════════════════════
# 对外
# ═══════════════════════════════════════════════════════════════

def translate(text: str, has_field, sma: list[int], ema: list[int],
              rsi: list[int], names=None, learned: dict | None = None) -> dict:
    """关键词匹配。→ {script, matched:[(原文, 表达式)]}

    有任何一段没认出来就抛 ScreenError(带上没认出来的原文),**不产出半份脚本**。
    """
    text = _normalize(text)
    if not text:
        raise ScreenError("生成框是空的")

    # names = 可用字段全集,用来让字段原名不分大小写(perf.y → Perf.Y)。不传也能跑,只是要写对大小写
    vocab = _Vocab(has_field, sma, ema, rsi, names)
    clauses = _split(text)
    if not clauses:
        raise ScreenError("没有可识别的内容")

    notes: list[str] = []
    # (原文, 表达式或 None, 来源) —— 来源 None = 本地规则;{id, key} = 对照表(之前的 AI 识别)
    rows: list[tuple[str, str | None, dict | None]] = [
        (c, _clause_to_expr(c, vocab, notes), None) for c in clauses]

    # ── 规则认不出的,再查对照表 ──────────────────────────────
    # 规则永远优先:它是逐条测过的(tests/test_screen_kw.py),对照表只补它的缺。
    if learned and any(e is None for _c, e, _m in rows):
        whole = learned_lookup(text, learned)
        if whole:
            # 整句命中 —— 之前整句问过 AI、又没法逐句对齐的复杂说法
            rows = [(text, e, whole[1]) for e in whole[0]]
        else:
            filled: list[tuple[str, str | None, dict | None]] = []
            for c, e, m in rows:
                if e is None:
                    h = learned_lookup(c, learned)
                    if h:
                        filled.extend((c, e2, h[1]) for e2 in h[0])
                        continue
                filled.append((c, e, m))
            rows = filled

    unmatched = [c for c, e, _m in rows if e is None]
    matched = [(c, e, m) for c, e, m in rows if e is not None]

    if unmatched:
        bare = [(u, _bare_field(u, vocab)) for u in unmatched]
        bare = [(u, b) for u, b in bare if b]
        if bare:
            u, (fld, lab) = bare[0]
            shown = fld if lab in (None, fld) else f"{fld}({lab})"
            raise MissingComparison(
                f"「{u}」是字段 {shown},但还没写怎么比 —— 在后面接比较词和数字,"
                f"例如「{fld} > 0」或「{lab or fld}大于0」(数字换成你要的阈值)。"
                + (f" 另外还有 {len(unmatched) - 1} 句没看懂。" if len(unmatched) > 1 else ""))
        amb = sorted({(lab, tuple(fl)) for u in unmatched for lab, fl in vocab.ambiguous.items()
                      if lab in u.lower()}, key=lambda x: -len(x[0]))
        if amb:
            lab, fl = amb[0]
            raise ScreenError(
                f"「{lab}」同时是 {' / '.join(fl)} 这几个字段的中文名,本地拿不准指哪个 ——"
                f" 请直接写字段名,例如「{fl[0]} > 0」。")
        raise ScreenError(
            "本地识别没看懂这几句:" + " / ".join(f"「{u}」" for u in unmatched[:4])
            + (f" 等 {len(unmatched)} 处" if len(unmatched) > 4 else ""))
    if not matched:
        raise ScreenError("没认出任何筛选条件")

    lines, names = [], []
    for i, (src, expr, _m) in enumerate(matched, 1):
        # 名字用序号,不用中文 —— DSL 的标识符只允许英文
        name = f"cond_{i}"
        lines.append(f"def {name} = {expr};")
        names.append(name)
    lines.append("plot scan = " + " and ".join(names) + ";")
    return {"script": "\n".join(lines),
            "matched": [dict({"text": s, "expr": e}, **({"learned": m} if m else {}))
                        for s, e, m in matched],
            "notes": notes}


# ═══════════════════════════════════════════════════════════════
# 输入归一化 —— 在任何匹配之前做
# ═══════════════════════════════════════════════════════════════

# 中文数字 → 阿拉伯数字。只转**后面紧跟周期/单位**的,
# 「统一」「一致」这类词里的"一"不能碰。
_CN_DIG = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
           "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
# 「个月」「星期」「个星期」2026-09-18 补:「近一个月涨幅」原来一字不转,区间认不出、退回了当日涨跌幅
_CN_NUM_RE = re.compile(
    r"([零〇一二两三四五六七八九十百]+)(?=\s*(?:日|天|周|个交易日|年|月|倍|个月|个星期|星期))")
# 「涨幅超过三成」—— 成 = 10%
_CN_CHENG_RE = re.compile(r"([一二两三四五六七八九十]+)成")


def _cn2int(s: str) -> int:
    total = cur = 0
    for ch in s:
        if ch in _CN_DIG:
            cur = _CN_DIG[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
    return total + cur


def _normalize(text: str) -> str:
    """全角转半角 + 中文数字转阿拉伯数字。

    · NFKC:中文输入法打出来的 `＞` `２０` `ＲＳＩ` 全是全角,
      不归一的话一个比较符都认不出(2026-09-11 实测,三条全角用例全挂)。
    · 中文数字:「五日均线」「二十日均线」在中文里很常见。
    """
    import unicodedata
    t = unicodedata.normalize("NFKC", text or "").strip()
    t = _CN_NUM_RE.sub(lambda m: str(_cn2int(m.group(1))), t)
    t = _CN_CHENG_RE.sub(lambda m: str(_cn2int(m.group(1)) * 10) + "%", t)
    return t


# ═══════════════════════════════════════════════════════════════
# 对照表 —— 从 AI 识别里学来的说法(2026-09-11)
# ═══════════════════════════════════════════════════════════════
# 用户点过「AI 识别」之后,把结果记下来,下次同样的说法直接本地识别、零 token。
# 存储在 services/screen_learned.py;这里只放纯逻辑,不连库,tests/ 里能直接跑。
#
# 三条原则:
#   1. **按句学,数字做成空位。** 学「RS线连涨超过50天 → rs_line_up_days > 50」,
#      存成「rs线连涨超过{0}天 → rs_line_up_days > {0}」。下次「超过60天」也能认。
#      **重放时的数字永远取用户这次输入的,不取 AI 当时写的** —— 与「LLM 不许产数字」同一条线。
#   2. **单位留在模板里。** 「市值大于{0}亿」只配得上「亿」,「市值大于50万」对不上就不命中,
#      绝不拿亿的模板去套万。
#   3. **对不齐就不拆。** 一段话好几句、AI 给了好几个条件,只有「句数 = 条件数」且
#      每一句的数字都能在对应条件里找到,才逐句记;否则只记整句。对错了一句,
#      以后这句话就永远翻错 —— 宁可少学。

# 表达式里的**独立**数字(不含标识符里的 20:EMA20、SMA50)
_LIT_RE = re.compile(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?(?![A-Za-z0-9_.])")
_MAX_SLOTS = 6


def _key_base(text: str) -> str:
    """对照表的 key 用这个归一化:小写、去掉所有空白。学和查必须走同一个函数。"""
    return re.sub(r"\s+", "", _normalize(text or "").lower())


def _num_matches(t: str) -> list:
    """t 里**可能是阈值**的数字。紧跟在字母/下划线后面的(ema20、rs_20)是标识符的一部分,不算。"""
    out = []
    for m in _NUM_RE.finditer(t):
        a = m.start(1)
        if a > 0 and re.match(r"[a-z_]", t[a - 1]):
            continue
        out.append(m)
    return out[:_MAX_SLOTS]


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _tmpl(t: str, ms: list, slot_idx: list[int]) -> str:
    """把 t 里第 slot_idx 个数字(只换数字本身,单位留着)换成 {0}{1}…"""
    out, last = [], 0
    for k, i in enumerate(slot_idx):
        m = ms[i]
        out.append(t[last:m.start(1)])
        out.append("{%d}" % k)
        last = m.end(1)
    out.append(t[last:])
    return "".join(out)


def _fill(expr: str, vals: list[str]) -> str | None:
    bad = []

    def rep(m):
        k = int(m.group(1))
        if k >= len(vals):
            bad.append(k)
            return ""
        return vals[k]
    out = re.sub(r"\{(\d+)\}", rep, expr)
    return None if bad else out


def learned_lookup(text: str, table: dict | None) -> tuple[list[str], dict] | None:
    """查对照表。命中 → (填好数字的表达式列表, {id, key});没有 → None。

    `table` 是 {key: {"id":…, "exprs":[模板,…]}} 的快照。
    先试最具体的(一个数字都不挖空),再逐步挖空 —— 同一句话既有原样记录又有模板时,原样的优先。
    """
    if not table:
        return None
    t = _key_base(text)
    if not t:
        return None
    ms = _num_matches(t)
    idx = list(range(len(ms)))
    for size in range(0, len(idx) + 1):
        for combo in combinations(idx, size):
            key = _tmpl(t, ms, list(combo))
            ent = table.get(key)
            if not ent:
                continue
            vals = [_fmt(_parse_number(ms[i])) for i in combo]
            exprs = [_fill(e, vals) for e in ent.get("exprs") or []]
            if not exprs or any(e is None for e in exprs):
                continue
            return exprs, {"id": ent.get("id"), "key": key}
    return None


def candidate_keys(text: str) -> list[str]:
    """这段输入**可能**用到的全部对照表 key(整句 + 每一句,各种数字挖空组合)。

    存储层按这个清单去库里精确查,而不是整张表读进内存缓存 ——
    缓存在多进程部署下会过期不一致:用户在 A 进程点了「忘掉」,
    B 进程还拿着旧的那条,刚纠完错一重新生成又看到学错的结果(2026-09-11 端到端实测踩到)。
    必须和 translate / learned_lookup 走完全一样的归一化与切分。
    """
    t = _normalize(text or "")
    out: list[str] = []
    seen: set[str] = set()
    for x in [t] + _split(t):
        tt = _key_base(x)
        if not tt:
            continue
        ms = _num_matches(tt)
        idx = list(range(len(ms)))
        for size in range(0, len(idx) + 1):
            for combo in combinations(idx, size):
                k = _tmpl(tt, ms, list(combo))
                if k not in seen:
                    seen.add(k)
                    out.append(k)
    return out


def _entry(text: str, exprs: list[str]) -> list[tuple[str, list[str]]]:
    """一句话 + 它对应的表达式 → [(key, 表达式模板)]。能对上的数字挖成空位。"""
    t = _key_base(text)
    if not t or not exprs:
        return []
    ms = _num_matches(t)
    lits = [(ei, lm) for ei, e in enumerate(exprs) for lm in _LIT_RE.finditer(e)]
    vals = [_parse_number(m) for m in ms]
    used: set[int] = set()
    slots: list[tuple[int, int]] = []          # (文本里第几个数字, lits 里第几个)
    for i, v in enumerate(vals):
        # 文本里同一个值出现不止一次 —— 分不清哪个对哪个,都不挖空
        if sum(1 for v2 in vals if _same(v, v2)) != 1:
            continue
        cands = [j for j, (_ei, lm) in enumerate(lits)
                 if j not in used and _same(float(lm.group(0)), v)]
        if len(cands) == 1:
            used.add(cands[0])
            slots.append((i, cands[0]))
    key = _tmpl(t, ms, [i for i, _j in slots])
    out = list(exprs)
    by_expr: dict[int, list[tuple[int, int, int]]] = {}
    for k, (_i, j) in enumerate(slots):
        ei, lm = lits[j]
        by_expr.setdefault(ei, []).append((lm.start(), lm.end(), k))
    for ei, reps in by_expr.items():
        e = out[ei]
        for a, b, k in sorted(reps, reverse=True):
            e = e[:a] + "{%d}" % k + e[b:]
        out[ei] = e
    return [(key, out)]


def _consistent(clause: str, expr: str) -> bool:
    """这一句的每个数字,都能在这个条件里找到 —— 用来确认逐句对齐没有对错位。

    句子里一个数字都没有(「均线多头排列」)就无从确认,判 False,整段退回只记整句。
    """
    ms = _num_matches(_key_base(clause))
    if not ms:
        return False
    lits = [float(x) for x in _LIT_RE.findall(expr)]
    digits = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", expr)]   # 含标识符里的(SMA50 的 50)
    for m in ms:
        v, raw = _parse_number(m), float(m.group(1))
        if not any(_same(x, v) for x in lits) and not any(_same(x, raw) for x in digits):
            return False
    return True


def learn_entries(text: str, exprs: list[str], rule_ok) -> list[tuple[str, list[str]]]:
    """AI 翻译成功之后 → 该往对照表里记的 [(key, 表达式模板列表)]。

    exprs:   AI 脚本里 plot 引用的布尔条件,已内联成自包含表达式(见 inline_conditions),按 plot 顺序
    rule_ok: rule_ok(句子) → 本地规则认不认得。认得的不学 —— 规则逐条测过,对照表只补缺
    """
    t = _normalize(text or "")
    clauses = _split(t)
    if not clauses or not exprs:
        return []
    if len(clauses) == 1:
        return [] if rule_ok(clauses[0]) else _entry(clauses[0], exprs)
    if len(clauses) == len(exprs) and all(_consistent(c, e) for c, e in zip(clauses, exprs)):
        out: list[tuple[str, list[str]]] = []
        for c, e in zip(clauses, exprs):
            if not rule_ok(c):
                out += _entry(c, [e])
        return out
    return _entry(t, exprs)             # 对不齐:只记整句


def inline_conditions(conditions: list[dict], plot_refs: list[str]) -> list[str] | None:
    """AI 的脚本(中间变量 + 条件 + plot)→ 每个条件一条**自包含**的表达式。

    对照表里存的东西下次要单独拿出来用,那时没有 AI 当时起的中间变量名
    (def sma50 = Average(close, 50) 里的 sma50),所以必须把它们展开进去。
    展不开(引用了不存在的名字 / 套娃太深)就返回 None,这次不学。
    """
    defs = {c["name"]: c["expr"] for c in conditions if c.get("name") and c.get("expr")}
    if not plot_refs:
        return None

    def expand(e: str, depth: int = 0) -> str | None:
        if depth > 6:
            return None
        hit = []

        def rep(m):
            tok = m.group(0)
            if tok in defs:
                hit.append(tok)
                return "(" + defs[tok] + ")"
            return tok
        out = _IDENT_RE.sub(rep, e)
        return expand(out, depth + 1) if hit else out

    res = []
    for n in plot_refs:
        if n not in defs:
            return None
        x = expand(defs[n])
        if x is None:
            return None
        res.append(x)
    return res


def rule_match(clause: str, has_field, sma, ema, rsi, names=None) -> str | None:
    """只用本地规则认一句(不查对照表)。给学习环节判断「这句要不要学」。"""
    try:
        return _clause_to_expr(clause, _Vocab(has_field, sma, ema, rsi, names), [])
    except ScreenError:
        return None
