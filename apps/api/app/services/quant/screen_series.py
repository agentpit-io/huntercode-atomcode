"""筛选脚本 · 时间序列引擎 —— 让标准的 ThinkScript 选股脚本能在本地跑起来(2026-09-15)。

## 为什么要有它

`screen_dsl` 原来只有**横截面**求值:每只票一行快照,`Average(close, 50)` 直接映射成扫描源的
SMA50 字段。这对「字段 op 常量」的筛选够用,但一份标准的 ThinkScript 选股脚本长这样:

    input lookback = 5;
    def isBull = close > open;
    def bullStreak = if isBull then bullStreak[1] + 1 else 0;      # 递归:连续阳线数
    def bearCount  = Sum(isBear, lookback);                         # 滚动窗口
    def cumRet     = close / close[lookback] - 1;                   # K 线偏移
    def volMA20    = Average(volume[1], 20);                        # 对偏移后的序列做均线
    plot scan = bullStreak >= 3 and bearCount <= 1 and ...;

`[1]`、`if … then … else`、对自己引用的 def、对任意表达式做 Sum / Highest ——
没有一样能靠一行快照算出来。2026-09-15 用户粘了这份脚本,界面报「第 18 行:看不懂的字符 '['」,
用户以为是自己打错了字,AI 修错(只做局部替换)也修不了。**这不是一句话的 bug,是整类写法没支持。**

## 它怎么算

- 数据 = 自家全市场日线 `rs_daily`(和「时间回溯」`screen_asof` 同一份、同一套拆股修正、同一个 RS 排名池),
  美股 / A 股 / 港股各约 320~600 根。**不是扫描源快照** —— 快照没有历史。
- 把整个市场摆成一个 **股票 × K 线** 的二维数组(右对齐到求值日,历史不够的在左边补 NaN),
  每个 def 就是一个二维数组,所有运算用 numpy 向量化:`x[n]` = 向右平移 n 列,
  `Sum(x, n)` = 滑动窗口求和,`if` = np.where。4000 只 × 250 根,一份 20 句的脚本几百毫秒。
- **递归定义**(`bullStreak = if isBull then bullStreak[1] + 1 else 0`)没法整列向量化,
  按 K 线逐根循环、每根对全市场向量化:250 根循环 × 4000 只,同样是亚秒级。
  递归定义里对自己的引用只能是 `名字[k]`(k ≥ 1)且不能放在 Sum / Highest 这类窗口函数里
  —— 这是 ThinkScript 自己的约束,我们照样。
- 最终看 plot 在**最后一根**的值:非 0 = 命中,0 = 不满足,NaN = 算不出(计入「算不出」,不算不满足)。
  NaN 的语义与横截面引擎一致:三值逻辑(假 且 未知 = 假;真 或 未知 = 真),比较里有 NaN 就是 NaN。
  日线不够长、某根缺高低量、缺开盘价 —— 都是 NaN,**不拿 0 顶替**(仓内铁律:空的比假的好)。

## 边界(写进返回体的 warnings,别只放在这里)

1. 求值日 = 日线最新一天(每晚更新),**不含今天盘中**;时间回溯时 = 回溯日。
2. 股票池 = RS 排名池(美股剔 OTC 与微盘),不是快照的 7400 只。
3. 快照才有的字段(市值 / PE / 财务)可以混用,按**今天的值**当常量;回溯时它们没有历史,整批 NaN。
4. `open` 从 2026-09-15 起入库,老行为空,要等每晚整窗重拉之后才有;
   parse 时会查这个市场的日线里有没有开盘价,没有就在「生成」那一步直接报,不等到扫描。
5. 递归定义按最近 `REC_BARS` 根算,更早的历史不参与(连续计数这类在第一次归零后就与完整历史一致)。
6. ExpAverage / RSI / ATR / MACD 从可用日线的起点递推,要求「周期 + EMA_EXTRA」根,不够给 NaN。

## 支持的写法(用例 tests/test_screen_series.py 逐条盯着)

语句:def / plot / input / rec;`declare …;` 与 AddLabel / AssignPriceColor 等画图语句整句跳过。
运算:+ - * /、比较、and or not(也认 && || !)、if c then a else b(可嵌套)、if(c, a, b)、
     x[n]、a crosses above / below b、cond within n bars、命名参数 f(length = 20) / f("length" = 20)。
序列:open high low close volume、hl2 hlc3 ohlc4。
函数:Average / SimpleMovingAvg / ExpAverage / MovAvgExponential / WildersAverage / MovingAverage(AverageType.X, …) ·
     Sum / TotalSum · Highest / Lowest / HighestAll / LowestAll · StDev · Max / Min / AbsValue / Sqrt / Sqr / Power /
     Log / Exp / Round / RoundUp / RoundDown / Floor / Ceil / Sign · IsNaN / Between / Crosses · IsAscending / IsDescending ·
     CompoundValue / GetValue(常量偏移) / BarNumber ·
     研究函数:RSI() / ATR() / MACD().Value|.Avg|.Diff / BollingerBands().UpperBand|.LowerBand|.MidLine / VolumeAvg().VolAvg。
常量:Double.NaN / Double.POSITIVE_INFINITY / AverageType.* / CrossingDirection.*。
不支持且会明说的:fold、AggregationPeriod(周线月线)、动态偏移(GetValue(x, 变量))、字符串、枚举型 input。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field as _dc_field

from app.services.quant.screen_dsl import Compiled, ScreenError, Stmt

REC_BARS = 250          # 递归定义回看多少根
MAX_BARS = 400          # 二维数组最多多少列(内存:5200 只 × 400 列 × 8 字节 ≈ 17 MB 一个数组)
EMA_EXTRA = 30          # EMA / RSI / ATR 递推额外要求的根数(与 screen_asof 同口径)

_PRICE = ("open", "high", "low", "close", "volume")
_DERIVED = {"hl2": ("high", "low"), "hlc3": ("high", "low", "close"),
            "ohlc4": ("open", "high", "low", "close"), "hl2c4": ("high", "low", "close")}
_SERIES_CN = {"open": "开盘价", "high": "最高价", "low": "最低价", "close": "收盘价", "volume": "成交量"}

# 函数表:名字 → (位置参数名, 默认值, 说明)。命名参数按参数名对号;
# 默认值里的 "close" 表示默认用收盘价序列。
_FN: dict[str, tuple[list[str], dict, str]] = {
    "average":            (["data", "length"], {"length": 12}, "简单均线"),
    "simplemovingavg":    (["data", "length"], {"length": 12}, "简单均线"),
    "movavg":             (["data", "length"], {"length": 12}, "简单均线"),
    "sma":                (["data", "length"], {"length": 12}, "简单均线"),
    "expaverage":         (["data", "length"], {"length": 12}, "指数均线"),
    "ema":                (["data", "length"], {"length": 12}, "指数均线"),
    "movavgexponential":  (["data", "length"], {"length": 12}, "指数均线"),
    "wildersaverage":     (["data", "length"], {"length": 14}, "Wilder 均线"),
    "movingaverage":      (["averagetype", "data", "length"], {"length": 12}, "指定类型的均线"),
    "sum":                (["data", "length"], {"length": 12}, "滚动求和"),
    "totalsum":           (["data"], {}, "累计求和"),
    "highest":            (["data", "length"], {"length": 12}, "窗口最高"),
    "lowest":             (["data", "length"], {"length": 12}, "窗口最低"),
    "highestall":         (["data"], {}, "历史最高"),
    "lowestall":          (["data"], {}, "历史最低"),
    "stdev":              (["data", "length"], {"length": 12}, "标准差"),
    "max":                (["a", "b"], {}, "两者取大"),
    "min":                (["a", "b"], {}, "两者取小"),
    "absvalue":           (["x"], {}, "绝对值"),
    "sqrt":               (["x"], {}, "平方根"),
    "sqr":                (["x"], {}, "平方"),
    "power":              (["base", "exponent"], {}, "乘方"),
    "log":                (["x"], {}, "自然对数"),
    "exp":                (["x"], {}, "e 的幂"),
    "round":              (["x", "numberofdigits"], {"numberofdigits": 0}, "四舍五入"),
    "roundup":            (["x", "numberofdigits"], {"numberofdigits": 0}, "向上取整"),
    "rounddown":          (["x", "numberofdigits"], {"numberofdigits": 0}, "向下取整"),
    "floor":              (["x"], {}, "向下取整"),
    "ceil":               (["x"], {}, "向上取整"),
    "sign":               (["x"], {}, "符号"),
    "isnan":              (["x"], {}, "是否为空"),
    "between":            (["x", "lo", "hi"], {}, "在区间内"),
    "crosses":            (["a", "b", "direction"], {"direction": "any"}, "交叉"),
    "if":                 (["cond", "a", "b"], {}, "条件取值"),
    "compoundvalue":      (["length", "visibledata", "historicaldata"], {"length": 1}, "递归初值"),
    "getvalue":           (["data", "dynamicoffset"], {"dynamicoffset": 0}, "取 n 根之前的值"),
    "barnumber":          ([], {}, "第几根 K 线"),
    "isascending":        (["data", "length"], {"length": 5}, "窗口内递增"),
    "isdescending":       (["data", "length"], {"length": 5}, "窗口内递减"),
    # 研究函数
    "rsi":                (["length", "price"], {"length": 14, "price": "close"}, "RSI"),
    "atr":                (["length"], {"length": 14}, "ATR"),
    "macd":               (["fastlength", "slowlength", "macdlength"],
                           {"fastlength": 12, "slowlength": 26, "macdlength": 9}, "MACD"),
    "bollingerbands":     (["price", "length", "num_dev_dn", "num_dev_up"],
                           {"price": "close", "length": 20, "num_dev_dn": -2.0, "num_dev_up": 2.0}, "布林带"),
    "volumeavg":          (["length"], {"length": 50}, "成交量均线"),
}
_WINDOW_FNS = {"average", "simplemovingavg", "movavg", "sma", "sum", "highest", "lowest", "stdev",
               "isascending", "isdescending"}
_RECUR_FNS = {"expaverage", "ema", "movavgexponential", "wildersaverage"}
_ALL_FNS = {"totalsum", "highestall", "lowestall"}
_PROPS = {"macd": {"value", "avg", "diff"}, "bollingerbands": {"upperband", "lowerband", "midline"},
          "volumeavg": {"volavg"}}
_PROP_DEFAULT = {"macd": "diff", "bollingerbands": "midline", "volumeavg": "volavg"}
_BOOL_FNS = {"isnan", "between", "crosses", "isascending", "isdescending"}

# 明确不支持的东西 —— 说清楚为什么,不让用户猜
_UNSUPPORTED = {
    "fold": "fold 循环这里不支持,请改写成 Sum / Highest / CompoundValue",
    "reference": "reference 引用别的研究这里不支持,直接写 RSI() / MACD() 这类函数",
    "vwap": "这里没有分时数据,算不了 VWAP",
    "imp_volatility": "这里没有期权数据,算不了隐含波动率",
    "open_interest": "这里没有期权数据",
    "getdayofweek": "这里不支持按星期几筛选",
    "getyyyymmdd": "这里不支持按具体日期筛选",
    "secondsfromtime": "这里只有日线,没有盘中时间",
    "secondstilltime": "这里只有日线,没有盘中时间",
    "gettime": "这里只有日线,没有盘中时间",
    "adx": "ADX / DMI 这里还没实现,可以先用 Highest / Lowest 和 ATR() 表达趋势强度",
    "dmi": "ADX / DMI 这里还没实现",
    "stochastic": "随机指标这里还没实现,可以用 (close - Lowest(low, n)) / (Highest(high, n) - Lowest(low, n)) 手写",
    "stochasticfull": "随机指标这里还没实现",
    "wma": "加权均线这里还没实现,请用 Average(简单)或 ExpAverage(指数)",
    "weightedaverage": "加权均线这里还没实现,请用 Average(简单)或 ExpAverage(指数)",
    "hullmovingavg": "Hull 均线这里还没实现",
    "linearregressionslope": "线性回归这里还没实现",
}

_AVG_TYPES = {"averagetype.simple": "sma", "averagetype.exponential": "ema",
              "averagetype.wilders": "wilders", "averagetype.weighted": None, "averagetype.hull": None}
_CROSS_DIRS = {"crossingdirection.above": "above", "crossingdirection.below": "below",
               "crossingdirection.any": "any"}
_CONSTS = {"double.nan": float("nan"), "double.positive_infinity": float("inf"),
           "double.negative_infinity": float("-inf")}


@dataclass
class Plan:
    """编译期算出来的、求值前就能知道的东西。"""
    needs: set = _dc_field(default_factory=set)         # 要用到的基础序列 open/high/low/close/volume
    snap_fields: list = _dc_field(default_factory=list)  # 直接写的扫描源字段名(按今天的值当常量)
    consts: dict = _dc_field(default_factory=dict)       # 常量定义:input 与能折叠成常量的 def
    rec: list = _dc_field(default_factory=list)          # 递归定义的名字
    depth: int = 0                                       # 非递归部分要多少根历史才能算出最后一根
    window: int = 1                                      # 实际要装几列
    funcs: list = _dc_field(default_factory=list)        # 用到的函数(去重、按出现顺序)
    plots: int = 1                                       # plot 语句个数(>1 时取最后一个,要提示)
    notes: list = _dc_field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# 编译:校验 + 常量折叠 + 深度
# ═══════════════════════════════════════════════════════════════

def _args_of(fn: str, args: list, where: str) -> list:
    """位置 + 命名参数 → 按 _FN 顺序排好的参数节点列表(缺的用默认值)。"""
    params, defaults, _ = _FN[fn]
    out: list = [None] * len(params)
    pos = 0
    for a in args:
        if isinstance(a, tuple) and a[0] == "named":
            key = a[1].lower()
            if key == "price":
                key = "data" if "data" in params else key
            if key not in params:
                raise ScreenError(f"{where}:{fn}() 没有叫 {a[1]!r} 的参数,它的参数是 {', '.join(params)}")
            out[params.index(key)] = a[2]
        else:
            if pos >= len(params):
                raise ScreenError(f"{where}:{fn}() 最多 {len(params)} 个参数,收到 {len(args)} 个")
            out[pos] = a
            pos += 1
    for i, p in enumerate(params):
        if out[i] is None:
            if p in defaults:
                d = defaults[p]
                out[i] = ("name", "close") if d == "close" else (("str", d) if isinstance(d, str) else ("num", float(d), 0, 0))
            elif p == "direction":
                out[i] = ("str", "any")
            else:
                raise ScreenError(f"{where}:{fn}() 少了参数 {p}")
    return out


def _const(node, consts: dict):
    """能折叠成常量就返回数字,否则 None。只认 num / bool / 常量名 / 一元负 / 常量间的四则。"""
    k = node[0]
    if k == "num":
        return node[1]
    if k == "bool":
        return 1.0 if node[1] else 0.0
    if k == "name":
        low = node[1].lower()
        if node[1] in consts:
            return consts[node[1]]
        if low in _CONSTS:
            return _CONSTS[low]
        return None
    if k == "un":
        v = _const(node[2], consts)
        if v is None:
            return None
        return -v if node[1] == "neg" else (1.0 - (1.0 if v else 0.0))
    if k == "bin" and node[1] in ("+", "-", "*", "/"):
        a, b = _const(node[2], consts), _const(node[3], consts)
        if a is None or b is None:
            return None
        if node[1] == "+":
            return a + b
        if node[1] == "-":
            return a - b
        if node[1] == "*":
            return a * b
        return None if b == 0 else a / b
    return None


def _int_const(node, consts: dict, what: str, lo: int = 1) -> int:
    v = _const(node, consts)
    if v is None or v != v or v == math.inf or v == -math.inf:
        raise ScreenError(
            f"{what}必须是常量(数字,或用 input 定义的参数);"
            f"这里写的是一个会随 K 线变化的值,ThinkScript 的动态偏移 / 动态窗口这里不支持")
    if abs(v - round(v)) > 1e-9:
        raise ScreenError(f"{what}必须是整数,这里是 {v}")
    iv = int(round(v))
    if iv < lo:
        raise ScreenError(f"{what}不能小于 {lo}(这里是 {iv})"
                          + (";负数偏移 = 未来的 K 线,选股里不存在" if lo == 0 and iv < 0 else ""))
    return iv


def compile(src: str, stmts: list[Stmt], plot_name: str, has_field, sma_periods, ema_periods,
            rsi_periods, skipped: list[str] | None = None) -> Compiled:
    """校验 + 常量折叠 + 算每个定义要多少根历史。不碰数据。"""
    plan = Plan()
    plan.plots = sum(1 for s in stmts if s.kind == "plot")
    names = {s.name: s for s in stmts}
    order = [s.name for s in stmts]
    consts: dict = {}
    depth_memo: dict = {}
    rec_set: set = set()
    funcs: list = []
    snap: list = []
    needs: set = set()

    # ── 第一遍:找递归定义(直接引用自己)、常量、input ──
    def refs(node, acc: set):
        if not isinstance(node, tuple):
            return
        k = node[0]
        if k == "name":
            acc.add(node[1])
        elif k == "num" or k == "bool" or k == "str":
            return
        else:
            for x in node[1:]:
                if isinstance(x, tuple):
                    refs(x, acc)
                elif isinstance(x, list):
                    for y in x:
                        refs(y, acc)

    for st in stmts:
        acc: set = set()
        refs(st.node, acc)
        if st.name in acc:
            rec_set.add(st.name)
        # 引用了后面才定义的名字(且不是自己)= 互相递归,ThinkScript 也不允许
        later = [n for n in acc if n in names and n != st.name and order.index(n) > order.index(st.name)]
        if later:
            raise ScreenError(
                f"第 {st.line} 行:{st.name} 用到了在它后面才定义的 {later[0]} —— "
                f"定义要写在使用之前;两个定义互相引用(互递归)这里不支持")
    for st in stmts:
        if st.name in rec_set:
            continue
        v = _const(st.node, consts)
        if v is not None:
            consts[st.name] = v
        elif st.kind == "input":
            raise ScreenError(
                f"第 {st.line} 行:input {st.name} 的值必须是常量(数字 / true / false / yes / no);"
                f"ThinkScript 里 `input price = close;` 这种序列型参数请改成 `def {st.name} = close;`")

    # ── 第二遍:校验每个节点、算深度 ──
    def resolve_name(nm: str, where: str) -> str:
        low = nm.lower()
        if nm in names:
            return "def"
        if low in _PRICE:
            needs.add(low)
            return "price"
        if low in _DERIVED:
            needs.update(_DERIVED[low])
            return "derived"
        if low in _CONSTS:
            return "const"
        if low in _AVG_TYPES or low in _CROSS_DIRS:
            return "enum"
        if low.startswith("aggregationperiod."):
            raise ScreenError(f"{where}:这里只有日线,不支持 {nm}(周线 / 月线 / 分钟线)")
        if low.startswith("color."):
            raise ScreenError(f"{where}:{nm} 是画图用的颜色,选股条件里用不上")
        if has_field(nm):
            if nm not in snap:
                snap.append(nm)
            return "snap"
        raise ScreenError(
            f"{where}:不认识 {nm!r}。它既不是 open/high/low/close/volume,"
            f"也不是扫描源的字段名。如果想用自定义变量,要先 `def {nm} = ...;` 定义"
            f"(定义要写在使用之前)。")

    def depth(node, owner: str, in_window: bool, where: str) -> int:
        """→ 这个表达式在最后一根上要多少根**之前**的历史。同时做校验。"""
        k = node[0]
        if k in ("num", "bool"):
            return 0
        if k == "str":
            raise ScreenError(f"{where}:字符串只能用作函数的参数名")
        if k == "name":
            kind = resolve_name(node[1], where)
            if kind == "def":
                nm = node[1]
                if nm == owner:
                    raise ScreenError(
                        f"{where}:{nm} 直接引用了自己(没有 [1] 偏移)—— 递归定义必须写成 {nm}[1] 这种"
                        f"「上一根的值」,否则它等于用自己定义自己")
                if nm in rec_set:
                    return 0                         # 递归定义的值当前根就有,靠循环保证
                return depth_of(nm)
            return 0
        if k == "idx":
            base, off = node[1], node[2]
            n = _int_const(off, consts, f"{where}:K 线偏移 [n] 的 n", lo=0)
            if base[0] == "name" and base[1] == owner:
                if n < 1:
                    raise ScreenError(f"{where}:{owner}[{n}] 引用了自己当前这一根 —— 递归只能引用之前的(至少 [1])")
                if in_window:
                    raise ScreenError(
                        f"{where}:递归定义里对自己的引用({owner}[{n}])不能放在 Sum / Highest 这类窗口函数里,"
                        f"先把窗口部分单独 def 一个变量")
                return 0
            return depth(base, owner, in_window, where) + n
        if k == "prop":
            base = node[1]
            if base[0] != "call" or base[1].lower() not in _PROPS:
                raise ScreenError(f"{where}:只有 MACD() / BollingerBands() / VolumeAvg() 后面能接 .{node[2]}")
            fn = base[1].lower()
            if node[2].lower() not in _PROPS[fn]:
                raise ScreenError(f"{where}:{base[1]}() 没有 .{node[2]},它有:{' / '.join(sorted(_PROPS[fn]))}")
            return depth(base, owner, in_window, where)
        if k == "if":
            return max(depth(node[1], owner, in_window, where), depth(node[2], owner, in_window, where),
                       depth(node[3], owner, in_window, where))
        if k == "cross":
            return max(depth(node[2], owner, in_window, where), depth(node[3], owner, in_window, where)) + 1
        if k == "within":
            n = _int_const(node[2], consts, f"{where}:within 后面的根数", lo=1)
            return depth(node[1], owner, in_window, where) + n - 1
        if k == "named":
            return depth(node[2], owner, in_window, where)
        if k == "un":
            return depth(node[2], owner, in_window, where)
        if k == "bin":
            return max(depth(node[2], owner, in_window, where), depth(node[3], owner, in_window, where))
        if k == "call":
            fn = node[1].lower()
            if fn in _UNSUPPORTED:
                raise ScreenError(f"{where}:{_UNSUPPORTED[fn]}")
            if fn in _PRICE or fn in _DERIVED:
                # close(period = AggregationPeriod.WEEK) —— 序列名当函数调 = 想换周期
                raise ScreenError(f"{where}:这里只有日线,不支持 {node[1]}(period = …) 换成周线 / 月线 / 分钟线;"
                                  f"直接写 {node[1]} 就是日线")
            if fn not in _FN:
                raise ScreenError(
                    f"{where}:不支持的函数 {node[1]}()。支持:Average / ExpAverage / WildersAverage / "
                    f"MovingAverage · Sum / TotalSum · Highest / Lowest / HighestAll / LowestAll · StDev · "
                    f"Max / Min / AbsValue / Sqrt / Power / Log / Exp / Round / Floor / Ceil / Sign · "
                    f"IsNaN / Between / Crosses · CompoundValue / GetValue / BarNumber · "
                    f"RSI() / ATR() / MACD() / BollingerBands() / VolumeAvg()。"
                    f"其它指标可以用这些函数手写,或直接写扫描源字段名(在「可用字段」里搜)。")
            if fn not in funcs:
                funcs.append(fn)
            args = _args_of(fn, node[2], where)
            if fn in _WINDOW_FNS:
                n = _int_const(args[1], consts, f"{where}:{node[1]}() 的窗口长度")
                return depth(args[0], owner, True, where) + n - 1
            if fn in _RECUR_FNS:
                n = _int_const(args[1], consts, f"{where}:{node[1]}() 的周期")
                return depth(args[0], owner, True, where) + n + EMA_EXTRA
            if fn == "movingaverage":
                t = args[0]
                if t[0] != "name" or t[1].lower() not in _AVG_TYPES:
                    raise ScreenError(f"{where}:MovingAverage 的第 1 个参数要是 AverageType.SIMPLE / EXPONENTIAL / WILDERS")
                if _AVG_TYPES[t[1].lower()] is None:
                    raise ScreenError(f"{where}:{t[1]} 这种均线这里还没实现,请用 SIMPLE / EXPONENTIAL / WILDERS")
                n = _int_const(args[2], consts, f"{where}:MovingAverage 的周期")
                extra = 0 if _AVG_TYPES[t[1].lower()] == "sma" else EMA_EXTRA + 1
                return depth(args[1], owner, True, where) + n - 1 + extra
            if fn in _ALL_FNS:
                depth(args[0], owner, True, where)
                return REC_BARS                      # 「历史全部」:按能装的最多算
            if fn in ("max", "min", "power", "between"):
                return max(depth(a, owner, in_window, where) for a in args)
            if fn in ("absvalue", "sqrt", "sqr", "log", "exp", "floor", "ceil", "sign", "isnan"):
                return depth(args[0], owner, in_window, where)
            if fn in ("round", "roundup", "rounddown"):
                _int_const(args[1], consts, f"{where}:{node[1]}() 的小数位数", lo=0)
                return depth(args[0], owner, in_window, where)
            if fn == "crosses":
                d = args[2]
                if not (d[0] == "str" or (d[0] == "name" and d[1].lower() in _CROSS_DIRS)):
                    raise ScreenError(f"{where}:Crosses 的第 3 个参数要是 CrossingDirection.ABOVE / BELOW / ANY")
                return max(depth(args[0], owner, in_window, where), depth(args[1], owner, in_window, where)) + 1
            if fn == "if":
                return max(depth(a, owner, in_window, where) for a in args)
            if fn == "compoundvalue":
                n = _int_const(args[0], consts, f"{where}:CompoundValue 的第 1 个参数", lo=1)
                return max(depth(args[1], owner, in_window, where), depth(args[2], owner, in_window, where)) + n
            if fn == "getvalue":
                n = _int_const(args[1], consts, f"{where}:GetValue 的偏移", lo=0)
                if args[0][0] == "name" and args[0][1] == owner:
                    if n < 1 or in_window:
                        raise ScreenError(f"{where}:GetValue({owner}, n) 引用自己时 n 至少为 1,且不能放在窗口函数里")
                    return 0
                return depth(args[0], owner, in_window, where) + n
            if fn == "barnumber":
                return 0
            if fn == "rsi":
                n = _int_const(args[0], consts, f"{where}:RSI 的周期")
                return depth(args[1], owner, True, where) + n + EMA_EXTRA
            if fn == "atr":
                n = _int_const(args[0], consts, f"{where}:ATR 的周期")
                needs.update(("high", "low", "close"))
                return n + 1 + EMA_EXTRA
            if fn == "macd":
                f_, s_, m_ = (_int_const(args[i], consts, f"{where}:MACD 的周期") for i in range(3))
                needs.add("close")
                return max(f_, s_) + m_ + EMA_EXTRA
            if fn == "bollingerbands":
                n = _int_const(args[1], consts, f"{where}:BollingerBands 的周期")
                _const(args[2], consts); _const(args[3], consts)
                return depth(args[0], owner, True, where) + n - 1
            if fn == "volumeavg":
                n = _int_const(args[0], consts, f"{where}:VolumeAvg 的周期")
                needs.add("volume")
                return n - 1
            raise ScreenError(f"{where}:内部错误:函数 {fn} 没有深度规则")
        raise ScreenError(f"{where}:内部错误:未知节点 {k}")

    def depth_of(nm: str) -> int:
        if nm in depth_memo:
            return depth_memo[nm]
        st = names[nm]
        where = f"第 {st.line} 行({nm})"
        d = depth(st.node, nm, False, where)
        depth_memo[nm] = d
        return d

    for st in stmts:
        depth_of(st.name)
    plan.depth = depth_of(plot_name)
    plan.rec = [n for n in order if n in rec_set]
    plan.consts = consts
    plan.needs = needs
    plan.snap_fields = snap
    plan.funcs = funcs
    need_cols = plan.depth + 1
    if plan.rec or any(f in _ALL_FNS for f in funcs):
        need_cols = max(need_cols, REC_BARS)
    plan.window = min(MAX_BARS, need_cols + 2)
    if plan.depth + 1 > MAX_BARS:
        raise ScreenError(
            f"这份脚本要回看 {plan.depth + 1} 根 K 线才能算出最后一根,超过了本地日线能装的 {MAX_BARS} 根"
            f"(自家日线约 320~600 根)。把最长的窗口 / 偏移改小一些。")
    if skipped:
        plan.notes.append("已跳过研究用的画图 / 标签语句(对选股没有影响):" + "、".join(skipped))
    if plan.plots > 1:
        plan.notes.append(f"脚本里有 {plan.plots} 个 plot,按 ThinkScript 扫描的规则取最后一个({plot_name})当筛选条件")
    fields = [f for f in snap]
    return Compiled(stmts=stmts, plot_name=plot_name, fields=fields, notes=list(plan.notes),
                    series=plan, skipped=list(skipped or []))


def describe(plan: Plan) -> str:
    """一句话说清这次是怎么算的 —— 放进 warnings 首条。"""
    used = []
    if plan.rec:
        used.append(f"递归定义({', '.join(plan.rec)})")
    if plan.funcs:
        used.append("函数 " + " / ".join(plan.funcs))
    ser = "、".join(_SERIES_CN[s] for s in _PRICE if s in plan.needs) or "收盘价"
    return (f"时间序列模式:脚本用到了 K 线偏移 / if / 滚动窗口{('(' + ';'.join(used) + ')') if used else ''},"
            f"按自家全市场日线**逐根**求值(不是扫描源快照)。用到 {ser},"
            f"每只票要 {plan.depth + 1} 根以上历史" + (f",递归定义按最近 {REC_BARS} 根算" if plan.rec else "")
            + ";历史不够或某根缺数的票算不出(不算不满足)。")


# ═══════════════════════════════════════════════════════════════
# 求值
# ═══════════════════════════════════════════════════════════════

def _np():
    import numpy as np
    return np


class _Ctx:
    def __init__(self, c: Compiled, bars: dict, snap: dict):
        self.np = _np()
        self.c = c
        self.plan: Plan = c.series
        self.bars = bars                                # open/high/low/close/volume → (N, W)
        any_arr = next(iter(bars.values()))
        self.N, self.W = any_arr.shape
        self.snap = snap                                # 字段 → (N,) | None
        self.env: dict = {}                             # def 名 → (N, W) 数组 | 标量
        self.names = {s.name: s for s in c.stmts}
        self.rec_set = set(self.plan.rec)
        self.full_cache: dict = {}                      # 递归求值时,不含自引用的子表达式整列缓存
        # 每只票第一根真实 K 线在第几列(左边补的 NaN 之后)。递归定义引用「起点之前」的自己
        # 按 ThinkScript 的规则取 0 —— 不能拿补位的 NaN 当初值,否则 NaN + 1 会一路传到最后一根,
        # 连续计数永远算不出
        np = self.np
        valid = ~np.isnan(bars["close"])
        self.first = np.where(valid.any(axis=1), np.argmax(valid, axis=1), self.W)

    # ── 基本形状 ──
    def arr(self, x):
        """标量 / 0 维数组 → (N, W) 常量数组;(N,) 快照列 → (N, 1) 广播。"""
        np = self.np
        if isinstance(x, (int, float)):
            return np.full((self.N, self.W), float(x))
        x = np.asarray(x, dtype=float)
        if x.ndim == 0:
            # 全常量表达式(Floor(10.7) == 10)一路都是 0 维数组,最后也要铺成整张表
            return np.full((self.N, self.W), float(x))
        if x.ndim == 1:
            return np.broadcast_to(x[:, None], (self.N, self.W))
        return x

    def shift(self, x, n: int):
        np = self.np
        if n == 0:
            return x
        if isinstance(x, (int, float)):
            return x
        x = self.arr(x)
        out = np.full_like(x, np.nan)
        if n < x.shape[1]:
            out[:, n:] = x[:, :-n]
        return out

    def roll(self, x, n: int, fn: str):
        """滑动窗口。窗口内有 NaN → NaN(不拿部分窗口冒充整个窗口)。"""
        np = self.np
        x = self.arr(x)
        out = np.full_like(x, np.nan)
        if n > x.shape[1]:
            return out
        from numpy.lib.stride_tricks import sliding_window_view
        v = sliding_window_view(x, n, axis=1)
        with np.errstate(invalid="ignore"):
            if fn == "sum":
                r = v.sum(axis=-1)
            elif fn == "mean":
                r = v.mean(axis=-1)
            elif fn == "max":
                r = v.max(axis=-1)
            elif fn == "min":
                r = v.min(axis=-1)
            elif fn == "std":
                r = v.std(axis=-1)
            elif fn == "asc":
                d = np.diff(v, axis=-1)
                r = np.where(np.isnan(d).any(axis=-1), np.nan, (d > 0).all(axis=-1).astype(float))
            elif fn == "desc":
                d = np.diff(v, axis=-1)
                r = np.where(np.isnan(d).any(axis=-1), np.nan, (d < 0).all(axis=-1).astype(float))
            else:
                raise ScreenError(f"内部错误:未知窗口函数 {fn}")
        out[:, n - 1:] = r
        return out

    def recur_avg(self, x, n: int, kind: str):
        """EMA / Wilder:沿时间递推。遇到 NaN 重新起算(左边补的 NaN 不能把整条线毒掉)。"""
        np = self.np
        x = self.arr(x)
        alpha = 2.0 / (n + 1) if kind == "ema" else 1.0 / n
        out = np.full_like(x, np.nan)
        prev = np.full(x.shape[0], np.nan)
        cnt = np.zeros(x.shape[0])
        for j in range(x.shape[1]):
            col = x[:, j]
            nan = np.isnan(col)
            cur = np.where(np.isnan(prev), col, alpha * col + (1 - alpha) * prev)
            cur = np.where(nan, np.nan, cur)
            cnt = np.where(nan, 0, cnt + 1)
            # 递推要「周期 + EMA_EXTRA」根才算稳定(与 screen_asof 同口径),之前的给 NaN
            out[:, j] = np.where(cnt >= n + EMA_EXTRA, cur, np.nan)
            prev = cur
        return out

    def cum(self, x, fn: str):
        np = self.np
        x = self.arr(x)
        nan = np.isnan(x)
        seen = np.cumsum(~nan, axis=1) > 0
        if fn == "sum":
            r = np.cumsum(np.where(nan, 0.0, x), axis=1)
        elif fn == "max":
            r = np.maximum.accumulate(np.where(nan, -np.inf, x), axis=1)
        else:
            r = np.minimum.accumulate(np.where(nan, np.inf, x), axis=1)
        return np.where(seen, r, np.nan)

    def barnumber(self):
        np = self.np
        c = self.bars["close"]
        valid = ~np.isnan(c)
        first_seen = np.cumsum(valid, axis=1) > 0
        idx = np.broadcast_to(np.arange(self.W)[None, :], c.shape).astype(float)
        first = np.argmax(valid, axis=1)[:, None]
        return np.where(first_seen, idx - first + 1, np.nan)

    # ── 三值逻辑 ──
    def truth(self, x):
        np = self.np
        x = np.asarray(x, dtype=float)
        return np.where(np.isnan(x), np.nan, (x != 0).astype(float))

    def and_(self, l, r):
        np = self.np
        l, r = np.asarray(l, dtype=float), np.asarray(r, dtype=float)
        ln, rn = np.isnan(l), np.isnan(r)
        lf, rf = (~ln) & (l == 0), (~rn) & (r == 0)
        out = np.ones(np.broadcast(l, r).shape)
        out = np.where(ln | rn, np.nan, out)
        return np.where(lf | rf, 0.0, out)

    def or_(self, l, r):
        np = self.np
        l, r = np.asarray(l, dtype=float), np.asarray(r, dtype=float)
        ln, rn = np.isnan(l), np.isnan(r)
        lt, rt = (~ln) & (l != 0), (~rn) & (r != 0)
        out = np.zeros(np.broadcast(l, r).shape)
        out = np.where(ln | rn, np.nan, out)
        return np.where(lt | rt, 1.0, out)

    def not_(self, x):
        np = self.np
        x = np.asarray(x, dtype=float)
        return np.where(np.isnan(x), np.nan, (x == 0).astype(float))

    def cmp(self, op: str, l, r):
        np = self.np
        l, r = np.asarray(l, dtype=float), np.asarray(r, dtype=float)
        with np.errstate(invalid="ignore"):
            if op == ">":
                res = l > r
            elif op == ">=":
                res = l >= r
            elif op == "<":
                res = l < r
            elif op == "<=":
                res = l <= r
            elif op == "==":
                res = l == r
            else:
                res = l != r
        return np.where(np.isnan(l) | np.isnan(r), np.nan, res.astype(float))

    def arith(self, op: str, l, r):
        np = self.np
        l, r = np.asarray(l, dtype=float), np.asarray(r, dtype=float)
        with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
            if op == "+":
                return l + r
            if op == "-":
                return l - r
            if op == "*":
                return l * r
            out = l / r
            return np.where(r == 0, np.nan, out)          # 除零不是 0,是"算不出"

    def if_(self, c, a, b):
        np = self.np
        c = np.asarray(c, dtype=float)
        a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
        return np.where(np.isnan(c), np.nan, np.where(c != 0, a, b))

    def cross(self, dirn: str, a, b, col: int | None = None):
        """a 穿越 b:上穿 = 这根 a > b 且上一根 a <= b。col 模式下由调用方保证不含自引用。"""
        np = self.np
        a, b = self.arr(a), self.arr(b)
        pa, pb = self.shift(a, 1), self.shift(b, 1)
        up = self.and_(self.cmp(">", a, b), self.cmp("<=", pa, pb))
        dn = self.and_(self.cmp("<", a, b), self.cmp(">=", pa, pb))
        if dirn == "above":
            return up
        if dirn == "below":
            return dn
        return self.or_(up, dn)

    # ── 名字 ──
    def name(self, nm: str):
        np = self.np
        low = nm.lower()
        if nm in self.env:
            return self.env[nm]
        if nm in self.plan.consts:
            return float(self.plan.consts[nm])
        if low in self.bars:
            return self.bars[low]
        if low in _DERIVED:
            parts = [self.bars[p] for p in _DERIVED[low]]
            if low == "hl2c4":
                return (self.bars["high"] + self.bars["low"] + 2 * self.bars["close"]) / 4.0
            return sum(parts) / float(len(parts))
        if low in _CONSTS:
            return float(_CONSTS[low])
        if low in _AVG_TYPES or low in _CROSS_DIRS:
            return ("enum", low)
        v = self.snap.get(nm)
        if v is None:
            return np.full((self.N, self.W), np.nan)
        return np.asarray(v, dtype=float)               # (N,) 广播成常量列
    # ── 整列求值 ──
    def full(self, node):
        np = self.np
        k = node[0]
        if k == "num":
            return float(node[1])
        if k == "bool":
            return 1.0 if node[1] else 0.0
        if k == "name":
            return self.name(node[1])
        if k == "idx":
            n = int(round(_const(node[2], self.plan.consts)))
            return self.shift(self.full(node[1]), n)
        if k == "prop":
            return self.call(node[1][1], node[1][2], prop=node[2].lower())
        if k == "if":
            return self.if_(self.full(node[1]), self.full(node[2]), self.full(node[3]))
        if k == "cross":
            return self.cross(node[1], self.full(node[2]), self.full(node[3]))
        if k == "within":
            n = int(round(_const(node[2], self.plan.consts)))
            s = self.roll(self.truth(self.full(node[1])), n, "sum")
            return self.cmp(">", s, 0.0)
        if k == "un":
            v = self.full(node[2])
            return -np.asarray(v, dtype=float) if node[1] == "neg" else self.not_(v)
        if k == "bin":
            op = node[1]
            l, r = self.full(node[2]), self.full(node[3])
            if op == "and":
                return self.and_(l, r)
            if op == "or":
                return self.or_(l, r)
            if op in ("+", "-", "*", "/"):
                return self.arith(op, l, r)
            return self.cmp(op, l, r)
        if k == "call":
            return self.call(node[1], node[2])
        if k == "named":
            return self.full(node[2])
        raise ScreenError(f"内部错误:求值遇到未知节点 {k}")

    def call(self, fname: str, raw_args: list, prop: str | None = None):
        np = self.np
        fn = fname.lower()
        args = _args_of(fn, raw_args, fname)
        C = self.plan.consts
        ci = lambda i, lo=0: int(round(_const(args[i], C)))      # noqa: E731  编译期已校验
        if fn in _WINDOW_FNS:
            n = ci(1)
            x = self.full(args[0])
            if fn == "sum":
                return self.roll(x, n, "sum")           # 布尔已经是 1/0/NaN,直接加就是计数
            if fn in ("average", "simplemovingavg", "movavg", "sma"):
                return self.roll(x, n, "mean")
            if fn == "highest":
                return self.roll(x, n, "max")
            if fn == "lowest":
                return self.roll(x, n, "min")
            if fn == "stdev":
                return self.roll(x, n, "std")
            if fn == "isascending":
                return self.roll(x, n, "asc")
            return self.roll(x, n, "desc")
        if fn in _RECUR_FNS:
            return self.recur_avg(self.full(args[0]), ci(1), "wilders" if fn == "wildersaverage" else "ema")
        if fn == "movingaverage":
            t = _AVG_TYPES[args[0][1].lower()]
            x, n = self.full(args[1]), ci(2)
            return self.roll(x, n, "mean") if t == "sma" else self.recur_avg(x, n, t)
        if fn == "totalsum":
            return self.cum(self.full(args[0]), "sum")
        if fn == "highestall":
            return self.cum(self.full(args[0]), "max")
        if fn == "lowestall":
            return self.cum(self.full(args[0]), "min")
        if fn in ("max", "min"):
            a, b = np.asarray(self.full(args[0]), dtype=float), np.asarray(self.full(args[1]), dtype=float)
            return np.maximum(a, b) if fn == "max" else np.minimum(a, b)     # NaN 传播
        if fn == "absvalue":
            return np.abs(np.asarray(self.full(args[0]), dtype=float))
        if fn == "sqrt":
            with np.errstate(invalid="ignore"):
                return np.sqrt(np.asarray(self.full(args[0]), dtype=float))
        if fn == "sqr":
            x = np.asarray(self.full(args[0]), dtype=float)
            return x * x
        if fn == "power":
            with np.errstate(invalid="ignore", over="ignore", divide="ignore"):
                return np.power(np.asarray(self.full(args[0]), dtype=float), np.asarray(self.full(args[1]), dtype=float))
        if fn == "log":
            with np.errstate(invalid="ignore", divide="ignore"):
                return np.log(np.asarray(self.full(args[0]), dtype=float))
        if fn == "exp":
            with np.errstate(over="ignore"):
                return np.exp(np.asarray(self.full(args[0]), dtype=float))
        if fn in ("round", "roundup", "rounddown"):
            x = np.asarray(self.full(args[0]), dtype=float)
            d = ci(1)
            f = 10.0 ** d
            if fn == "round":
                # ThinkScript 的 Round 是四舍五入(2.5 → 3);numpy 的 np.round 是银行家舍入(2.5 → 2),不能用
                with np.errstate(invalid="ignore"):
                    return np.sign(x) * np.floor(np.abs(x) * f + 0.5) / f
            return (np.ceil(x * f) if fn == "roundup" else np.floor(x * f)) / f
        if fn == "floor":
            return np.floor(np.asarray(self.full(args[0]), dtype=float))
        if fn == "ceil":
            return np.ceil(np.asarray(self.full(args[0]), dtype=float))
        if fn == "sign":
            return np.sign(np.asarray(self.full(args[0]), dtype=float))
        if fn == "isnan":
            return np.isnan(np.asarray(self.full(args[0]), dtype=float)).astype(float)
        if fn == "between":
            x = self.full(args[0])
            return self.and_(self.cmp(">=", x, self.full(args[1])), self.cmp("<=", x, self.full(args[2])))
        if fn == "crosses":
            d = args[2]
            dirn = d[1] if d[0] == "str" else _CROSS_DIRS[d[1].lower()]
            return self.cross(dirn, self.full(args[0]), self.full(args[1]))
        if fn == "if":
            return self.if_(self.full(args[0]), self.full(args[1]), self.full(args[2]))
        if fn == "compoundvalue":
            n = ci(0)
            v, init = self.arr(self.full(args[1])), self.arr(self.full(args[2]))
            bn = self.barnumber()
            return np.where(bn > n, v, init)
        if fn == "getvalue":
            return self.shift(self.full(args[0]), ci(1))
        if fn == "barnumber":
            return self.barnumber()
        if fn == "rsi":
            return self.rsi(self.full(args[1]), ci(0))
        if fn == "atr":
            return self.atr(ci(0))
        if fn == "macd":
            f_, s_, m_ = ci(0), ci(1), ci(2)
            c = self.bars["close"]
            value = self.recur_avg(c, f_, "ema") - self.recur_avg(c, s_, "ema")
            if prop == "value":
                return value
            avg = self.recur_avg(value, m_, "ema")
            return avg if prop == "avg" else value - avg
        if fn == "bollingerbands":
            x, n = self.full(args[0]), ci(1)
            dn, up = float(_const(args[2], C)), float(_const(args[3], C))
            mid = self.roll(x, n, "mean")
            if prop == "midline":
                return mid
            sd = self.roll(x, n, "std")
            return mid + (up if prop == "upperband" else dn) * sd
        if fn == "volumeavg":
            return self.roll(self.bars["volume"], ci(0), "mean")
        raise ScreenError(f"内部错误:函数 {fname} 没有求值规则")

    def rsi(self, x, n: int):
        np = self.np
        x = self.arr(x)
        d = np.full_like(x, np.nan)
        d[:, 1:] = x[:, 1:] - x[:, :-1]
        gain = np.where(np.isnan(d), np.nan, np.maximum(d, 0.0))
        loss = np.where(np.isnan(d), np.nan, np.maximum(-d, 0.0))
        ag, al = self.recur_avg(gain, n, "wilders"), self.recur_avg(loss, n, "wilders")
        with np.errstate(invalid="ignore", divide="ignore"):
            rs = ag / al
            out = 100.0 - 100.0 / (1.0 + rs)
            out = np.where((al == 0) & (ag > 0), 100.0, out)
            out = np.where((al == 0) & (ag == 0), 50.0, out)
        return out

    def atr(self, n: int):
        np = self.np
        h, lo, c = self.bars["high"], self.bars["low"], self.bars["close"]
        pc = self.shift(c, 1)
        with np.errstate(invalid="ignore"):
            tr = np.maximum(h - lo, np.maximum(np.abs(h - pc), np.abs(lo - pc)))
        return self.recur_avg(tr, n, "wilders")

    # ── 递归定义:逐根 ──
    def has_self(self, node, owner: str) -> bool:
        if not isinstance(node, tuple):
            return False
        k = node[0]
        if k == "name":
            return node[1] == owner
        if k in ("num", "bool", "str"):
            return False
        for x in node[1:]:
            if isinstance(x, tuple) and self.has_self(x, owner):
                return True
            if isinstance(x, list) and any(self.has_self(y, owner) for y in x):
                return True
        return False

    def col(self, node, j: int, owner: str, out):
        """递归定义的第 j 列。不含自引用的子表达式整列算一次、切第 j 列。"""
        np = self.np
        if not self.has_self(node, owner):
            key = id(node)
            if key not in self.full_cache:
                self.full_cache[key] = self.arr(self.full(node))
            return self.full_cache[key][:, j]
        k = node[0]
        if k == "idx":
            base, off = node[1], node[2]
            n = int(round(_const(off, self.plan.consts)))
            if base[0] == "name" and base[1] == owner:
                return self.prev_self(out, j - n)
            return self.col(base, j - n, owner, out) if j - n >= 0 else np.full(self.N, np.nan)
        if k == "if":
            return self.if_(self.col(node[1], j, owner, out), self.col(node[2], j, owner, out),
                            self.col(node[3], j, owner, out))
        if k == "un":
            v = self.col(node[2], j, owner, out)
            return -v if node[1] == "neg" else self.not_(v)
        if k == "bin":
            op = node[1]
            l, r = self.col(node[2], j, owner, out), self.col(node[3], j, owner, out)
            if op == "and":
                return self.and_(l, r)
            if op == "or":
                return self.or_(l, r)
            if op in ("+", "-", "*", "/"):
                return self.arith(op, l, r)
            return self.cmp(op, l, r)
        if k == "named":
            return self.col(node[2], j, owner, out)
        if k == "call":
            fn = node[1].lower()
            args = _args_of(fn, node[2], node[1])
            C = self.plan.consts
            if fn == "if":
                return self.if_(self.col(args[0], j, owner, out), self.col(args[1], j, owner, out),
                                self.col(args[2], j, owner, out))
            if fn == "compoundvalue":
                # ThinkScript:K 线序号 > length 取 visible,否则取 historical。序号是**每只票自己的**
                # (从第一根真实 K 线数起),不是二维数组的列号 —— 左边补了 NaN 的票列号早就过了 length
                n = int(round(_const(args[0], C)))
                bn = (j - self.first) + 1
                vis = self.col(args[1], j, owner, out)
                his = self.col(args[2], j, owner, out)
                return np.where(bn > n, vis, his)
            if fn == "getvalue":
                n = int(round(_const(args[1], C)))
                base = args[0]
                if base[0] == "name" and base[1] == owner:
                    return self.prev_self(out, j - n)
                return self.col(base, j - n, owner, out) if j - n >= 0 else np.full(self.N, np.nan)
            if fn in ("max", "min"):
                a, b = self.col(args[0], j, owner, out), self.col(args[1], j, owner, out)
                return np.maximum(a, b) if fn == "max" else np.minimum(a, b)
            if fn == "absvalue":
                return np.abs(self.col(args[0], j, owner, out))
            if fn == "between":
                x = self.col(args[0], j, owner, out)
                return self.and_(self.cmp(">=", x, self.col(args[1], j, owner, out)),
                                 self.cmp("<=", x, self.col(args[2], j, owner, out)))
            if fn == "isnan":
                return np.isnan(self.col(args[0], j, owner, out)).astype(float)
            raise ScreenError(f"{owner} 的递归定义里,{node[1]}() 不能包含对自己的引用(编译期应已拦下)")
        raise ScreenError(f"内部错误:递归求值遇到未知节点 {k}")

    def prev_self(self, out, jj: int):
        """递归定义里「之前的自己」:起点之前(含左侧补位)按 ThinkScript 的默认初值 0。"""
        np = self.np
        if jj < 0:
            return np.zeros(self.N)
        return np.where(jj < self.first, 0.0, out[:, jj])

    def recur(self, st: Stmt):
        np = self.np
        out = np.full((self.N, self.W), np.nan)
        for j in range(self.W):
            out[:, j] = self.col(st.node, j, st.name, out)
        return out


def evaluate(c: Compiled, bars: dict, snap: dict | None = None, keep: set | None = None) -> dict:
    """→ {"verdict": (N,) 1/0/NaN, "last": {def名: (N,)}, "full": {名: (N, W)}(只有 keep 里的)}

    bars = {"open"|"high"|"low"|"close"|"volume": (N, W) float,右对齐到求值日、左边补 NaN}
    snap = {扫描源字段: (N,) float | None}(时间回溯时传 None → 整批 NaN)
    """
    np = _np()
    snap = snap or {}
    for k in _PRICE:
        if k not in bars:
            any_arr = next(iter(bars.values()))
            bars[k] = np.full_like(any_arr, np.nan)
    ctx = _Ctx(c, bars, snap)
    keep = keep or set()
    full: dict = {}
    for st in c.stmts:
        if st.name in ctx.plan.consts and st.name not in ctx.rec_set:
            ctx.env[st.name] = float(ctx.plan.consts[st.name])
            continue
        v = ctx.recur(st) if st.name in ctx.rec_set else ctx.full(st.node)
        ctx.env[st.name] = v
        ctx.full_cache.clear()
    last: dict = {}
    for st in c.stmts:
        v = ctx.env[st.name]
        arr = ctx.arr(v)
        last[st.name] = arr[:, -1].copy()
        if st.name in keep:
            full[st.name] = arr
    verdict = ctx.truth(last[c.plot_name])
    return {"verdict": verdict, "last": last, "full": full}


def bars_matrix(store: dict, as_of, window: int, need_last: bool = True):
    """自家日线缓存(screen_asof.get_store 的结果)→ 右对齐的二维数组 + 代码顺序。

    → (codes, {"open": (N,W), ...}, short: 各票实际有几根)。只收「回溯日当天有收盘」的票,
    与时间回溯同口径;历史不足 window 根的左边补 NaN。
    """
    np = _np()
    from bisect import bisect_right
    codes: list = []
    mats = {k: [] for k in _PRICE}
    short: list = []
    for code, (dates, arr) in store["codes"].items():
        k = bisect_right(dates, as_of)
        if k == 0 or (need_last and dates[k - 1] != as_of):
            continue
        lo = max(0, k - window)
        seg = arr[lo:k]
        pad = window - seg.shape[0]
        ncol = arr.shape[1]
        cols = {"close": 0, "high": 1, "low": 2, "volume": 3, "open": 4 if ncol > 4 else None}
        for name, ci in cols.items():
            if ci is None:
                col = np.full(seg.shape[0], np.nan)
            else:
                col = seg[:, ci]
            if pad:
                col = np.concatenate([np.full(pad, np.nan), col])
            mats[name].append(col)
        codes.append(code)
        short.append(seg.shape[0])
    if not codes:
        return [], {k: np.zeros((0, window)) for k in _PRICE}, []
    return codes, {k: np.vstack(v) for k, v in mats.items()}, short
