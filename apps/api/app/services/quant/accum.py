"""资金逆势买入 · 大盘下跌时扣掉 beta 仍放量上涨 —— 纯计算,不连库不联网。

2026-09-14 用户(突破买入研究线):「我需要的抗跌不是这支股票特性就抗跌,我需要的是在近两三个月找到领头羊,
下跌时有大资金偷偷潜伏买入」。先做成突破买入 v12 的第 3 项评分,同日用户要求加进「即将突破」筛选器(v13)。
一份算法三处用:突破买入引擎(agent_breakout)、每晚落库(rs_history)、时间回溯(screen_asof) —— 别各写一份。

## 口径(写死,不开放参数)

1. beta:最近 `BETA_LOOK`=126 个交易日,个股日收益对基准(美股标普 500)日收益回归。
2. 最近 `LOOK`=42 个交易日(约两个月)里基准下跌的日子,个股同时满足
   ① 收盘上涨 ② 扣掉 beta 后仍多涨 > `EXC`=1%(个股涨幅 − beta × 基准涨幅)
   ③ 成交量 > 前 `VOL_BASE`=50 日均量 × `VOL`=1.2 → 记一天 → `acc_dn_days_42d`。
3. 这些基准下跌日扣 beta 后的平均超额(%)→ `acc_dn_excess_42d`;基准下跌日不足 3 天 → 空。
4. **扣 beta 是关键**:不扣时这类指标与 beta 相关 -0.36 ~ -0.45(量到的是低 beta 股性,不是资金),扣掉后 -0.13 ~ 0。
5. 「有资金逆势买入」写 `acc_dn_days_42d >= 1 and acc_dn_excess_42d >= 0`。
   1,654 次突破里不满足的 20 天中位 -1.0%、先跌 5% 55%,满足的 +0.3%、47%;天数多少分不出强弱 —— 只当门槛用。
   全市场 RS ≥ 70 里满足的 20 天超额中位 +0.9、不满足 -0.0(上下半年都正)。依据见 agent_breakout.py 文件头 v12 / v13。

## 算不出就是空

日线不足 `NEED`=127 根、窗口里任一天没有基准收盘或个股收盘缺 → 两个字段都空;
某天成交量或前 50 天里有缺 → 那天不计入天数(超额照算)。
"""
from __future__ import annotations

LOOK = 42
BETA_LOOK = 126
EXC = 0.01
VOL = 1.2
VOL_BASE = 50
MIN_DOWN_DAYS = 3
NEED = max(BETA_LOOK, LOOK + VOL_BASE) + 1
FIELDS = ("acc_dn_days_42d", "acc_dn_excess_42d")


def stats(bars: list[tuple], bench: dict | None) -> dict | None:
    """bars = [(d, c, h, l, v)] 升序,最后一根是今天;bench = {日期: 基准收盘}。
    → {beta, an, exc, dn};算不出 → None。exc 在基准下跌日不足 3 天时为 None。"""
    if not bench or not bars or len(bars) < NEED:
        return None
    seg = bars[-NEED:]
    r = []
    for b in seg:
        x = bench.get(b[0])
        if x is None or not x or not b[1] or b[1] <= 0:
            return None
        r.append(x)
    s = [b[1] for b in seg]
    v = [b[4] if len(b) > 4 else None for b in seg]
    sr = [s[k] / s[k - 1] - 1 for k in range(NEED - BETA_LOOK, NEED)]
    br = [r[k] / r[k - 1] - 1 for k in range(NEED - BETA_LOOK, NEED)]
    mb, ms = sum(br) / len(br), sum(sr) / len(sr)
    var = sum((x - mb) ** 2 for x in br)
    if not var:
        return None
    beta = sum((x - mb) * (y - ms) for x, y in zip(br, sr)) / var
    an = dn = 0
    exc = 0.0
    for k in range(NEED - LOOK, NEED):
        m = r[k] / r[k - 1] - 1
        if m >= 0:
            continue
        st = s[k] / s[k - 1] - 1
        dn += 1
        res = st - beta * m
        exc += res
        base = v[k - VOL_BASE:k]
        if v[k] is None or any(x is None for x in base):
            continue
        if res > EXC and st > 0 and v[k] > sum(base) / VOL_BASE * VOL:
            an += 1
    return {"beta": beta, "an": an, "exc": exc / dn * 100 if dn >= MIN_DOWN_DAYS else None, "dn": dn}


def fields(bars: list[tuple], bench: dict | None) -> dict:
    """筛选器用 → {acc_dn_days_42d, acc_dn_excess_42d};算不出的为 None。"""
    st = stats(bars, bench)
    if st is None or st["exc"] is None:
        return {FIELDS[0]: None, FIELDS[1]: None}
    return {FIELDS[0]: st["an"], FIELDS[1]: round(st["exc"], 3)}
