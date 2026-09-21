"""小鹿智能体 · 涨停 + 三根阴线(A 股主板 · 2026-09-18 用户在「涨停后强势整理」线上新开的迭代方向)。

用户原话:「再修改一下条件,新开个迭代方向:涨停 + 三根阴线,即涨停后连续三天阴线后买入,就隔日卖,
以隔日收盘价为收益率,且只做主板票」。原方向 `limitup`(v4)不动,两个方向放在同一条研究线里对比。

## 规则(Y-xx,卖出 / 仓位 / 手续费沿用 L-04 ~ L-06)

- Y-01 涨停:T-3 收盘较 T-4 收盘涨幅达到主板涨停(≥ 9.8%,上限 11.2% 挡日线错误与新股无涨跌幅限制期);
       主板 ST 2025-07-07 前按 5%(与原方向同一个 limit_of)
- Y-02 三根阴线:T-2、T-1、T 三天每天**收盘 < 开盘**(严格小于,平盘十字星不算阴线)。开盘价缺失 → 算不出、不买。
       「阴线」按 K 线实体定义,不是「收跌」—— 高开低走但仍比前一天涨的也算阴线。这是替用户定的口径
- Y-03 只做主板:代码 60 / 00 开头
- 信号当天收盘买 1 万元(取整股),次日收盘全部卖出,收益率按次日收盘价;次日收盘跌停或停牌顺延(与原方向同一套)

没有「三天没再涨停」「收盘守住涨停价」这些原方向的条件:阴线的收盘低于开盘,而开盘不会超过涨停价,
所以三天里不可能再收涨停;守不守涨停价用户没要求。

## 数据

- 日线要开盘价:本模块声明 WITH_OPEN,`agent_run.Ctx` 为 A 股装载带开盘价的 6 元组(`screen_asof.bars_upto(with_open=True)`)。
  A 股 `rs_daily.open` 一年内缺失 229 行 / 142 万行,基本齐全。
- 候选池 = 筛选器时间回溯跑 POOL_SCRIPT(T-3 涨幅 ≥ 9.8% 且三天收阴,板块不分);**主板由引擎按代码判**。
- 立项前用原始日线粗算(2026-09-18):2025-09-17 ~ 2026-09-17 主板 1,976 个信号,次日收盘扣费后平均 -0.46%(t -4.76),
  上下半年都为负;2025-06 ~ 09 检验段 520 个信号 +0.14%。
"""
from __future__ import annotations

from app.services.quant import agent_limitup as lu

# 市场 / 货币 / 本金 / 护栏说明与原方向相同
MARKET = lu.MARKET
CURRENCY, CURRENCY_SYMBOL, CURRENCY_UNIT = lu.CURRENCY, lu.CURRENCY_SYMBOL, lu.CURRENCY_UNIT
BENCH_LABEL = lu.BENCH_LABEL
INITIAL_CAPITAL = lu.INITIAL_CAPITAL
NO_GUARDS = True
WITH_OPEN = True
EXEC_NOTE = lu.EXEC_NOTE
REBALANCE_SUFFIX = lu.REBALANCE_SUFFIX

PARAMS = dict(lu.PARAMS, mode="yin", growth_only=False, amp_max=None, no_all_shrink=False)
STOP_KEYS: tuple = ()
MIN_BARS = lu.MIN_BARS
ENTRY_RULE = lu.ENTRY_RULE
WATCH_POOL_DAYS = 1

POOL = "limitup_yin"
POOL_LIMIT = 500
POOL_LABEL = "涨停三阴预筛池(T-3 涨幅 ≥ 9.8% · 之后三天每天收盘低于开盘;主板由引擎按代码判)"
POOL_SCRIPT = """# ===== 涨停 + 三根阴线 · 预筛(小鹿 · A 股研究线)=====
# 脚本里分不出板块,这里不分;只做主板由引擎按代码判
def up_t3 = (close[3] - close[4]) / close[4] >= 0.098;
def yin3  = close[2] < open[2] and close[1] < open[1] and close < open;

plot scan = up_t3 and yin3;
"""

RULE_NAME = {"L-01": "涨停三阴买入", "L-05": "次日收盘卖出"}
RULE_PARAM_KEY: dict = {}


def rules_for(p: dict = PARAMS) -> list[dict]:
    return lu.rules_for(p)


RULES = rules_for(PARAMS)


def summary(p: dict = PARAMS) -> str:
    return lu.summary(p)


def indicators(bars, p: dict = PARAMS, bench=None):
    return lu.indicators(bars, p, bench)


def entry_checks(ind, code, name, p: dict = PARAMS):
    return lu.entry_checks(ind, code, name, p)


def watch_item(code, name, ind, held, blocked_reason, score=None, p: dict = PARAMS):
    return lu.watch_item(code, name, ind, held, blocked_reason, score, p)


def run_day(date_iso, positions, cash, bars_of, watch, prev_equity, consec_losses, p: dict = PARAMS, g=None,
            ind_of=None, want_text: bool = True):
    return lu.run_day(date_iso, positions, cash, bars_of, watch, prev_equity, consec_losses, p,
                      g if g is not None else lu.av.GUARDS, ind_of, want_text)
