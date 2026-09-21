# -*- coding: utf-8 -*-
"""关键词匹配(screen_kw)回归用例 —— 不联网,不依赖 pytest 也能跑。

    cd apps/api && PYTHONPATH=. python tests/test_screen_kw.py
    # 或 pytest tests/test_screen_kw.py

## 为什么要有它

本地关键词匹配最危险的失败方式**不是报错,是静默理解错**:
产出一个看起来很正常、但意思完全不对的条件,用户照着跑扫描,完全不会发现。
这些用例每一条都来自一次真实的错误(用户报的,或对抗测试挖出来的),
改 screen_kw.py 之后必须全过才能上线。

用例分三类:
  SHOULD_MATCH    应当识别,且表达式必须**逐字**等于期望
  SHOULD_REJECT   应当拒绝(交给用户改写或点 AI 识别)
                  —— 这一类里任何一条被识别出来,都是一个静默错误
字段表用固定集合模拟,与线上 metainfo 的周期取值一致。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_API = os.path.dirname(_HERE)


def _load():
    """只加载两个纯逻辑模块,不拖起 app 包(本机和部分容器没有全套依赖)。"""
    for n in ("app", "app.services", "app.services.quant"):
        if n not in sys.modules:
            m = types.ModuleType(n)
            m.__path__ = []
            sys.modules[n] = m
    mods = {}
    for name in ("screen_dsl", "screen_kw"):
        full = f"app.services.quant.{name}"
        path = os.path.join(_API, "app", "services", "quant", f"{name}.py")
        spec = importlib.util.spec_from_file_location(full, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
        mods[name] = mod
    return mods["screen_dsl"], mods["screen_kw"]


SMA = [2, 3, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 20, 21, 25, 26, 30, 34, 40, 50,
       55, 60, 75, 89, 100, 120, 144, 150, 200, 250, 300]
EMA = list(SMA)
RSI = [2, 3, 4, 5, 7, 9, 10, 20, 21, 30]
FIELDS = set("""close open high low volume change market_cap_basic price_earnings_ttm
price_book_fq return_on_equity dividends_yield_current debt_to_equity gross_margin_ttm
total_revenue_yoy_growth_ttm relative_volume_10d_calc current_ratio beta_1_year
earnings_per_share_diluted_ttm price_52_week_high price_52_week_low all_time_high
RSI ADX ATR VWAP MACD.macd MACD.signal MACD.hist Stoch.K Stoch.D BB.upper BB.lower BB.basis
Perf.W Perf.1M Perf.3M Perf.6M Perf.Y Perf.YTD rs_rating rs_raw rs_line_up_days
vcp_contractions vcp_first_depth vcp_last_depth vcp_vol_declining vcp_last_vol_ratio
vcp_pivot_dist vcp_base_days vcp_low_vol_ratio up_days_20d down_days_20d ud_vol_ratio_20d
high_5d low_5d high_21d low_21d high_63d low_63d""".split())
FIELDS |= {f"SMA{n}" for n in SMA} | {f"EMA{n}" for n in EMA} | {f"RSI{n}" for n in RSI}
FIELDS |= {f"average_volume_{n}d_calc" for n in (10, 30, 60, 90)}
# 2026-09-18 · 成交额与区间涨幅(线上 metainfo 实有;当日成交额 Value.Traded **不在**白名单,故意不放)
FIELDS |= {f"AvgValue.Traded_{n}d" for n in (10, 30, 60, 90)}
FIELDS |= {"Perf.5D", "Perf.3Y", "Perf.5Y", "Perf.10Y", "Perf.All"}
# 2026-09-17 · 中文名识别用到的真实字段(中文名由 screen_dsl.field_label_cn 生成,不在这里写死)
FIELDS |= set("""Candle.3BlackCrows BB.basis_50 cash_dividend_coverage_ratio_ttm gap
Low.All all_time_low""".split())


SHOULD_MATCH = [
    # ── 基础三段式 ─────────────────────────────────────────
    ("成交量大于1000000", "volume > 1000000"),
    ("成交量大于100万", "volume > 1000000"),
    ("市盈率低于15", "price_earnings_ttm < 15"),
    ("市盈率低于15，市净率小于2", "price_earnings_ttm < 15 AND price_book_fq < 2"),
    ("净资产收益率大于15%", "return_on_equity > 15"),
    ("股价超过20，成交量不低于50万", "close > 20 AND volume >= 500000"),
    ("RSI 小于 30", "RSI < 30"),
    ("市值大于100亿", "market_cap_basic > 10000000000"),
    ("涨跌幅超过4%", "change > 4"),
    ("量比大于2", "relative_volume_10d_calc > 2"),
    ("90日均量大于100万", "average_volume_90d_calc > 1000000"),
    # ── 2026-09-14 · 扫描源没有的均量周期由自家日线算;「成交量对N日均线」按 N 日均量理解(用户确认)──
    ("50日均量大于100万", "average_volume_50d_calc > 1000000"),
    ("20天平均成交量大于50万", "average_volume_20d_calc > 500000"),
    ("成交量大于50日均线", "volume > average_volume_50d_calc"),
    ("成交量大于20日均量", "volume > average_volume_20d_calc"),
    ("成交量低于200日均线", "volume < average_volume_200d_calc"),
    ("50日均线小于成交量", "average_volume_50d_calc < volume"),     # 均线在左边也改写左边
    ("5日均量大于50日均量", "average_volume_5d_calc > average_volume_50d_calc"),
    ("成交量站上50日均线", "volume > average_volume_50d_calc"),
    ("收盘价大于50日均线", "close > SMA50"),                       # 价格对均线照旧是 SMA,不被改写
    # ── 2026-09-14 · 区间与双边比较(第一轮评审 GN-006)· 按写法类别 ─────────
    ("股价在10到20之间", "close >= 10 and close <= 20"),
    ("收盘价介于10和20之间", "close >= 10 and close <= 20"),
    ("股价在20到10之间", "close >= 10 and close <= 20"),             # 颠倒写按大小排
    ("股价在10-20之间", "close >= 10 and close <= 20"),              # 连字符不被当成负号
    ("股价在10至20元之间", "close >= 10 and close <= 20"),
    ("收盘价在１０到２０之间", "close >= 10 and close <= 20"),         # 全角
    ("市盈率在10到20之间", "price_earnings_ttm >= 10 and price_earnings_ttm <= 20"),
    ("净资产收益率在10%到20%之间", "return_on_equity >= 10 and return_on_equity <= 20"),
    ("成交量在100万到200万之间", "volume >= 1000000 and volume <= 2000000"),
    ("收盘价大于10小于20", "close > 10 and close < 20"),
    ("市盈率大于10小于20", "price_earnings_ttm > 10 and price_earnings_ttm < 20"),   # 曾产出 pe > 10
    ("股价小于20大于10", "close < 20 and close > 10"),
    ("收盘价不低于10不高于20", "close >= 10 and close <= 20"),
    ("收盘价大于10但小于20", "close > 10 and close < 20"),
    ("20日均线在10到20之间", "SMA20 >= 10 and SMA20 <= 20"),         # 均线周期里的 20 不是阈值
    ("股价在10到20之间，市盈率低于15", "close >= 10 and close <= 20 AND price_earnings_ttm < 15"),
    ("股息率大于5%且负债权益比小于1", "dividends_yield_current > 5 AND debt_to_equity < 1"),
    # 2026-09-10 · 「、」不在分句符里时前一个条件被静默丢掉
    ("毛利率大于40%、营收同比大于20%",
     "gross_margin_ttm > 40 AND total_revenue_yoy_growth_ttm > 20"),
    # ── 英文 ──────────────────────────────────────────────
    # 2026-09-10 · "be(low)" 曾命中字段 low
    ("price above 20", "close > 20"),
    ("RSI below 30 and price above 10", "RSI < 30 AND close > 10"),
    # ── 字段对字段 ───────────────────────────────────────
    # 2026-09-10 · 曾产出 SMA20 > 50(把右边的均线当成了阈值)
    ("20日均线大于50日均线", "SMA20 > SMA50"),
    # 2026-09-10 · 用户报:曾被「站上」模板吞掉,产出 close > SMA50
    ("50日均线高于150日均线。", "SMA50 > SMA150"),
    ("收盘价高于50日均线", "close > SMA50"),
    ("股价低于200日均线", "close < SMA200"),
    ("20日均线低于60日均线", "SMA20 < SMA60"),
    ("50日EMA高于200日EMA", "EMA50 > EMA200"),
    # ── 行话 ──────────────────────────────────────────────
    ("收盘价站上50日均线", "close > SMA50"),
    ("股价跌破200日均线", "close < SMA200"),
    ("均线多头排列", "SMA20 > SMA50 and SMA50 > SMA200"),
    # 2026-09-10 · 曾把「52周」的 52 当成百分比
    ("距离52周最高不到10%",
     "(price_52_week_high - close) / price_52_week_high <= 0.1"),
    # 2026-09-11 · 写成 > 永远 0 只
    ("股价突破52周新高", "close >= price_52_week_high"),
    ("市盈率低于15，净资产收益率大于15%，而且股价站上200日均线",
     "price_earnings_ttm < 15 AND return_on_equity > 15 AND close > SMA200"),
    # ── 2026-09-11 · 数字在后的 TA 写法(用户报 EMA20大于EMA50 识别不了)──
    ("EMA20大于EMA50", "EMA20 > EMA50"),
    ("MA20大于MA50", "SMA20 > SMA50"),
    ("SMA50>SMA200", "SMA50 > SMA200"),
    ("ema20 > ema50", "EMA20 > EMA50"),
    ("EMA 20 大于 EMA 50", "EMA20 > EMA50"),
    ("MA(20)大于MA(60)", "SMA20 > SMA60"),
    ("站上MA20", "close > SMA20"),
    ("ma5>ma10且ma10>ma20", "SMA5 > SMA10 AND SMA10 > SMA20"),
    # 曾产出 close > 20 —— MA20 里的 20 被当成了阈值
    ("收盘价大于MA20", "close > SMA20"),
    # ── 2026-09-11 · 全角(中文输入法)──────────────────────
    ("收盘价＞２０", "close > 20"),
    ("ＲＳＩ小于３０", "RSI < 30"),
    ("市盈率＜１５", "price_earnings_ttm < 15"),
    # ── 2026-09-11 · 两条均线的上穿/下穿(曾产出 close > SMA5)────
    ("MA5上穿MA10", "SMA5 > SMA10"),
    ("5日均线上穿20日均线", "SMA5 > SMA20"),
    ("20日均线下穿60日均线", "SMA20 < SMA60"),
    ("5日线金叉10日线", "SMA5 > SMA10"),
    # ── 2026-09-11 · 常用指标名 ───────────────────────────
    ("MACD大于0", "MACD.macd > 0"),
    ("MACD柱大于0", "MACD.hist > 0"),
    ("DIF大于DEA", "MACD.macd > MACD.signal"),
    ("K值小于20", "Stoch.K < 20"),
    ("收盘价大于布林上轨", "close > BB.upper"),
    ("收盘价低于布林下轨", "close < BB.lower"),
    ("收盘价大于VWAP", "close > VWAP"),
    # ── 2026-09-11 · 中文数字 ─────────────────────────────
    ("收盘价站上五日均线", "close > SMA5"),
    ("二十日均线大于六十日均线", "SMA20 > SMA60"),
    # ── 2026-09-11 · 「A比B高」句式 ───────────────────────
    ("收盘价比50日均线高", "close > SMA50"),
    ("20日均线比60日均线低", "SMA20 < SMA60"),
    # ── 2026-09-11 · RS 相对强度评级 ──────────────────────
    ("RS大于80", "rs_rating > 80"),
    ("RS评级不低于90", "rs_rating >= 90"),
    ("相对强度评级大于85", "rs_rating > 85"),
    ("RS大于80，收盘价站上50日均线", "rs_rating > 80 AND close > SMA50"),
    # rs 与 rsi 不能互相吃掉
    ("RSI小于30", "RSI < 30"),
    ("RS大于80且RSI小于70", "rs_rating > 80 AND RSI < 70"),
    # 中文里 RSI 就叫「相对强弱指数」—— 不能被映射成 RS 评级
    ("相对强弱指数小于30", "RSI < 30"),
    ("相对强度指数大于70", "RSI > 70"),
    # ── 2026-09-11 · 带否定的比较符(曾命中里面的「少于」「超过」,意思整个反过来)──
    ("成交量不少于100万", "volume >= 1000000"),
    ("市盈率未超过20", "price_earnings_ttm <= 20"),
    ("市盈率没有超过20", "price_earnings_ttm <= 20"),
    ("市值不多于100亿", "market_cap_basic <= 10000000000"),
    # ── 2026-09-11 · RS 线上涨天数(用户原话:「RS线上涨时间大于50天」)──
    # 走通用规则会错两次:开头的 rs 被认成 RS 评级 → rs_rating > 50;
    # 「50天」的「天」又会触发"标识符里的数字不当阈值"的保护 → 整句拒绝
    ("RS线上涨时间大于50天", "rs_line_up_days > 50"),
    ("RS线上涨天数不少于60天", "rs_line_up_days >= 60"),
    ("RS线连续上涨超过50个交易日", "rs_line_up_days > 50"),
    ("RS线向上超过五十天", "rs_line_up_days > 50"),
    ("rs线上涨天数>=30", "rs_line_up_days >= 30"),
    ("RS线站上21日均线超过50天", "rs_line_up_days > 50"),   # 均线里的 21 不是阈值
    ("RS线上涨50天以上", "rs_line_up_days >= 50"),
    ("RS line up more than 50 days", "rs_line_up_days > 50"),
    ("相对强度线上涨时间大于40天", "rs_line_up_days > 40"),
    ("RS大于80，RS线上涨时间大于50天", "rs_rating > 80 AND rs_line_up_days > 50"),
]

SHOULD_REJECT = [
    # 无关 / 看不懂
    "帮我推荐几只好股票",
    "今天天气不错",
    "找那些最近很强势的票",
    "成交量大于100万，并且老板人品好",      # 全中或全不中,不做部分识别
    # 周期不存在
    "37日均线大于50日均线",
    "RSI6小于20",
    "RSI(6) 小于 25",
    # 词表里没有的指标 —— 数字绝不能被当成阈值
    "CCI20大于100",
    # 单位不同(2026-09-11 · 曾产出 volume > SMA50)
    # 「成交量大于50日均线」2026-09-14 起按 50 日均量识别(移到 SHOULD_MATCH);只认「N日均线」,别的均线写法不猜
    "市盈率大于20日均线",
    "成交量大于50日EMA",
    "成交量大于MA50",
    "成交量大于50日均线的1.5倍",         # 倍数照样拒绝,不能丢掉 1.5
    "300日均量大于100万",               # 超过自家日线上限 250 天,不拿短窗口冒充
    "1日均量大于100万",
    # 本地处理不了的句型 —— 不拦就会**丢掉一半意思**
    "收盘价大于20日均线的1.05倍",        # 曾产出 close > SMA20
    "成交量是30日均量的2倍以上",
    # 「市盈率在10到20之间」「市盈率大于10小于20」2026-09-14 起本地识别(移到 SHOULD_MATCH);下面是区间写法里仍要拒绝的
    "收盘价大于20小于10",               # 空区间:写错了,不替用户猜
    "收盘价大于10大于20",               # 同向两个比较
    "收盘价大于10或小于5",              # 「或」不是区间
    "收盘价等于10小于20",
    "成交量在10到20万之间",             # 单位不一致:可能是 10万~20万,不猜
    "净资产收益率在10%到20之间",
    "10到20之间的股价",                 # 数字在字段前面
    "股价在10到20到30之间",
    "股价在10到10之间",
    # 光秃秃的「相对强度/相对强弱」有歧义(RS 评级 还是 RSI?)—— 不猜
    "相对强度大于80",
    "相对强弱小于30",
    # RS 线:认不全就拒绝,**绝不能**落到通用规则变成 rs_rating > N
    "RS线向上",                         # 没有天数
    "RS线大于50天",                     # 没说向上还是向下
    "RS线下跌超过20天",                 # 只有上涨天数这个字段
    "RS线站上50日均线超过30天",          # 口径定死 21 日均线
    "RS线上涨超过10周",                 # 周 ≠ 5 个交易日(节假日),不换算
    "RS线大于80",
]


# ── 2026-09-11 · 直接写字段原名 ──────────────────────────────────────
# 用户报「rs_line_up_days大于50天」本地识别不出来。查下来是一整类:
#   1. 词表只收了中文说法,**字段原名**(3777 个里没被收录的)一律不认
#   2. 英文词的词边界没把 `_` `.` 算进去 —— 「rs_line_up_days」开头的 rs
#      被认成了 RS 评级。只是碰巧被下面第 3 条挡住,否则会静默产出 rs_rating > 50
#   3. 「数字后跟 天/日」被一刀切拒掉(本意是挡「50日均线」),
#      但「大于50天」结尾的天是阈值单位
SHOULD_MATCH += [
    ("rs_line_up_days大于50天", "rs_line_up_days > 50"),          # ← 用户原话
    ("rs_line_up_days > 50", "rs_line_up_days > 50"),
    ("rs_line_up_days>=50", "rs_line_up_days >= 50"),
    ("rs_line_up_days 大于 50 个交易日", "rs_line_up_days > 50"),
    ("rs_line_up_days不低于30天", "rs_line_up_days >= 30"),
    ("rs_line_up_days大于50天以上", "rs_line_up_days > 50"),
    ("RS_LINE_UP_DAYS大于50天", "rs_line_up_days > 50"),          # 大小写不敏感
    ("rs_rating >= 80", "rs_rating >= 80"),
    ("market_cap_basic大于100亿", "market_cap_basic > 10000000000"),
    ("price_earnings_ttm小于15", "price_earnings_ttm < 15"),
    ("return_on_equity > 15", "return_on_equity > 15"),
    ("Perf.Y大于20%", "Perf.Y > 20"),
    ("perf.y大于20%", "Perf.Y > 20"),                               # 带点的也不分大小写
    ("MACD.hist大于0", "MACD.hist > 0"),
    ("average_volume_90d_calc大于1000000", "average_volume_90d_calc > 1000000"),
    ("relative_volume_10d_calc大于2", "relative_volume_10d_calc > 2"),
    ("close > SMA50", "close > SMA50"),
    ("EMA20 > EMA50", "EMA20 > EMA50"),
    ("rs_line_up_days大于50天，市盈率小于20",
     "rs_line_up_days > 50 AND price_earnings_ttm < 20"),
]
SHOULD_REJECT += [
    # 不认识的标识符里藏着认识的词 —— 绝不能拿里面那个词去猜
    "rs_score大于50",                  # 不能变成 rs_rating > 50
    "close_price大于20",               # 不能变成 close > 20
    "volume_ratio大于2",               # 不能变成 volume > 2
    "ema20_slope大于0",                # 不能变成 EMA20 > 0
    "rs_line_up_day大于50天",          # 拼错一个字母:报不认识,别猜
    "Perf.Z大于20",
    "foo_bar大于5",
    # 「天」单位只对「天数」类字段成立
    "收盘价大于50天",                  # 价格字段后面跟天,说不通
    "rs_line_up_days大于50日均线",     # 天数去比均线
    "rs_line_up_days大于50周",         # 周不按 5 天换算
    "rs_line_up_days大于50%",          # 天数字段带百分号
    "rs_line_up_days大于5万",          # 天数字段带万
    "rs_line_up_days大于50天小于100天",  # 区间,本地不拆
]


# ── 2026-09-11 · VCP 字段(每晚日线算出,见 services/quant/vcp.py)────────
SHOULD_MATCH += [
    ("收缩次数大于等于3", "vcp_contractions >= 3"),
    ("VCP收缩次数不少于3次", "vcp_contractions >= 3"),
    ("最后一次收缩深度小于8%", "vcp_last_depth < 8"),
    ("首次收缩深度不超过35%", "vcp_first_depth <= 35"),
    ("收缩量比小于0.7", "vcp_last_vol_ratio < 0.7"),       # 不能被里面的「量比」截胡
    ("距枢轴不到5%", "vcp_pivot_dist < 5"),
    ("底部天数大于30天", "vcp_base_days > 30"),
    ("量能逐次递减", "vcp_vol_declining == 1"),
    ("收缩次数大于等于3，量能递减，最后一次收缩深度小于8%",
     "vcp_contractions >= 3 AND vcp_vol_declining == 1 AND vcp_last_depth < 8"),
    ("vcp_contractions >= 3", "vcp_contractions >= 3"),
    # 「不到」曾被区间规则误拒(`到\s*\d` 把「不到5」当成了「10到20」)
    ("市盈率不到15", "price_earnings_ttm < 15"),
    ("股价不到20", "close < 20"),
    ("涨跌幅不到3%", "change < 3"),
]
SHOULD_REJECT += [
    "收缩深度小于10%",          # 第一次还是最后一次?差好几倍,不猜
    "量能没有递减",             # 否定句本地不拆
    "成交量逐日递减",           # 近几天缩量 ≠ 每次收缩量能递减
    "VCP形态",                 # 太笼统
    "收缩次数大于3周",          # 单位对不上
    # 「市盈率10%到20%」「市盈率从10到20」原来在这里(区间一律拒绝时,不带「之间」也要认出来);
    # 2026-09-14 起区间本地识别,移到下面 SHOULD_MATCH
]
SHOULD_MATCH += [
    ("市盈率10%到20%", "price_earnings_ttm >= 10 and price_earnings_ttm <= 20"),
    ("市盈率从10到20", "price_earnings_ttm >= 10 and price_earnings_ttm <= 20"),
    # ── 2026-09-14 · 用「且」等连接的双边比较(后半句省略了主语)· 按连接词类别 ─────────
    ("收盘价不低于10且不高于20", "close >= 10 and close <= 20"),
    ("股价大于10并且小于20", "close > 10 and close < 20"),
    ("市盈率大于10而且小于20", "price_earnings_ttm > 10 and price_earnings_ttm < 20"),
    ("收盘价大于10，小于20", "close > 10 and close < 20"),
    ("股价小于20且大于10", "close < 20 and close > 10"),
    ("成交量大于100万且小于200万", "volume > 1000000 and volume < 2000000"),
    ("收盘价大于10元且小于20元", "close > 10 and close < 20"),
    ("price above 10 and below 20", "close > 10 and close < 20"),
    ("收盘价大于10且小于20，市盈率低于15", "close > 10 and close < 20 AND price_earnings_ttm < 15"),
    ("市盈率低于15，收盘价大于10且小于20", "price_earnings_ttm < 15 AND close > 10 and close < 20"),
    ("收盘价大于10且市盈率小于20", "close > 10 AND price_earnings_ttm < 20"),   # 后半句有字段:照旧两个条件
]
SHOULD_REJECT += [
    "收盘价大于20且小于10",             # 空区间
    "收盘价大于10且大于20",             # 同向
    "成交量大于10且小于20万",           # 单位不一致
    "收盘价大于10且小于",               # 后半句没有数字
    "收盘价大于50日均线且小于20",       # 前半句是两字段比较,「小于20」不知道比谁
    "RS线连涨超过50天且少于100",        # 天数类不拼
    "收盘价大于10且小于20且大于15",     # 三个比较不是区间
    "price above 10 billion and below 20",   # 英文整词单位在区间里不猜
]


# ── 2026-09-11 第二批 · 最低点量比 / 近 20 日涨跌天数 / 涨跌日均量比 ─────────
SHOULD_MATCH += [
    ("最低点量比小于0.6", "vcp_low_vol_ratio < 0.6"),       # 不能被里面的「量比」截胡
    ("低点量比不超过0.5", "vcp_low_vol_ratio <= 0.5"),
    ("近20日涨跌日均量比大于1", "ud_vol_ratio_20d > 1"),
    ("涨跌日均量比大于1.2", "ud_vol_ratio_20d > 1.2"),
    ("20日上涨天数大于12", "up_days_20d > 12"),              # 字段名里的 20 不能当阈值
    ("近20日下跌天数小于8天", "down_days_20d < 8"),
    ("近20日上涨天数大于近20日下跌天数", "up_days_20d > down_days_20d"),
    ("最低点量比小于最后一次收缩量比", "vcp_low_vol_ratio < vcp_last_vol_ratio"),  # 同一个分母,能比
    ("量比大于2", "relative_volume_10d_calc > 2"),          # 拦「涨跌量比」不能误伤裸「量比」
    ("今日量比大于2", "relative_volume_10d_calc > 2"),
]
SHOULD_REJECT += [
    "上涨天数大于下跌天数",      # 不带窗口:和「RS线上涨天数」撞,也不知道几天
    "涨跌量比大于1",            # IBD 口径(总量比)和我们的日均量比不是一个数,不能被「量比」截胡
    "最低点成交量很小",          # 没有数字
    "多空量比大于1",            # 同类:前面接了修饰词,「量比」的意思就变了
    "买卖量比大于2",
    "日均量比大于1",
    "最低点量比小于今日量比",    # 分母不同,不能直接比
]


# ── 2026-09-11 第三批 · 低点抬高 / 精确交易日窗口字段 ─────────────────
SHOULD_MATCH += [
    ("low_21d大于low_63d", "low_21d > low_63d"),                 # 字段原名直接认,里面的 low 不能被截走
    ("high_5d小于high_21d", "high_5d < high_21d"),
]
SHOULD_REJECT += [
    "低点抬高",                  # 没有对应字段(收缩次数 ≥2 已保证),不能随便映射到哪个价格字段
    "1月低点比3月低点抬高2%",    # 两个窗口的最低价之比,本地不拆
    "low_21d大于low_63d的1.02倍",  # 倍数,本地不拆
]


# ── 2026-09-17 · 「可用字段」里点出来的字段,生成时报「没看懂」 ─────────────────
# 用户:点字段插进生成框、点生成,说不认识 —— 自己的产品不认识自己的库。全量探针(美股 3809 个字段名)按写法类别查出三类:
#   1. 只写字段名、没写怎么比(列表里 1096 个字段全中):报错必须点明「缺比较」,且不给 AI(阈值 AI 补就是编)
#   2. 列表显示的中文名打进来认不出(576 个里 380 个):中文名收进词汇,同名多字段 / 两字短名单独收紧
#   3. 带 - + 或数字开头的字段名列出来却写不进脚本(27 个):从列表里拿掉(screen_source.listable_fields,不在本文件测)
SHOULD_MATCH += [
    ("K线·三只乌鸦大于0", "Candle.3BlackCrows > 0"),
    ("布林带中轨(50)大于100", "BB.basis_50 > 100"),            # 名字里的 50 不能当阈值
    ("现金股息保障倍数(TTM)大于2", "cash_dividend_coverage_ratio_ttm > 2"),   # 名字里的「倍数」不是倍数句型
    ("跳空大于1", "gap > 1"),                                # 两字短名:句首 + 紧跟比较词才认
    ("Candle.3BlackCrows > 0", "Candle.3BlackCrows > 0"),
]
SHOULD_REJECT += [
    "向上跳空大于1",                    # 两字短名不在句首:可能是复合词的一部分
    "跳空率大于1",                      # 两字短名后面没紧跟比较词
    "历史最低大于0",                    # Low.All 和 all_time_low 同名,拿不准指哪个
    "现金股息保障倍数(TTM)大于收盘价的2倍",   # 字段名以外出现倍数,照样拒绝
]
SHOULD_MATCH += [
    # 双边比较(09-14 第 5 轮支持)配中文名:名字里的 50 不能被数成第三个数把区间拆坏
    ("布林带中轨(50)大于10小于20", "BB.basis_50 > 10 and BB.basis_50 < 20"),
]
# 只写了字段名 / 中文名、没有比较 → 必须是 MissingComparison(路由据此不给 AI 按钮),报错里点名字段
SHOULD_MISS_CMP = [
    ("MACD.hist", "MACD.hist"),
    ("macd.hist", "MACD.hist"),                # 大小写不敏感,报错给规范写法
    ("close", "close"),
    ("K线·三只乌鸦", "Candle.3BlackCrows"),
    ("跳空", "gap"),
    ("市盈率", "price_earnings_ttm"),
]


# ── 2026-09-18 · 成交额(金额)被当成成交量、区间涨幅被当成当日涨跌幅 ─────────────────
# 用户在本地 docker(美股)实测「股价站上50日均线，成交额大于2000万，近一个月涨幅超过10%」:
#   成交额 → volume > 20000000(金额当股数)、近一个月涨幅 → change > 10(区间当当日)。按写法类别补:
#   成交额:当日 / 成交金额 / 成交额度 / N日均 / 日均没说几天 / N日合计还是日均说不清 / 周月单位
#   区间涨幅:近N日 / 近N周 / 近N个月 / 近N年 / 中文数字 / 年内 / 今年以来 / 上市以来 / 当日;对不上字段的拒绝
#   跌幅:方向相反(曾产出 change > 10 当成「跌幅超过10%」)
SHOULD_MATCH += [
    ("股价站上50日均线，成交额大于2000万，近一个月涨幅超过10%",          # ← 用户原话
     "close > SMA50 AND close * volume > 20000000 AND Perf.1M > 10"),
    # 成交额类
    ("成交额大于2000万", "close * volume > 20000000"),
    ("成交金额大于1亿", "close * volume > 100000000"),
    ("成交额度不低于5000万", "close * volume >= 50000000"),
    ("今日成交额大于2000万", "close * volume > 20000000"),
    ("30日均成交额大于2000万", "AvgValue.Traded_30d > 20000000"),
    ("近10日日均成交额大于1亿", "AvgValue.Traded_10d > 100000000"),
    ("90天平均成交额不低于500万", "AvgValue.Traded_90d >= 5000000"),
    ("六十日均成交额大于1亿", "AvgValue.Traded_60d > 100000000"),
    ("AvgValue.Traded_30d大于2000万", "AvgValue.Traded_30d > 20000000"),
    ("成交额在1000万到5000万之间", "close * volume >= 10000000 and close * volume <= 50000000"),
    ("成交额大于30日均成交额", "close * volume > AvgValue.Traded_30d"),
    ("成交量大于100万，成交额大于2000万", "volume > 1000000 AND close * volume > 20000000"),
    # 区间涨幅类
    ("近一个月涨幅超过10%", "Perf.1M > 10"),
    ("近1月涨幅超过10%", "Perf.1M > 10"),
    ("近1个月涨幅大于10%", "Perf.1M > 10"),
    ("一个月内涨幅超过10%", "Perf.1M > 10"),
    ("最近一个月的涨幅大于10%", "Perf.1M > 10"),
    ("过去三个月涨幅大于30%", "Perf.3M > 30"),
    ("近3月涨幅大于30%", "Perf.3M > 30"),
    ("近半年涨幅大于50%", "Perf.6M > 50"),
    ("近6个月涨幅大于50%", "Perf.6M > 50"),
    ("近12个月涨幅大于50%", "Perf.Y > 50"),
    ("近一年涨幅大于100%", "Perf.Y > 100"),
    ("近3年涨幅大于100%", "Perf.3Y > 100"),
    ("近一周涨幅大于5%", "Perf.W > 5"),
    ("近1个星期涨幅大于5%", "Perf.W > 5"),
    ("近5日涨幅大于5%", "Perf.5D > 5"),
    ("近五个交易日涨幅大于5%", "Perf.5D > 5"),
    ("年内涨幅大于20%", "Perf.YTD > 20"),
    ("今年以来涨幅大于20%", "Perf.YTD > 20"),
    ("年初至今涨幅大于20%", "Perf.YTD > 20"),
    ("上市以来涨幅大于100%", "Perf.All > 100"),
    ("今日涨幅大于5%", "change > 5"),
    ("当日涨跌幅超过4%", "change > 4"),
    ("涨幅大于5%", "change > 5"),                                    # 不写区间照旧是当日
    ("近一个月涨幅在10%到30%之间", "Perf.1M >= 10 and Perf.1M <= 30"),
    ("近1个月涨幅大于近3个月涨幅", "Perf.1M > Perf.3M"),
    # 跌幅:方向相反
    ("跌幅超过5%", "change < -5"),
    ("近一个月跌幅超过10%", "Perf.1M < -10"),
    ("跌幅不超过3%", "change >= -3"),
    ("近1个月涨跌幅大于-10%", "Perf.1M > -10"),                       # 涨跌幅本来就带符号,不翻转
]
SHOULD_REJECT += [
    # 成交额:说不清几天 / 合计还是日均 / 扫描源没有的周期 —— 不能退回成交量,也不拿别的周期冒充
    "日均成交额大于2000万",
    "平均成交额大于2000万",
    "20日均成交额大于2000万",
    "近20日日均成交额大于2000万",
    "20日成交额大于1亿",                   # 合计还是日均?
    "近一个月日均成交额大于2000万",
    "近一周成交额大于1亿",
    "成交额大于50日均线",                   # 金额对价格
    "成交额大于成交量",                     # 金额对股数
    "成交额大于30日均量",
    "换手率大于5%",                         # 曾映射成量比
    # 区间涨幅:扫描源没有对应字段的一律拒绝,**绝不退回当日涨跌幅**
    "近20日涨幅超过10%",
    "近二十个交易日涨幅超过10%",
    "近30天涨幅超过10%",
    "近两周涨幅大于5%",
    "近2个月涨幅大于10%",
    "近半个月涨幅大于5%",
    "近2年涨幅大于50%",
    "本月涨幅大于10%",
    "上周涨幅大于5%",
    "涨幅近一个月超过10%",                  # 区间写在后面
    "最近涨幅超过10%",                      # 没说多久
    "过去一段时间涨幅大于10%",
    "近期涨幅大于10%",
    # 跌幅:翻转说不清的句型
    "跌幅在5%到10%之间",
    "跌幅超过-5%",
    "近一个月跌幅大于近三个月跌幅",
]


# ── 2026-09-18(第 8 轮)· 字段名里的比较词被读成比较 ─────────────────────────────
# 全量字段探针查出两条既有静默错(与第 7 轮无关,新旧版本结果一致):
#   「recommendation_under > 0」→ recommendation_under < 0:英文比较词 under 从字段原名内部被读出来(_find_op 裸 find、无词边界);
#   「盘前变动(绝对值)大于0」→ change_abs > 0:同名多字段中文名按设计不收,里面的「变动(绝对值)」(change_abs)却被截了出来。
# 按类别补:字段原名里含 under / over 的写法(符号 / 中文 / 英文比较词 / 大写),
#   普通英文单词里的比较词(overbought / oversold / undervalued / overshoot —— oversold 30 曾产出 RSI > 30,意思整个反了),
#   同名多字段中文名的各种写法(必须报点名错误,不能让子串命中别的字段)
FIELDS |= {"recommendation_over", "recommendation_under", "pre_change_abs", "premarket_change_abs", "change_abs"}
SHOULD_MATCH += [
    ("recommendation_under > 0", "recommendation_under > 0"),           # ← 探针原句
    ("recommendation_under大于0", "recommendation_under > 0"),           # ← 探针原句
    ("RECOMMENDATION_UNDER >= 3", "recommendation_under >= 3"),
    ("recommendation_under不低于1", "recommendation_under >= 1"),
    ("recommendation_under above 2", "recommendation_under > 2"),
    ("recommendation_over小于5", "recommendation_over < 5"),             # 旧版 over 抢在「小于」前面 → > 5
    ("recommendation_over below 5", "recommendation_over < 5"),
    ("变动(绝对值)大于0", "change_abs > 0"),                             # 唯一的中文名照旧认
    # 独立的英文比较词照旧认
    ("close under 10", "close < 10"),
    ("RSI over 70", "RSI > 70"),
]
SHOULD_REJECT += [
    "盘前变动(绝对值)大于0",                 # ← 探针原句:pre_change_abs / premarket_change_abs 同名
    "盘前变动(绝对值)小于-1",
    "盘前变动(绝对值) > 0",
    "收盘价大于10，盘前变动(绝对值)大于0",    # 全中或全不中
    # 普通英文单词里的比较词不是比较
    "RSI overbought 70",
    "RSI oversold 30",                      # 旧版 → RSI > 30(超卖读成大于)
    "市盈率undervalued 10",
    "close overshoot 10",
]
# 同名多字段中文名:必须报「拿不准指哪个」且点名全部字段,不能退成「没看懂」或命中别的字段
SHOULD_AMBIG = [
    ("盘前变动(绝对值)大于0", ("pre_change_abs", "premarket_change_abs")),
    ("盘前变动(绝对值) > 0", ("pre_change_abs", "premarket_change_abs")),
    ("历史最低大于0", ("Low.All", "all_time_low")),
]


def _run(sd, kw) -> list[str]:
    has = lambda n: n in FIELDS                          # noqa: E731
    fails: list[str] = []
    for text, want in SHOULD_MATCH:
        try:
            r = kw.translate(text, has, SMA, EMA, RSI, names=FIELDS)
            got = " AND ".join(m["expr"] for m in r["matched"])
        except sd.ScreenError as e:
            got = f"(拒绝: {e})"
        if got != want:
            fails.append(f"应识别  {text!r}\n        期望 {want}\n        实得 {got}")
    for text in SHOULD_REJECT:
        try:
            r = kw.translate(text, has, SMA, EMA, RSI, names=FIELDS)
            got = " AND ".join(m["expr"] for m in r["matched"])
            fails.append(f"应拒绝  {text!r}\n        却产出 {got}   ← 静默错误")
        except sd.ScreenError:
            pass
    for text, fld in SHOULD_MISS_CMP:
        try:
            r = kw.translate(text, has, SMA, EMA, RSI, names=FIELDS)
            fails.append(f"应报缺比较  {text!r}\n        却产出 {r['script']}")
        except kw.MissingComparison as e:
            if fld not in str(e):
                fails.append(f"应报缺比较  {text!r}\n        报错里没点名字段 {fld}:{e}")
        except sd.ScreenError as e:
            fails.append(f"应报缺比较  {text!r}\n        报的却是普通错误(会给 AI 按钮):{e}")
    for text, flds in SHOULD_AMBIG:
        try:
            r = kw.translate(text, has, SMA, EMA, RSI, names=FIELDS)
            fails.append(f"应报同名多字段  {text!r}\n        却产出 {r['script']}   ← 静默错误")
        except sd.ScreenError as e:
            if not all(f in str(e) for f in flds):
                fails.append(f"应报同名多字段  {text!r}\n        报错里没点名 {' / '.join(flds)}:{e}")
    return fails


def test_screen_kw():
    sd, kw = _load()
    fails = _run(sd, kw)
    assert not fails, "\n" + "\n".join(fails)


if __name__ == "__main__":
    sd, kw = _load()
    fails = _run(sd, kw)
    total = len(SHOULD_MATCH) + len(SHOULD_REJECT) + len(SHOULD_MISS_CMP) + len(SHOULD_AMBIG)
    print(f"应识别 {len(SHOULD_MATCH)} 条 · 应拒绝 {len(SHOULD_REJECT)} 条 · 共 {total}")
    if fails:
        print(f"FAIL {len(fails)} 条:")
        for f in fails:
            print("  " + f)
        sys.exit(1)
    print("ALL OK")
