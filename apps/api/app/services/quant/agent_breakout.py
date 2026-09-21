"""小鹿智能体 · 突破买入线(2026-09-13 用户给的「Patrick Walker Style Complete Strategy」完整合并版脚本)。

用户给的是一份 ThinkScript 风格脚本:市场环境过滤 + 筛选(Clean Simple Base)+ 突破触发 + 两次加仓 +
部分止盈 + 三种止损 + 完全出场。这个引擎逐条移植,规则编号 P-xx(和 VCP 的 R/C/V、唐奇安的 D 不共用)。

## 筛选看前一天收盘,突破看今天(2026-09-13 用户拍板)

脚本字面是筛选和突破在**同一根 K 线**上判断。照字面跑 2025-09-12 ~ 2026-03-10:811 个突破日(P-06 成立)
**没有一个**同一天过得了筛选 —— 突破日本身把筛选破坏掉了:1 月振幅超过 12% 的 767 个(突破把区间撑大)、
10 日均量不再低于 50 日 × 0.9 的 729 个(1.5 倍放量把 10 日均量拉上去)、1 月 ≤ 3 月 × 0.55 的 613 个。
全年 0 笔,回测关会自动淘汰。筛选改看前一天收盘后同一段有 16 次信号。用户选了「筛选看前一天收盘」,
符合脚本「1. 筛选阶段 → 2. 突破触发阶段」的分段意图。**要改回同一天,先把上面这组数字给用户看。**

## 规则

- P-01 市场环境(今天):标普 500 收盘 > 50 日均线 > 200 日均线;不满足不开新仓(持仓按 P-15 退出)。
- P-02 价格与流动性(前一天收盘):收盘 > 20 美元,30 日均量 > 20 万股。
- P-03 趋势(前一天收盘):收盘 > 50 日 > 150 日 > 200 日均线;收盘 > 近 252 日最高价 × 0.80;RS ≥ 80。
        RS 用当天池子里的评级(池子不存前一天的 RS;一天之内评级变化很小)。
- P-04 整理形态(前一天收盘):3 个月振幅 12%~35%、1 个月振幅 3%~12%、1 月振幅 ≤ 3 月振幅 × 0.55、
        5 日振幅 ≤ 1 月振幅 × 0.65、近 21 日最低 > 近 63 日最低 × 1.01(振幅 = (最高 − 最低)÷ 最高)。
- P-05 枢轴与缩量(前一天收盘):距前一天的枢轴 −4% ~ +5%;10 日均量 < 50 日均量 × 0.9。
- P-06 突破触发(今天):枢轴 = 前一天为止的近 21 日最高价(脚本 `highest(high, 21)[1]`);
        收盘 > 枢轴,收盘 < 枢轴 × 1.05(不追高超过 5%),当天成交量 > 50 日均量 × 1.5。
        **脚本里的 `close > open`(阳线)没做:日线没有开盘价**(仓内 CLAUDE.md 小鹿第 1 条),
        不拿「收盘 > 昨收」之类顶替 —— 那是另一个条件。
- P-07 仓位:一个完整仓位 = 总资产 20%;首次买入完整仓位的 50%;最多 5 只。
- P-08 加仓:① 收盘 > 进场价 × 1.02、量 > 20 日均量 × 1.2、收盘 > EMA8、市场向上 → 加完整仓位的 30%;
        ② 收盘 > 进场价 × 1.05、量 > 20 日均量 × 1.1、收盘 > EMA21、市场向上 → 再加 20%。按顺序,一天最多加一次。
- P-09 止损 A:当天最低价 < 前一天为止近 21 日最低价 × 0.98(跌破 Base 低点)。
- P-10 止损 B:收盘 < 进场价 × 0.94(固定 6%)。
- P-11 止损 C:收盘 < EMA8 且收盘 < 进场价。
- P-12 部分止盈:浮盈 ≥ 8%,且收盘 < 前一天最高价、量 < 10 日均量 → 卖出当前持仓约 20%(每个持仓只做一次)。
- P-13 趋势出场:收盘 < EMA21 且浮盈 > 10%。
- P-14 趋势出场:收盘 < 50 日均线且浮盈 > 20%。
- P-15 市场转弱:P-01 不成立 → 清仓。
- P-16 护栏:与其他研究线同一套(单日权益 -3% 当天不开仓、连亏 3 笔停一天)。

## 移植时替用户做的决定(脚本没说清的地方,改之前先问用户)

1. **`entryprice()` = 第一次买入价**,加仓后不变。ThinkScript 里它可以是持仓均价;用首笔价时
   「+2% 加仓、+5% 再加、-6% 止损、浮盈 8%/10%/20%」都相对同一个锚点,和脚本注释的读法一致。
2. **「建议仓位 50% / +30% / +20%」的完整仓位没给大小**,取总资产 20%(和唐奇安线单股上限同一个数),最多 5 只。
3. **部分止盈每个持仓只做一次**。脚本的信号每根 K 线都可能为真,照字面每个「暂停」日都卖 20%,持仓会被切碎。
4. **止损 A 用当天最低价判断、按收盘价成交**。脚本写的是 `low <`;成交价统一收盘(和其他研究线同一口径,
   日线没有盘中路径)。所以止损 A 的实际成交价可能比 Base 低点还低不少,这是口径,不是 bug。
5. 均量窗口含当天(和扫描源 average_volume_Nd_calc 同口径);`[1]` 的两处(枢轴、Base 低点)不含当天。
6. 所有字段由引擎用自家日线精确算(扫描源没有 50 日 / 20 日均量);池子只做宽松预筛。

收盘后决策、信号当天收盘价成交。规则固定,满 30 笔前不优化(tunable 为空)。

## v2(2026-09-13 用户:「枢轴改成前浪高点也太过于绝对,应该是贴着浪高点的密集成交区,RS 降到 70」)

v1(枢轴 = 前一天为止 21 日最高、RS ≥ 80)一年:-4.72%、21 笔、胜 4 笔、每笔 -226 美元。用户看 CNO(4 月底)/ NATL(2 月底)
认为「即将突破」的位置没命中;诊断见仓内 CLAUDE.md 研究台第 8 条 (h)。v2 只改这两处(一次改两处是用户的要求,归因时记住):

- **枢轴 = 浪高点下方的密集成交区上沿**(`pivot_zone`):前一天为止 63 根里,浪高 = 最高价;在浪高下方 10% 以内,
  把每天的成交量按当天高低区间均摊到 0.5% 宽的价位格,取量最大的格,向两边扩到量 ≥ 峰值一半的相邻格 → 密集成交区。
  枢轴取区间**上沿**(站上它 = 冲出这片套牢盘)。区间量不到窗口总量 10% → 没有像样的密集区,枢轴为空、不买(不退回 21 日最高)。
  不取单根最高价:一根冲高回落的长上影就能把「前浪高点」抬高几个点,而那里几乎没成交,不是真阻力。
- **RS ≥ 70**(池子与 P-03 同步)。

v2 一年:+0.63%、48 笔、每笔 +12 美元、回撤 -7.94%。

## v3(2026-09-14 用户:「先改 5c 缩量为 1.0」)

诊断(去重后 1,728 次突破):5c「10 日均量 < 50 日均量 × 0.9」单独卡掉 88 次,那批票 10 日 / 50 日量比中位 1.02
—— 量能正常、没放大,不是在出货。放到 1.0 新增 33 次,突破后 20 天收盘中位 +2.0%(六条全过组 -0.1%)。
**只改这一个数**,其余与 v2 相同。之后 v4 再在 v3 基础上放宽 4b,分两轮跑是为了分清各自的作用。

## v4(2026-09-14 用户:「然后 4b 放到 15%」)

在 v3 基础上把 4b「1 个月振幅 3%~12%」的上限放到 15%。诊断:只差 P-04 的 161 次里 4b 没过 107 次,
那批票 1 个月振幅中位 14%;单独放到 15% 新增 12 次,突破后 20 天收盘中位 +5.5%(样本很小)。
「1 月 ≤ 3 月 × 0.55」没动 —— 放到 0.70 新增的 10 次 20 天中位 -7.3%,说明收缩这条有用。

v3 一年 -0.27%、63 笔、每笔 -6;v4 一年 -1.41%、74 笔、每笔 -21、回撤 -11.62%。三版里 EMA8 止损(P-11)的亏损 7,550 → 9,953 → 14,035。

## v5(2026-09-14 用户:「把 EMA8 止损改成更宽松的组合」,入场保持 v4)

用户选定(AskUserQuestion):**只换 EMA8 止损,其余出场照旧**;移动止盈「破 EMA10 卖一半、破 EMA20 清仓」;入场保持 v4。

- P-11 初始止损:进场价 − 1 × ATR(20),但止损距离最多 8%(止损价 = max(进场价 − ATR, 进场价 × 0.92));
        持有期最高收盘到过进场价 × 1.05 后,止损上移到**持仓均价**(加过仓的话均价比首笔高,这样才是真保本)。收盘跌破即出。
        ⚠ P-10 固定 6% 保留着,ATR 超过 6% 的票会先碰到 6%,8% 上限实际用不上 —— 用户选「其余照旧」时已提醒。
        ATR 算不出 → 不买(不拿 8% 顶替)。
- P-17 +20% 减半:收盘第一次到进场价 × 1.20,卖出一半(一次)。
- P-18 跌破 EMA10 减半:P-17 之后,收盘跌破 EMA10,卖出余仓一半(一次)。
- P-19 跌破 EMA20 清仓:P-17 之后,收盘跌破 EMA20,清仓。和 P-13(浮盈 10% 跌破 EMA21)几乎同一条线,
        两者同时成立时记 P-19(用户的新规则优先记账,动作一样都是清仓)。

v5 一年:+3.98%、回撤 -5.86%、62 笔(赚 18)、每笔 +63、盈亏比 1.19;1 ATR 初始止损距离中位只有 2.5%,
初始止损 30 笔 -13,831 仍是最大亏损来源。

## v6(2026-09-14 用户:「初始止损改为形态 + ATR 混合,取更合理的距离」,入场保持 v4/v5)

- P-11 初始止损距离 = max(形态止损距离, 1.5 × ATR20),上限 7%(超过就截在 7%,照样买)。
        形态止损 = **Base 低点(前一天为止 21 日最低)下方 1.5%**。用户原话「Base 低点(或密集成交区下沿)下方 1~1.5%」:
        取 1.5%(这次目标是别太紧);密集区下沿通常在 Base 低点上方、离进场价更近,套进 max() 后几乎总被 ATR / Base 低点盖过,不单独用。
        保本(+5% 移到均价)、+20% 减半、破 EMA10 再减半、破 EMA20 清仓不变。
- **去掉 P-09(Base 低点 2%)和 P-10(固定 6%)** —— 用户选的。它们和 7% 上限冲突:6% 会先卖掉,止损距离永远到不了 7%;
        P-09 每天按滚动 21 日低点重算、会跟着上移,可能比进场时定的止损先触发。新 P-11 已经包含 Base 低点与最大风险上限。

v6 一年:-2.30%、回撤 -7.50%、49 笔、每笔 -49。49 笔里 43 笔被截在 7%(21 日低点离突破收盘通常超过 7%),
等于固定 7% 止损;仓位不随止损距离变,止损放宽 3 倍、每笔最大亏损也放大 3 倍。

## v7(2026-09-14 用户:「先回退到 v5 的初始止损,加上入场前根据级别确认仓位」,入场筛选保持 v4/v5)

**止损回到 v5**:P-11 = max(进场价 − 1 × ATR20, 进场价 × 0.92),P-09 Base 低点 2%、P-10 固定 6% 加回来。

**P-20 入场前五项评分(满分 500)**:每项 S 100 / A 80 / B 60 / C 40 / D 0;总分 ≥350 S · ≥300 A · ≥250 B · ≥200 C · <200 D。
档位定仓(用户选「首次就买满这个比例,加仓另加」):**S / A / B / C 首次买入总资产 20 / 15 / 10 / 5%,D 不买**;
加仓仍按 P-08,股数 = 首次股数的 30% / 20%(S 级单票最多约 30%)。
1. 止损上方支撑(本引擎新写 `supports`):在「止损价 < 价位 < 收盘价」之间数有几**类**结构,≥4 S · 3 A · 2 B · 1 C · 0 D。
   - 均线:10 日 / 20 日均线、11 日 / 21 日加权均线(含今天)四条里至少两条落在区间内 → 算 1 类;
   - 前浪顶:今天之前 63 根里的 5 根摆动高点(比前后各 2 根都高);
   - 拒绝块(用户选「长下影」):当天振幅 ≥ 1 ATR 且收盘离最低价占振幅 ≥ 60%,支撑价 = 当天最低;
   - 缺口:向上跳空(当天最低 > 前一天最高)且到今天还没回补,支撑价 = 缺口下沿(前一天最高);
   - 关键 K 线(用户选「放量阳线」):成交量 ≥ 当时 50 日均量 2 倍、收盘在当天振幅上 1/3、收盘高于前一天,支撑价 = 当天最低。
   **没有开盘价**,拒绝块和关键 K 线只能用高 / 低 / 收 / 量认(用户确认过)。只看今天之前的 K 线 ——
   突破当天那根放量阳线必然满足「关键 K 线」,算进去等于人人白送 1 类。
2. 量价配合、3. 抗跌、5. MACD 金叉:直接用方向 C(agent_vcp3)的 `_vp_net` / `_defense` / `_macd_cross`,口径与用户这次给的一致
   (最近 63 个交易日;日线金叉最近 5 天内、周线最近 3 根周 K 内)。
4. 走廊:R = 收盘 − P-11 止损;走廊 = (上方 252 日强阻力 − 收盘)÷ R,阻力取「近 252 根最高价」与「1 年高成交量节点下沿」里近的
   (`agent_vcp3.res_above`,离收盘不足 0.5 ATR 的不算);上方没有阻力 = 走廊无上限记 S。≥5R S · ≥4R A · ≥3R B · ≥2R C。
**P-21 空间受限**:走廊不足 2R 直接不买(排在评分之前,不是记 0 分)。
算不出的项按 D 记 0 分并写原因(第 5 项没有 D,算不出记 C)。评分字段只在突破当天(P-06 成立)算,别的日子不花这份计算。

v7 一年:+2.77%、回撤 -6.35%、51 笔、每笔 +53(v5 +3.98%)。档位和盈亏不单调:S 13 笔平均 -130、A +478、B -27、C -117。
逐笔看 S 级:走廊 13 笔全是 S(一年新高附近上方没阻力)、量价 12 笔是 S —— 这两项人人高分,S 级靠它们堆出来;
全部 51 笔里量价 S 平均 -77、抗跌 S 0 笔赚钱;S 级 4 笔高出枢轴 ≥4% 全亏或打平;仓位 20% 又把亏损放大。

## v8(2026-09-14 用户:「按照 1、3 修改」)

1. **第 4 项「走廊」换成「追高幅度」**:收盘高出枢轴 ≤1% S · ≤2% A · ≤3% B · ≤4% C · 超过 4% D(追高上限本来是 5%)。
   依据:v5 的 62 笔高出枢轴 0~2% 平均 +181、2~5% 平均 -26。**P-21 空间受限(走廊 < 2R 不买)保留** —— 那是用户单列的规则,只是走廊不再计分。
2. **仓位统一,回到 v5**:完整仓位 = 总资产 20%,首次买一半,加仓按完整仓位的 30% / 20%。评分照算、档位照写进成交记录,
   **不再决定买多少**(`size_by_grade=False`;v7 的按档定仓留成开关)。**D 级仍然不买**(那是筛,不是定仓)。

v8 一年:-3.08%、回撤 -8.28%、47 笔、每笔 -67。和 v5 入场 / 止损 / 仓位一样,只多 P-21(挡 29 次)和 D 级不买(挡 10 次),
逐笔对照发现这两条正好挡掉 v5 最赚的 5 笔:ENVA / ORA / STT 被空间受限挡(走廊 0.6 / 1.7 / 1.1R,之后冲过前高走出大行情),
ECPG / R 被 D 级挡(追高 4.4% / 4.9% 记 D、量价 D)。

## v9(2026-09-14 用户:「先改成走廊不足 1R 不买,D 级不买只留一条:追高超过 4% 不买」)

- **P-21 空间受限放宽到 1R**(`corr_min` 2.0 → 1.0)。v8 挡掉的 ENVA(0.6R)仍会挡,ORA(1.7R)/ STT(1.1R)放行。
- **D 级不再拦人**(`block_d=False`,档位照记);**唯一的硬条件:收盘高出枢轴超过 4% 不买**(`chase_block`)。
  v8 里追高 > 4% 的 8 笔 0 笔赚钱、平均 -597。ECPG(4.4%)/ R(4.9%)在这条下仍然不买 —— 用户选的,读结果时记住。

v9 一年:+3.35%、回撤 -8.56%、50 笔、每笔 +65、盈亏比 1.23。

## v10(2026-09-14 用户:「针对量价配合这条规则,根据历史经验合理优化评分」)—— 只改第 2 项怎么定档,不影响买卖

原口径(63 天净次数 > 5 S · > 4 A · > 3 B · > 2 C)拿全年 1,654 次突破(去重、有 20 天后续)验证:S 513 次、D 880 次,
中间三档只有 261 次;S+A 与 C+D 突破后 20 天均值差 -0.4 个百分点 —— **完全分不出好坏**(63 天每天都计数,净次数动辄 ±10)。
试了净次数 / 比例 / 上涨量 ÷ 下跌量 / 放量日 / 21 · 42 天窗口,五等分都没有单调关系。只有两个方向一致的弱信号:
3 个月吸筹过多(上涨量 ÷ 下跌量最高 20% > 1.71、放量上涨 − 放量下跌 ≥ 7)突破后 20 天中位 -1.0%(涨过头);
近 21 天一个净放量上涨日都没有,20 天内涨过 10% 只有 23%(其他 28~34%)。

**新口径(`vp_stats` + `vp_tier`)**:
- 近 21 天「放量上涨天数 − 放量下跌天数」(放量 = 成交量 > 50 日均量 × 1.2):≥4 S · 3 A · 2 B · 1 C · ≤0 D;
- 3 个月过热降一档:近 63 天上涨日总量 ÷ 下跌日总量 > 1.7,或近 63 天放量上涨 − 放量下跌 ≥ 8。
同一批事件:S 154 次 20 天均值 +4.0%、涨过 10% 40%;D 531 次 -0.3%、26%;S+A 对 C+D 均值差 +1.0、涨过 10% 差 +3 个百分点(原口径 -0.4 / +1)。
**中间档(A / B / C)仍然分不开,C 反而比 A、B 好** —— 能说清楚的只有两头。门槛是在同一年上挑的,有贴合这一年的风险;
评分只记录、不影响买卖(v8 起),所以这次改动不改变任何成交,只改成交记录里的档位。

## v11(2026-09-14 用户:「针对抗跌这条规则也按历史数据优化评分」)—— 只改第 3 项怎么定档,不影响买卖

原口径(63 天里标普下跌日这只票不跌的次数 > 15 S · > 10 A · > 5 B · > 2 C)拿同一批 1,654 次突破验证:**两头差、中间好** ——
S 203 次突破后 20 天中位 -0.8%、涨过 10% 只有 26%;成交里 S 9 笔平均 -256、B 16 笔 +412。不跌次数与 beta 相关 -0.53:
「抗跌强」很大程度就是低波动、不跟大盘动的防御股,突破后没人跟进。试了全部下跌日比例 / 下跌捕获率 / 下跌日超额收益 /
上涨捕获率 / beta / 波动率 / 21 天窗口,最有区分度的是**只看标普大跌日(≤ -1%)不跌的比例,而且是倒 U 形**:
最低 20%(≤ 11%)20 天中位 -2.0%、先跌 5% 57%;17~33% 中位 +2.4%、先跌 5% 41%;最高 20%(> 50%)中位 -0.9%、涨过 10% 只有 20%。
大跌日能扛住一部分是强势;每次大跌都不跌的,是防御股。

**新口径(`def_stats` + `def_tier`)**:
- 近 63 天标普大跌日 ≥ 3 天:大跌日不跌比例 17%~34% S · 34%~51% A · 10%~17% B · > 51% C · < 10% D;
- 大跌日不足 3 天(行情平静,约 14% 的突破):改看全部下跌日不跌比例 38%~52% S · 32%~38% A · ≥ 52% B · 25%~32% C · < 25% D。
同一批事件:S 479 / A 326 / B 374 / C 247 / D 228 次,20 天中位 +1.5 / +0.2 / +0.1 / -0.6 / -3.5%(单调),先跌 5% 42% → 59%;
S+A 对 C+D 中位差 +2.7、先跌 5% 比例差 -12 个百分点(原口径中位差 +1.6、均值差 -1.1)。v9/v10 成交:S 18 笔 +330、C 8 笔 -270、D 5 笔 -278。
另一套「平静行情一律记 C」在事件上两头更开(中位差 +3.2),但成交上不单调,且把 229 次平静行情硬归一档,没选。
比例是小分数(大跌日中位 7 天):1/6 = 16.7% 落 B、1/3 落 S、1/2 落 A —— 边界故意避开这些值的正中。门槛在同一年上挑,有贴合风险。
**v11 口径已被 v12 取代**(用户否了:量的是股性,不是资金),上面留作记录。

## v12(2026-09-14 用户:「我需要的抗跌不是这支股票特性就抗跌,我需要的是在近两三个月找到领头羊,下跌时有大资金偷偷潜伏买入……我是波段投资者」)

用户要的第 3 项是**资金行为**:大盘下跌时有大资金逆势买入;并希望看个股均线是否领先大盘(比大盘先完成回调、先开始上涨、领先同类股)。
在同一批 1,654 次突破上验证(与 beta 相关系数、按 beta 三组、上下半年分开都看了):
- **资金逆势买入(`accum_stats`)**:近 42 天标普下跌日里,个股收盘上涨、扣掉 beta 后仍多涨 > 1%、成交量 > 50 日均量 × 1.2 的天数(an),
  及这些下跌日扣 beta 后的平均超额(exc)。**不扣 beta 时指标与 beta 相关 -0.36 ~ -0.45(量到的是低 beta 股性),扣掉后 -0.13 ~ 0**。
  an = 0 或 exc < 0 的 489 次:20 天中位 -1.0%、先跌 5% 55%;其余 1,165 次 +0.3%、47%;高 beta 组里前者 -3.0%、65%;上下半年都成立。
  **但痕迹越多并不越好**(五档 S +0.2% / A -0.4% / B +0.9%,不单调)—— 日线只能看出「完全没有」,看不出强弱。
- **均线领先大盘**(大盘跌破 10 日线时守 5 日线、20 日线斜率领先、比大盘先见底、先站上 20 日线)、**领先同板块**(21 天涨幅板块百分位):
  与 20 天收益相关 -0.06 ~ +0.05,分档不单调;「先见底」看着有效是因为大盘刚回调过(大盘回调过的 710 次里早 0 天与早 30 天中位都在 +2% 左右)。
  六条合成信号数上半年 4~6 个最差、下半年最好 —— 不稳定。原因大概是突破池已卡 RS ≥ 70、贴前高,进来的已是领头羊,再比谁更领先分不开。

**现口径(`acc_tier`)**:an ≥ 1 且 exc ≥ 0 → B;否则 D(不设 S / A,数据不支持)。
均线领先大盘四项由 `lead_stats` 算出,**只写进第 3 项说明,不参与定档**,方便对照交易清单。领先同板块引擎里拿不到板块指数,没写。
第 3 项满分从 100 变 60,汇总档普遍下移。评分只记录、不影响买卖。

## v13(2026-09-14 用户:「把资金逆势买入加进即将突破筛选器,重跑一年」)—— 这次**改变买卖**

依据:全市场选股层面(38 个抽样日 × 过 P-02 的 59,234 条),RS ≥ 70 里满足的 20 天超额中位 +0.9、不满足 -0.0,上下半年都为正;
均线领先大盘几条在同一层面无效或略反,所以只加资金逆势买入。
- 算法挪到 `accum.py`(本引擎、每晚落库、时间回溯共用一份),筛选器新字段 `acc_dn_days_42d` / `acc_dn_excess_42d`;
- 预筛池脚本(POOL_SCRIPT)加 `acc_dn_days_42d >= 1 and acc_dn_excess_42d >= 0`(按当天收盘圈池);
- 引擎加 **P-22**:按**前一天收盘**核对同一条件(与 P-02 ~ P-05 同一口径,突破当天那根放量不算进去),算不出不买;
  开关 `acc_filter`(关掉 = 回到 v12 买卖);
- 用户保存的「即将突破」(user_screen_preset)同步加这条。

## v14(2026-09-15 用户:「财报日前 5 个交易日内不得买入或加仓;已经买入的,财报前 2 天浮盈不大于 10% 清仓,
## 大于 10% 卖出一半,后续仓位沿用之前的止损止盈规则」)—— 改变买卖

财报日来自 Nasdaq 官方日历(`earnings_dates.py`,口径与「知道 / 不知道」的区分写在那边)。
- **P-23 财报前不买**:下一次财报日离今天 ≤ 5 个交易日(含财报当天)不开新仓,也不加仓(P-08)。
  「还有 n 个交易日」= 今天之后到财报日(含)的交易日数,财报当天 = 0。含当天是因为盘前 / 盘后分不出(过去日期都没给)。
  **日历缺(往后 10 天里有没拉到的日子)也不买、不加仓** —— 不知道就当有风险,不当成「没有财报」。
- **P-24 财报前减仓**:离财报 ≤ 2 个交易日(正常就是财报前第 2 个交易日收盘)、这次财报还没处理过:
  浮盈 ≤ 10% 清仓;> 10% 卖出一半,余仓照原来的止损止盈走(每次财报只做一次,`extra.earn_done` 记财报日)。
  排在完全出场(止损 / 趋势 / 大盘转弱)之后:同一天止损也成立时记止损。减半当天不再做别的减仓。
- 替用户定的两处(改之前先问):**浮盈按首笔进场价算**(和 P-12 / P-13 / P-14 的「浮盈」同一锚点,决定 1),
  说明里同时写出按持仓均价算的数;余仓只剩 1 股时「卖一半」取整为 0,整股清掉。
- 开关 `earn_filter`(关掉 = 回到 v13 买卖)。持仓离财报时日历缺,P-24 做不了,不猜。

v14 一年:+9.43%、回撤 -2.81%、26 笔、每笔 +363(v13 +8.22%、-4.97%、34 笔、+240)。P-23 挡掉 v13 的 9 笔合计 -1,900;
P-24 只触发 2 次(NXT / ORA 浮盈 > 10% 卖一半),财报后两只都涨约 9%,少赚 878。
"""
from __future__ import annotations

import math

from app.services.quant import accum
from app.services.quant import earnings_dates as ed
from app.services.quant import agent_vcp as av
from app.services.quant import agent_vcp3 as c3

MARKET_KEY = "__market__"
SECTORS_KEY = "__sectors__"          # agent_run.ensure_cache 按有 MARKET_KEY 的引擎一起写;本引擎不用板块
EARNINGS_KEY = "__earnings__"        # v14:按日的财报日视图(earnings_dates.EarningsView),agent_run.ensure_cache 写
MIN_BARS = 60                        # 持仓管理(EMA21 / 50 日线 / 21 日低点)要的最少根数
SCREEN_BARS = 252                    # 筛选要近 252 日最高价

PARAMS = {
    "price_min": 20.0, "liq_min": 200_000.0, "near_high": 0.80, "rs_min": 70,      # v2:80 → 70
    "zone_lookback": 63, "zone_depth": 0.10, "zone_bin": 0.005, "zone_keep": 0.5, "zone_min_share": 0.10,
    "depth_min": 0.12, "depth_max": 0.35, "tight1m_min": 0.03, "tight1m_max": 0.15,   # v4:0.12 → 0.15
    "shrink": 0.55, "tight5d": 0.65, "higher_low": 1.01,
    "pivot_lo": -4.0, "pivot_hi": 5.0, "vdry": 1.0,                                   # v3:0.9 → 1.0
    "extend": 1.05, "vol_surge": 1.5,
    "add1_pct": 1.02, "add1_vol": 1.2, "add2_pct": 1.05, "add2_vol": 1.1,
    "base_stop": 0.98, "fixed_stop": 0.94,
    "stop_atr": 1.0, "stop_cap": 0.08,                                                 # v7 回到 v5:1 ATR,最多 8%
    "be_trigger": 1.05, "half_trigger": 1.20,                                          # v5 起:保本 / 20% 减半
    "grade_pct": {"S": 0.20, "A": 0.15, "B": 0.10, "C": 0.05},                         # v7:档位 → 首次买入占总资产
    "size_by_grade": False,                                                            # v8:关 = 统一仓位(v5),评分只记录
    "corr_min": 1.0, "sup_look": 63, "rej_close_pos": 0.6, "key_vol": 2.0,             # v9:空间受限 2R → 1R;支撑结构口径
    "chase_block": 4.0, "block_d": False,                                              # v9:追高 > 4% 不买;D 级不再拦人
    "acc_filter": True,                                                                # v13:P-22 前一天收盘要有资金逆势买入
    "earn_filter": True, "earn_block_days": 5, "earn_exit_days": 2,                    # v14:P-23 财报前 5 个交易日不买不加仓
    "earn_keep_profit": 10.0, "earn_keep_frac": 0.5,                                   # v14:P-24 财报前 2 天,浮盈 > 10% 卖一半,否则清仓
    "partial_profit": 8.0, "partial_frac": 0.20,
    "exit_ema21_profit": 10.0, "exit_sma50_profit": 20.0,
    "unit_pct": 0.20, "initial_frac": 0.50, "add1_frac": 0.30, "add2_frac": 0.20, "max_holdings": 5,
    "watch_pool_days": 1,
}
STOP_KEYS = ("fixed_stop",)
SUP_MIN = {"S": 4, "A": 3, "B": 2, "C": 1}      # 第 1 项:支撑结构类数的档位下限
CHASE_MAX = {"S": 1.0, "A": 2.0, "B": 3.0, "C": 4.0}   # v8 第 4 项:收盘高出枢轴 % 的档位上限;超过 C = D
VP_BIG = 1.2          # v10 第 2 项:放量 = 成交量 > 50 日均量 × 1.2
VP_HOT_UDV = 1.7      # v10:近 63 天上涨日总量 ÷ 下跌日总量 > 1.7 → 过热降一档
VP_HOT_AD = 8         # v10:近 63 天放量上涨 − 放量下跌 ≥ 8 → 过热降一档
ACC_LOOK = accum.LOOK   # v12 第 3 项 / v13 P-22:资金逆势买入口径全在 accum.py
GRADE_RULE = "P-20"
ENTRY_RULE = "P-06"
ADD_RULE = "P-08"
WATCH_POOL_DAYS = 1                  # 突破是当天的事;筛选条件由引擎按前一天收盘重算,池子只要当天的
# 悬停日K 的「形态就绪」那层(2026-09-15 用户要求蓝线分三层:候选池 / 形态就绪 / 买入条件全满足):
# 这几条全过、只差突破(P-06)与市场(P-01)的日子。声明了 SETUP_RULES 的引擎,watch_item 必须输出 fails,
# entry_checks 必须是 (ind, p, score, market) 签名 —— agent_run.backfill_watch_fails 按它回填老记录
SETUP_RULES = ("P-02", "P-03", "P-04", "P-05", "P-22")
SETUP_LABEL = "形态就绪"
SETUP_NOTE = "P-02~P-05 + P-22 全过,只差突破 P-06 与市场 P-01"

POOL = "breakout"
POOL_LIMIT = 800
POOL_LABEL = "突破买入预筛池(收盘 > 20 · 30 日均量 > 20 万 · 均线多头 · 距 52 周高点 20% 以内 · RS ≥ 70 · 近两个月有资金逆势买入)"
POOL_SCRIPT = """# ===== 突破买入预筛池(小鹿 · 突破买入线)=====
# 整理形态 / 枢轴 / 50 日均量 / 突破由引擎用日线精确算;这里只圈脚本里扫描源能算的那几条
def c_price = close > 20;
def c_liq   = average_volume_30d_calc > 200000;
def c_trend = close > SMA50 and SMA50 > SMA150 and SMA150 > SMA200;
def c_near  = close > price_52_week_high * 0.80;
def c_rs    = rs_rating >= 70;
# v13:近 42 天大盘下跌时,扣 beta 后逆势放量超额上涨至少 1 天,且下跌日平均超额不为负(口径见 accum.py)
def c_acc   = acc_dn_days_42d >= 1 and acc_dn_excess_42d >= 0;

plot scan = c_price and c_liq and c_trend and c_near and c_rs and c_acc;
"""
EXEC_NOTE = "纸上交易 · 日线收盘价成交(筛选看前一天收盘、突破看当天;没有开盘价,阳线条件未实现)"

RULES = [
    {"id": "P-01", "kind": "risk", "condition": "市场环境(当天):标普 500 收盘 > 50 日均线 > 200 日均线;不满足不开新仓"},
    {"id": "P-02", "kind": "buy", "condition": "价格与流动性(前一天收盘):收盘 > $20,30 日均量 > 20 万股"},
    {"id": "P-03", "kind": "buy", "condition": "趋势(前一天收盘):收盘 > 50 日 > 150 日 > 200 日均线;收盘 > 近 252 日最高 × 0.80;RS ≥ 70"},
    {"id": "P-04", "kind": "buy", "condition": "整理形态(前一天收盘):3 月振幅 12%~35%、1 月振幅 3%~15%、1 月 ≤ 3 月 × 0.55、5 日 ≤ 1 月 × 0.65、21 日最低 > 63 日最低 × 1.01"},
    {"id": "P-05", "kind": "buy", "condition": "枢轴与缩量(前一天收盘):枢轴 = 近 63 日浪高点下方 10% 内的密集成交区上沿;距枢轴 −4% ~ +5%;10 日均量 < 50 日均量 × 1.0"},
    {"id": "P-06", "kind": "buy", "condition": "突破触发(当天):收盘 > 枢轴(密集成交区上沿)、收盘 < 枢轴 × 1.05、成交量 > 50 日均量 × 1.5(阳线条件没做:日线没有开盘价)"},
    {"id": "P-07", "kind": "risk", "condition": "仓位:完整仓位 = 总资产 20%,首次买入 50%;加仓按完整仓位的 30% / 20%;最多 5 只(P-20 评分只记录,不影响仓位,也不拦人)"},
    {"id": "P-08", "kind": "buy", "condition": "加仓:收盘 > 进场价 × 1.02、量 > 20 日均量 × 1.2、收盘 > EMA8、市场向上 → +30%;收盘 > 进场价 × 1.05、量 > 20 日均量 × 1.1、收盘 > EMA21 → 再 +20%"},
    {"id": "P-09", "kind": "sell", "condition": "止损 A:当天最低价 < 前一天为止近 21 日最低 × 0.98(跌破 Base 低点),按收盘价出"},
    {"id": "P-10", "kind": "sell", "condition": "止损 B:收盘 < 进场价 × 0.94(固定 6%)"},
    {"id": "P-11", "kind": "sell", "condition": "初始止损:进场价 − 1 × ATR(20),止损距离最多 8%;最高收盘到过进场价 × 1.05 后上移到持仓均价(保本);收盘跌破即出"},
    {"id": "P-12", "kind": "sell", "condition": "部分止盈:浮盈 ≥ 8% 且收盘 < 前一天最高、量 < 10 日均量 → 卖出约 20%(每个持仓一次)"},
    {"id": "P-13", "kind": "sell", "condition": "趋势出场:收盘 < EMA21 且浮盈 > 10%"},
    {"id": "P-14", "kind": "sell", "condition": "趋势出场:收盘 < 50 日均线且浮盈 > 20%"},
    {"id": "P-15", "kind": "sell", "condition": "市场转弱:标普 500 不再满足 收盘 > 50 日 > 200 日 → 清仓"},
    {"id": "P-16", "kind": "risk", "condition": "护栏:单日权益回撤达 -3% 当天停止开仓;连亏 3 笔后下一个交易日不开仓"},
    {"id": "P-17", "kind": "sell", "condition": "+20% 减半:收盘第一次到进场价 × 1.20,卖出一半(一次)"},
    {"id": "P-18", "kind": "sell", "condition": "移动止盈:+20% 减半之后,收盘跌破 EMA10 卖出余仓一半(一次)"},
    {"id": "P-19", "kind": "sell", "condition": "移动止盈:+20% 减半之后,收盘跌破 EMA20 清仓"},
    {"id": "P-22", "kind": "buy", "condition": "资金逆势买入(前一天收盘):近 42 天标普下跌日里,个股上涨、扣 beta 后多涨 > 1%、成交量 > 50 日均量 × 1.2 至少 1 天,且这些下跌日扣 beta 平均超额 ≥ 0;算不出不买"},
    {"id": "P-20", "kind": "risk", "condition": "入场评分(满分 500,每项 S100/A80/B60/C40/D0):止损上方支撑 · 量价配合(近 21 天放量上涨 − 放量下跌 ≥4 S · 3 A · 2 B · 1 C · ≤0 D,3 个月过热降一档) · 抗跌 = 资金逆势买入(近 42 天标普下跌日,个股上涨、扣 beta 后多涨 >1%、放量 1.2 倍,有 ≥1 天且下跌日平均超额 ≥0 记 B,否则 D;均线领先大盘只记录) · 追高幅度(高出枢轴 ≤1% S · ≤2% A · ≤3% B · ≤4% C) · 日 / 周 MACD 金叉;≥350 S · ≥300 A · ≥250 B · ≥200 C · 其余 D;档位只记录,不定仓、不拦人;唯一硬条件:收盘高出枢轴超过 4% 不买"},
    {"id": "P-21", "kind": "risk", "condition": "空间受限:走廊(上方 252 日强阻力 − 收盘)÷ R 不足 1R,达到买点也不进"},
    {"id": "P-23", "kind": "risk", "condition": "财报前不买:下一次财报日离今天 5 个交易日以内(含财报当天)不开新仓、不加仓;财报日历没拉到(不知道)也不买、不加仓"},
    {"id": "P-24", "kind": "sell", "condition": "财报前减仓:离财报 2 个交易日时,浮盈(相对首笔进场价)不超过 10% 清仓;超过 10% 卖出一半,余仓照原来的止损止盈走(每次财报只做一次)"},
]
RULE_NAME = {"P-06": "枢轴突破买入", "P-08": "加仓", "P-09": "跌破 Base 低点", "P-10": "固定 6% 止损",
             "P-11": "ATR 止损 / 保本", "P-12": "部分止盈", "P-13": "跌破 EMA21", "P-14": "跌破 50 日线",
             "P-15": "市场转弱", "P-17": "+20% 减半", "P-18": "跌破 EMA10 减半", "P-19": "跌破 EMA20 清仓",
             "P-23": "财报前不买", "P-24": "财报前减仓"}
RULE_PARAM_KEY: dict = {}            # 规则固定,不进优化器


# 指标落库(agent_store.agent_ind_cache)的版本。**改了 indicators / _core / pivot_zone / grade_features / supports /
# vp_stats / accum 的算法,把基准号改掉**;指标里用到的参数值进哈希,改参数自动换版本、不会读到旧口径
IND_BASE = "2026-09-15"
IND_PARAM_KEYS = ("zone_lookback", "zone_depth", "zone_bin", "zone_keep", "zone_min_share", "extend", "vol_surge",
                  "stop_atr", "stop_cap", "sup_look", "rej_close_pos", "key_vol")


def ind_version(p: dict = PARAMS) -> str:
    import hashlib
    import json as _json
    h = hashlib.md5(_json.dumps({k: p[k] for k in IND_PARAM_KEYS}, sort_keys=True).encode()).hexdigest()[:8]
    return f"{IND_BASE}:{h}"


def rules_for(p: dict = PARAMS) -> list[dict]:
    return [dict(r) for r in RULES]


def summary(p: dict = PARAMS) -> str:
    return ("Patrick Walker 风格突破:标普在 50 日 > 200 日之上才做;前一天收盘时已是均线多头、离一年高点 20% 以内、RS ≥ 70、"
            "3 个月 → 1 个月 → 5 天振幅逐级收紧、低点抬高、量能干燥的票,今天收盘放量(> 1.5 倍 50 日均量)站上浪高点下方密集成交区的上沿、"
            "且不超过 4% 时(追高超过 4% 不买),走廊不足 1R 不买;五项评分只记录档位;买入完整仓位(总资产 20%)的一半;"
            "涨 2% / 5% 且放量站上 EMA8 / EMA21 各加 30% / 20%;"
            "跌破 Base 低点 2%、亏 6%、或跌破 1 倍 ATR 初始止损(最多 8%)出场,最高到过 +5% 后止损上移到均价保本;"
            "浮盈 8% 后遇暂停卖 20%,+20% 减半,之后跌破 EMA10 再减半、跌破 EMA20 清仓;"
            "浮盈 10% 跌破 EMA21、浮盈 20% 跌破 50 日线、或大盘转弱时清仓;"
            "财报前 5 个交易日内不买不加仓(财报日历没拉到也不买),离财报 2 个交易日时浮盈不超过 10% 清仓、超过 10% 卖一半。")


# ═══════════════════════════════════════════════════════════════
# 市场环境 · 指标
# ═══════════════════════════════════════════════════════════════

def market_regime(bars: list[tuple], p: dict = PARAMS) -> dict:
    """基准日线 [(日期, 收, 高, 低, 量)] 截到当天 → {ok, above, text}。算不出时 above = None(不当成转弱)。"""
    if len(bars) < 200:
        return {"ok": False, "above": None, "text": f"基准日线只有 {len(bars)} 根,不足 200,市场环境算不出,不开新仓"}
    c = [b[1] for b in bars]
    s50, s200 = av._sma(c, 50), av._sma(c, 200)
    above = c[-1] > s50 and s50 > s200
    return {"ok": above, "above": above, "close": c[-1], "sma50": s50, "sma200": s200,
            "text": f"标普 {c[-1]:.0f} · 50 日 {s50:.0f} · 200 日 {s200:.0f}" + (" —— 上升趋势" if above else " —— 不在上升趋势")}


def _mean(xs):
    return sum(xs) / len(xs) if xs and all(x is not None for x in xs) else None


def pivot_zone(bars: list[tuple], p: dict = PARAMS) -> dict | None:
    """浪高点下方的密集成交区(不含最后一根)→ {low, high, wave_high, share};算不出或不像样 → None。

    窗口 = 最后一根之前的 zone_lookback 根。浪高 = 窗口最高价;只看浪高下方 zone_depth 以内的价位,
    每天的量按当天 [最低, 最高] 均摊到 zone_bin 宽的价位格(当天区间超出这段价位的部分不计),
    取量最大的格,向两边扩到量 ≥ 峰值 × zone_keep 的相邻格。区间量占窗口总量 < zone_min_share → None。"""
    n = int(p["zone_lookback"])
    win = bars[-n - 1:-1]
    if len(win) < n or any(b[2] is None or b[3] is None or b[4] is None for b in win):
        return None
    wave = max(b[2] for b in win)
    floor = wave * (1 - p["zone_depth"])
    step = wave * p["zone_bin"]
    nb = max(1, int(math.ceil((wave - floor) / step - 1e-9)))
    vol = [0.0] * nb
    total = 0.0
    for _d, c, hi, lo, v in win:
        total += v
        top, bot = min(hi, wave), max(lo, floor)
        if top < bot:
            continue
        if hi <= lo:
            vol[min(nb - 1, max(0, int((c - floor) / step)))] += v
            continue
        per = v / (hi - lo)
        for k in range(max(0, int((bot - floor) / step)), min(nb - 1, int((top - floor) / step)) + 1):
            a = floor + k * step
            ov = min(a + step, top) - max(a, bot)
            if ov > 0:
                vol[k] += per * ov
    pk = max(range(nb), key=lambda k: vol[k])
    if vol[pk] <= 0 or total <= 0:
        return None
    i0 = i1 = pk
    while i0 > 0 and vol[i0 - 1] >= vol[pk] * p["zone_keep"]:
        i0 -= 1
    while i1 < nb - 1 and vol[i1 + 1] >= vol[pk] * p["zone_keep"]:
        i1 += 1
    share = sum(vol[i0:i1 + 1]) / total
    if share < p["zone_min_share"]:
        return None
    return {"low": floor + i0 * step, "high": min(wave, floor + (i1 + 1) * step), "wave_high": wave, "share": share}


def _core(bars: list[tuple]) -> dict | None:
    """一天的字段。持仓管理要的只要 60 根;筛选字段要 252 根且窗口里没有缺值,算不出时 screen = None(不猜)。"""
    n = len(bars)
    if n < MIN_BARS:
        return None
    c = [b[1] for b in bars]
    h = [b[2] for b in bars]
    lo = [b[3] for b in bars]
    v = [b[4] for b in bars]
    if any(x is None for x in h[-23:] + lo[-23:] + v[-50:]):
        return None
    zone = pivot_zone(bars)
    ind = {
        "close": c[-1], "high": h[-1], "low": lo[-1], "volume": v[-1], "prev_high": h[-2],
        "pivot": zone["high"] if zone else None, "zone": zone, "base_low": min(lo[-22:-1]),
        "av10": _mean(v[-10:]), "av20": _mean(v[-20:]), "av50": _mean(v[-50:]),
        "ema8": av._ema(c[-160:], 8), "ema21": av._ema(c[-160:], 21),
        "ema10": av._ema(c[-160:], 10), "ema20": av._ema(c[-160:], 20), "atr20": av._atr(bars[-61:], 20),
        "sma50": av._sma(c, 50),
        "screen": None,
    }
    if n >= SCREEN_BARS and not any(x is None for x in h[-SCREEN_BARS:] + lo[-63:] + v[-30:]):
        hi63, lo63 = max(h[-63:]), min(lo[-63:])
        hi21, lo21 = max(h[-21:]), min(lo[-21:])
        hi5, lo5 = max(h[-5:]), min(lo[-5:])
        ind["screen"] = {
            "sma150": av._sma(c, 150), "sma200": av._sma(c, 200), "hi252": max(h[-SCREEN_BARS:]),
            "av30": _mean(v[-30:]),
            "rng3m": (hi63 - lo63) / hi63 if hi63 else None,
            "rng1m": (hi21 - lo21) / hi21 if hi21 else None,
            "rng5d": (hi5 - lo5) / hi5 if hi5 else None,
            "low21": lo21, "low63": lo63,
        }
    return ind


def indicators(bars: list[tuple], p: dict = PARAMS, bench: dict | None = None) -> dict | None:
    """bars = [(d, c, h, l, v)] 升序,最后一根是今天。→ 今天的字段 + prev(前一天收盘的字段,筛选用)
    + gf(突破当天才算的五项评分特征)。"""
    ind = _core(bars)
    if ind is not None:
        prev = _core(bars[:-1])
        if prev is not None:
            prev.pop("prev", None)
            prev["acc"] = accum.stats(bars[:-1], bench)        # v13 P-22 看前一天收盘
        ind["prev"] = prev
        ind["gf"] = None
        if ind.get("atr20") and trigger_check(ind, p)["ok"]:
            ind["gf"] = grade_features(bars, ind, bench, p)
    return ind


# ═══════════════════════════════════════════════════════════════
# v7 入场评分(P-20)与空间受限(P-21)
# ═══════════════════════════════════════════════════════════════

def stop_of(px: float, atr: float, p: dict = PARAMS) -> float:
    """P-11 初始止损价 = max(收盘 − stop_atr × ATR, 收盘 × (1 − stop_cap))。"""
    return max(px - p["stop_atr"] * atr, px * (1 - p["stop_cap"]))


def _wma(xs: list[float], n: int) -> float | None:
    if len(xs) < n:
        return None
    return sum(x * k for x, k in zip(xs[-n:], range(1, n + 1))) / (n * (n + 1) / 2)


def supports(bars: list[tuple], stop: float, p: dict = PARAMS) -> dict | None:
    """第 1 项 · 止损价与收盘价之间的支撑结构 → {count, items:[(类, 价, 说明)], text};日线不够 → None。
    均线含今天;前浪顶 / 拒绝块 / 缺口 / 关键 K 线只看今天之前 sup_look 根。每类最多算 1 个。"""
    look = int(p["sup_look"])
    n = len(bars)
    if n < look + 52:
        return None
    c = [b[1] for b in bars]
    h = [b[2] for b in bars]
    lo = [b[3] for b in bars]
    v = [b[4] for b in bars]
    if any(x is None for x in h[-look - 2:] + lo[-look - 2:] + v[-look - 51:]):
        return None
    px = c[-1]

    def between(x):
        return x is not None and stop < x < px

    items = []
    mas = [("10 日均线", av._sma(c, 10)), ("20 日均线", av._sma(c, 20)), ("11 日加权", _wma(c, 11)), ("21 日加权", _wma(c, 21))]
    inb = [(k, x) for k, x in mas if between(x)]
    if len(inb) >= 2:
        items.append(("均线", max(x for _k, x in inb), "、".join(f"{k} ${x:.2f}" for k, x in inb)))
    i0 = n - 1 - look                                   # 窗口 = [i0, n-2](不含今天)
    tops = [h[i] for i in range(max(i0, 2), n - 3) if h[i] > max(h[i - 2:i] + h[i + 1:i + 3]) and between(h[i])]
    if tops:
        items.append(("前浪顶", max(tops), f"摆动高点 ${max(tops):.2f}"))
    atr = av._atr(bars[-61:], 20)
    rej, key, gaps = [], [], []
    for i in range(max(i0, 50), n - 1):
        rng = h[i] - lo[i]
        pos_ = (c[i] - lo[i]) / rng if rng > 0 else None
        if atr and rng >= atr and pos_ is not None and pos_ >= p["rej_close_pos"] and between(lo[i]):
            rej.append(lo[i])
        a50 = sum(v[i - 49:i + 1]) / 50
        if a50 and v[i] >= p["key_vol"] * a50 and pos_ is not None and pos_ >= 2 / 3 and c[i] > c[i - 1] and between(lo[i]):
            key.append(lo[i])
        if lo[i] > h[i - 1] and min(lo[i:]) > h[i - 1] and between(h[i - 1]):
            gaps.append(h[i - 1])
    if rej:
        items.append(("拒绝块", max(rej), f"长下影低点 ${max(rej):.2f}"))
    if gaps:
        items.append(("缺口", max(gaps), f"未回补缺口下沿 ${max(gaps):.2f}"))
    if key:
        items.append(("关键 K 线", max(key), f"放量阳线低点 ${max(key):.2f}"))
    text = (f"止损 ${stop:.2f} ~ 收盘 ${px:.2f} 之间 {len(items)} 类:" + ";".join(f"{a}({t})" for a, _x, t in items)
            if items else f"止损 ${stop:.2f} ~ 收盘 ${px:.2f} 之间没有支撑结构")
    return {"count": len(items), "items": items, "text": text}


def vp_stats(bars: list[tuple]) -> dict | None:
    """第 2 项 · 量价口径(v10)→ {acc21, dist21, ad21, ad63, udv63};日线不足 113 根或缺量 → None。
    放量 = 成交量 > 当天为止 50 日均量 × VP_BIG;上涨 / 下跌按收盘比前一天。"""
    c = [b[1] for b in bars]
    v = [b[4] for b in bars]
    n = len(c)
    if n < 113 or any(x is None for x in v[-113:]):
        return None
    out = {}
    for look in (63, 21):
        acc = dist = 0
        up_v = dn_v = 0.0
        for k in range(n - look, n):
            sma = sum(v[k - 49:k + 1]) / 50
            if c[k] > c[k - 1]:
                up_v += v[k]
                acc += v[k] > VP_BIG * sma
            elif c[k] < c[k - 1]:
                dn_v += v[k]
                dist += v[k] > VP_BIG * sma
        out[f"acc{look}"], out[f"dist{look}"], out[f"ad{look}"] = acc, dist, acc - dist
        out[f"udv{look}"] = (up_v / dn_v) if dn_v else None
    return out


def vp_tier(vs: dict | None) -> tuple[str | None, str]:
    """v10 第 2 项定档 → (档位, 说明)。近 21 天净放量上涨日 ≥4 S · 3 A · 2 B · 1 C · ≤0 D;3 个月过热降一档。"""
    if not vs:
        return None, "日线不足 113 根或缺成交量,算不出"
    a = vs["ad21"]
    t = "S" if a >= 4 else "A" if a == 3 else "B" if a == 2 else "C" if a == 1 else "D"
    udv = vs.get("udv63")
    hot = (udv is not None and udv > VP_HOT_UDV) or vs["ad63"] >= VP_HOT_AD
    txt = (f"近 21 天放量上涨 {vs['acc21']} 天、放量下跌 {vs['dist21']} 天(净 {a:+d});"
           f"近 63 天上涨量 ÷ 下跌量 " + (f"{udv:.2f}" if udv is not None else "—") + f"、净放量 {vs['ad63']:+d}")
    if hot and t != "D":
        t = "SABCD"["SABCD".index(t) + 1]
        txt += ",3 个月过热降一档"
    return t, txt


def _bench_aligned(bars: list[tuple], bench: dict | None, n: int) -> list[float] | None:
    """最后 n 根个股日线对应的标普收盘;缺任何一天 / 个股收盘缺 → None。"""
    if not bench or len(bars) < n:
        return None
    out = []
    for b in bars[-n:]:
        r = bench.get(b[0])
        if r is None or not b[1]:
            return None
        out.append(r)
    return out


def accum_stats(bars: list[tuple], bench: dict | None) -> dict | None:
    """第 3 项 / P-22 · 资金逆势买入 → {beta, an, exc, dn};日线 / 基准不够 → None。口径与筛选器同一份,见 accum.py。"""
    return accum.stats(bars, bench)


def accum_check(sp: dict | None, p: dict = PARAMS) -> dict:
    """P-22(v13):按前一天收盘核对资金逆势买入 → {rule, ok, text}。算不出不买。"""
    if not p.get("acc_filter", True):
        return {"rule": "P-22", "ok": True, "text": "资金逆势买入这条已关(acc_filter)"}
    t, txt = acc_tier((sp or {}).get("acc"))
    return {"rule": "P-22", "ok": t == "B", "text": ("昨收时" + txt) if t else txt}


def acc_tier(acc: dict | None) -> tuple[str | None, str]:
    """v12 第 3 项定档 → (档位, 说明)。有资金逆势买入痕迹 B,没有 D;数据只支持这一刀,不设 S / A。"""
    if not acc or acc["exc"] is None:
        return None, "日线或标普基准不足,资金逆势买入算不出"
    ok = acc["an"] >= 1 and acc["exc"] >= 0
    return ("B" if ok else "D"), (f"近 {ACC_LOOK} 天标普下跌 {acc['dn']} 天里,逆势放量超额上涨 {acc['an']} 天、"
                                  f"扣 beta({acc['beta']:.2f})后平均超额 {acc['exc']:+.2f}%"
                                  + ("" if ok else " —— 没有资金逆势买入痕迹"))


def _sma(a: list[float], n: int, j: int) -> float:
    return sum(a[j - n + 1:j + 1]) / n


def lead_stats(bars: list[tuple], bench: dict | None) -> dict | None:
    """v12 · 均线领先大盘(只记录,不定档)→ {ma_frac, slope_gap, bottom_lead, regain_lead};日线 / 基准不足 81 根 → None。
    ma_frac:近 42 天标普收盘跌破 10 日线的日子里,个股仍在 5 日线上的比例(那种日子不足 3 天 → None);
    slope_gap:个股 20 日线 5 天涨幅 − 标普的(个百分点);
    bottom_lead:近 42 天个股最低收盘比标普早几天(标普那段回撤不到 3% → None);
    regain_lead:个股最近一次站上 20 日线比标普早几天(标普还在 20 日线下 = 今天之后;个股不在 20 日线上 → None)。"""
    n = 81
    r = _bench_aligned(bars, bench, n)
    if r is None:
        return None
    s = [b[1] for b in bars[-n:]]
    J = n - 1
    weak = lead = 0
    for j in range(J - 41, J + 1):
        if r[j] < _sma(r, 10, j):
            weak += 1
            lead += s[j] > _sma(s, 5, j)

    def slope(a, j):
        return _sma(a, 20, j) / _sma(a, 20, j - 5) - 1

    w = range(J - 41, J + 1)
    ir = min(w, key=lambda j: r[j])
    is_ = min(w, key=lambda j: s[j])
    r_dd = r[ir] / max(r[j] for j in range(J - 41, ir + 1)) - 1

    def last_cross(a):
        if a[J] <= _sma(a, 20, J):
            return None
        for j in range(J, J - 60, -1):
            if a[j] > _sma(a, 20, j) and a[j - 1] <= _sma(a, 20, j - 1):
                return j
        return J - 60

    sk, rk = last_cross(s), last_cross(r)
    return {"ma_frac": lead / weak if weak >= 3 else None,
            "slope_gap": (slope(s, J) - slope(r, J)) * 100,
            "bottom_lead": (ir - is_) if r_dd <= -0.03 else None,
            "regain_lead": ((J + 1 if rk is None else rk) - sk) if sk is not None else None}


def lead_text(ls: dict | None) -> str:
    if not ls:
        return "均线领先大盘(只记录,不定档):日线或基准不足,算不出"
    parts = [("标普跌破 10 日线的日子里守住 5 日线 " + (f"{ls['ma_frac'] * 100:.0f}%" if ls["ma_frac"] is not None else "—(近 42 天标普没怎么跌破)")),
             f"20 日线斜率比标普 {ls['slope_gap']:+.1f} 个百分点",
             ("比标普早见底 " + (f"{ls['bottom_lead']} 天" if ls["bottom_lead"] is not None else "—(标普近 42 天回撤不到 3%)")),
             ("站上 20 日线比标普早 " + (f"{ls['regain_lead']} 天" if ls["regain_lead"] is not None else "—(个股不在 20 日线上)"))]
    return "均线领先大盘(只记录,不定档):" + " · ".join(parts)


def grade_features(bars: list[tuple], ind: dict, bench: dict | None, p: dict = PARAMS) -> dict:
    c = [b[1] for b in bars]
    v = [b[4] for b in bars]
    px, atr = ind["close"], ind["atr20"]
    stop = stop_of(px, atr, p)
    need = c3.LOOKBACK + c3.VOL_SMA
    return {
        "stop": stop,
        "sup": supports(bars, stop, p),
        "vp": c3._vp_net(c, v) if len(v) >= need and all(x is not None for x in v[-need:]) else None,   # 原口径,留作对照
        "vps": vp_stats(bars),                                                                          # v10 定档用
        "def": c3._defense(bars, bench),                                                                # 原口径,留作对照
        "acc": accum_stats(bars, bench),                                                                # v12 定档用
        "lead": lead_stats(bars, bench),                                                                # v12 只记录
        "macd_d": c3._macd_cross(c, c3.MACD_DAILY_WITHIN),
        "macd_w": c3._macd_cross(c3._weekly_closes(bars), c3.MACD_WEEKLY_WITHIN),
        "res": c3.res_above(bars, px, atr),
    }


def corridor(ind: dict, stop: float) -> tuple[float | None, str]:
    """第 4 项 · 走廊 = (上方 252 日强阻力 − 收盘)÷ R;上方没有阻力 → inf。"""
    px = ind["close"]
    r1 = px - stop
    if r1 <= 0:
        return None, "止损不在收盘下方,R 算不出"
    ra = (ind.get("gf") or {}).get("res")
    if not ra:
        return math.inf, f"R = ${r1:.2f};上方 252 根内没有强阻力(一年新高之上),走廊无上限"
    lvl, src = ra
    corr = max(lvl - px, 0.0) / r1
    return corr, f"R = ${r1:.2f};到上方强阻力 ${lvl:.2f}({src})走廊 {corr:.1f}R"


def grade(ind: dict, stop: float, score=None, p: dict = PARAMS) -> dict:
    """P-20 五项 500 分 → {grade, points, factors:[(项, 档, 分, 说明)], text}。算不出的项按 D 计 0 分(第 5 项算不出记 C)。"""
    gf = ind.get("gf") or {}
    items = []
    sup = gf.get("sup")
    items.append(("止损上方支撑", c3._tier(sup["count"], SUP_MIN) if sup else None,
                  sup["text"] if sup else f"日线不足 {int(p['sup_look']) + 52} 根,算不出"))
    vp_t, vp_txt = vp_tier(gf.get("vps"))
    items.append(("量价配合", vp_t, vp_txt))
    df_t, df_txt = acc_tier(gf.get("acc"))
    items.append(("抗跌", df_t, f"{df_txt};{lead_text(gf.get('lead'))}"))
    pv = ind.get("pivot")
    ch_pct = (ind["close"] / pv - 1) * 100 if pv else None
    ch_tier = None if ch_pct is None else next((k for k in ("S", "A", "B", "C") if ch_pct <= CHASE_MAX[k]), "D")
    items.append(("追高幅度", ch_tier, f"收盘高出枢轴 ${pv:.2f} {ch_pct:.1f}%" if pv else "枢轴缺,算不出"))
    md, mw = bool(gf.get("macd_d")), bool(gf.get("macd_w"))
    items.append(("MACD 金叉", "S" if md and mw else "A" if mw else "B" if md else "C",
                  "日线 + 周线" if md and mw else "只有周线" if mw else "只有日线" if md else "没有金叉"))
    factors = [(name, t or "D", c3.SUB_POINTS[t or "D"], txt) for name, t, txt in items]
    total = sum(x[2] for x in factors)
    g = c3._tier(total, c3.GRADE_MIN)
    return {"grade": g, "points": total, "factors": factors,
            "text": f"{g} 级({total}/500):" + "、".join(f"{a} {b} {pt}({t})" for a, b, pt, t in factors)}


# ═══════════════════════════════════════════════════════════════
# 买入条件
# ═══════════════════════════════════════════════════════════════

def screen_checks(sp: dict | None, p: dict = PARAMS, score=None) -> list[dict]:
    """P-02 ~ P-05,按 sp(前一天收盘的字段)判断 → [{rule, ok, text}]。"""
    sc = sp.get("screen") if sp else None
    if sc is None:
        t = "前一天的日线不足 252 根或窗口里缺高低量,筛选条件算不出"
        return [{"rule": r, "ok": False, "text": t} for r in ("P-02", "P-03", "P-04", "P-05")]
    px = sp["close"]
    out = []
    ok2 = px > p["price_min"] and sc["av30"] is not None and sc["av30"] > p["liq_min"]
    out.append({"rule": "P-02", "ok": ok2,
                "text": f"昨收 ${px:.2f},30 日均量 {sc['av30'] / 1e4:.0f} 万股" + ("" if ok2 else f"(要 > ${p['price_min']:.0f} 且 > {p['liq_min'] / 1e4:.0f} 万)")})
    s50, s150, s200 = sp["sma50"], sc["sma150"], sc["sma200"]
    stack = s50 is not None and s150 is not None and s200 is not None and px > s50 > s150 > s200
    near = px > sc["hi252"] * p["near_high"]
    rs_ok = score is not None and score >= p["rs_min"]
    bad = []
    if not stack:
        bad.append("昨收时均线没排成 收盘 > 50 > 150 > 200")
    if not near:
        bad.append(f"昨收离一年最高 ${sc['hi252']:.2f} 超过 {(1 - p['near_high']) * 100:.0f}%")
    if not rs_ok:
        bad.append(f"RS {score:.0f} < {p['rs_min']}" if score is not None else "RS 缺")
    out.append({"rule": "P-03", "ok": not bad, "text": "昨收时趋势通过" if not bad else ";".join(bad)})
    r3, r1, r5 = sc["rng3m"], sc["rng1m"], sc["rng5d"]
    bad = []
    if r3 is None or not (p["depth_min"] <= r3 <= p["depth_max"]):
        bad.append(f"3 月振幅 {r3 * 100:.1f}% 不在 {p['depth_min'] * 100:.0f}%~{p['depth_max'] * 100:.0f}%" if r3 is not None else "3 月振幅缺")
    if r1 is None or not (p["tight1m_min"] <= r1 <= p["tight1m_max"]):
        bad.append(f"1 月振幅 {r1 * 100:.1f}% 不在 {p['tight1m_min'] * 100:.0f}%~{p['tight1m_max'] * 100:.0f}%" if r1 is not None else "1 月振幅缺")
    if r1 is not None and r3 is not None and not (r1 <= r3 * p["shrink"]):
        bad.append(f"1 月振幅没收缩到 3 月的 {p['shrink']:.2f} 倍以内")
    if r5 is not None and r1 is not None and not (r5 <= r1 * p["tight5d"]):
        bad.append(f"5 日振幅 {r5 * 100:.1f}% 没收紧到 1 月的 {p['tight5d']:.2f} 倍以内")
    if not (sc["low21"] > sc["low63"] * p["higher_low"]):
        bad.append("近 21 日低点没比 63 日低点高 1% 以上")
    txt = (f"昨收时振幅 3 月 {r3 * 100:.1f}% → 1 月 {r1 * 100:.1f}% → 5 日 {r5 * 100:.1f}%" if None not in (r3, r1, r5) else "振幅算不出")
    out.append({"rule": "P-04", "ok": not bad, "text": txt + ("" if not bad else ";" + ";".join(bad))})
    pv = sp.get("pivot")
    vdry = sp["av10"] is not None and sp["av50"] is not None and sp["av10"] < sp["av50"] * p["vdry"]
    ratio = f"{sp['av10'] / sp['av50']:.2f}" if sp["av10"] and sp["av50"] else "—"
    if pv is None:
        out.append({"rule": "P-05", "ok": False,
                    "text": f"昨收时浪高点下方 {p['zone_depth'] * 100:.0f}% 内没有像样的密集成交区(区间量不到 {p['zone_min_share'] * 100:.0f}%),没有枢轴"})
        return out
    z = sp.get("zone") or {}
    dist = (px - pv) / pv * 100
    ok5 = p["pivot_lo"] <= dist <= p["pivot_hi"] and vdry
    out.append({"rule": "P-05", "ok": ok5,
                "text": (f"昨收距枢轴 ${pv:.2f} {dist:+.1f}%(密集区 ${z.get('low', 0):.2f}~${pv:.2f} 占量 {z.get('share', 0) * 100:.0f}%,"
                         f"浪高 ${z.get('wave_high', 0):.2f}),10 日均量 ÷ 50 日均量 {ratio}")
                        + ("" if ok5 else f"(要 {p['pivot_lo']:.0f}% ~ +{p['pivot_hi']:.0f}%、量比 < {p['vdry']})")})
    return out


def trigger_check(ind: dict, p: dict = PARAMS) -> dict:
    """P-06:今天的突破。"""
    px, pv = ind["close"], ind.get("pivot")
    if pv is None:
        return {"rule": "P-06", "ok": False, "text": "浪高点下方没有像样的密集成交区,没有枢轴可突破"}
    brk = px > pv
    not_ext = px < pv * p["extend"]
    surge = ind["av50"] is not None and ind["volume"] > ind["av50"] * p["vol_surge"]
    if not brk:
        t6 = f"收盘 ${px:.2f} 还没站上枢轴 ${pv:.2f}"
    elif not not_ext:
        t6 = f"收盘 ${px:.2f} 已高出枢轴 {(px / pv - 1) * 100:.1f}%,超过 {(p['extend'] - 1) * 100:.0f}% 不追"
    else:
        t6 = f"收盘 ${px:.2f} 突破枢轴 ${pv:.2f}(高出 {(px / pv - 1) * 100:.1f}%)"
    t6 += f",成交量 {ind['volume'] / ind['av50']:.2f} × 50 日均量" if ind["av50"] else ",50 日均量算不出"
    if not surge:
        t6 += f"(要 > {p['vol_surge']:.1f} 倍)"
    return {"rule": "P-06", "ok": brk and not_ext and surge, "text": t6}


def entry_checks(ind: dict, p: dict = PARAMS, score=None, market: dict | None = None) -> list[dict]:
    """→ [{rule, ok, text}] 按 P-01 ~ P-06:市场与突破看今天,P-02 ~ P-05 看前一天收盘。"""
    mk_ok = bool(market and market.get("ok"))
    return ([{"rule": "P-01", "ok": mk_ok, "text": market["text"] if market else "没有基准日线"}]
            + screen_checks(ind.get("prev"), p, score) + [accum_check(ind.get("prev"), p), trigger_check(ind, p)])


def entry_ok(ind: dict, p: dict = PARAMS, score=None, market: dict | None = None) -> bool:
    return all(c["ok"] for c in entry_checks(ind, p, score, market))


def watch_item(code, name, ind, held, blocked_reason, score=None, p: dict = PARAMS, market=None) -> dict:
    it = {"symbol": code, "name": name, "score": score, "rule_id": ENTRY_RULE, "rule_text": RULES[5]["condition"]}
    if ind is None:
        it.update({"price": None, "progress_pct": None, "gap": f"日线不足 {MIN_BARS} 根或缺高低量,指标算不出"})
        return it
    it["price"] = round(ind["close"], 2)
    checks = entry_checks(ind, p, score, market)
    passed = sum(1 for c in checks if c["ok"])
    it["progress_pct"] = int(passed / len(checks) * 100)
    fails = [c for c in checks if not c["ok"]]
    it["fails"] = [c["rule"] for c in fails]      # 悬停日K「形态就绪」那层按它判(agent_run.scan_layers)
    if held:
        it["gap"] = "已持仓 · 等加仓 / 出场信号"
    elif not fails:
        it["gap"] = "买入条件全满足 —— 今日收盘触发买入"
    else:
        it["gap"] = f"{passed}/{len(checks)} 满足 · 还差:" + ";".join(f"{c['rule']} {c['text']}" for c in fails)
    if blocked_reason:
        it["blocked"] = True
        it["blocked_reason"] = blocked_reason
    return it


# ═══════════════════════════════════════════════════════════════
# 一天的决策
# ═══════════════════════════════════════════════════════════════

def _fill(side, pos: av.Position, shares, price, rule, rationale, **extra) -> dict:
    d = {"side": side, "symbol": pos.code, "name": pos.name, "shares": int(shares), "price": round(price, 2),
         "rule_id": rule, "rule_name": RULE_NAME.get(rule, rule), "rationale": rationale,
         "entry_date": pos.entry_date, "level": pos.level}
    d.update(extra)
    return d


def _sell(pos: av.Position, qty: int, px: float, rule: str, why: str, state: dict, fills: list, n: int, want_text: bool, base: str):
    qty = max(1, min(int(qty), pos.size))
    pnl = (px - pos.avg_cost) * qty
    state["cash"] += qty * px
    if qty >= pos.size:
        state["closed_pnl"].append(pnl)
    fills.append(_fill("sell", pos, qty, px, rule, (base + why) if want_text else "",
                       pnl_abs=round(pnl, 2), pnl_pct=round((px / pos.avg_cost - 1) * 100, 2), hold_days=n))
    pos.size -= qty


def exit_rule(pos: av.Position, ind: dict, market: dict | None, p: dict = PARAMS) -> tuple[str | None, str]:
    """完全出场(P-09 ~ P-11 止损、P-13 / P-14 趋势、P-15 市场)→ (规则, 说明);不出 → (None, "")。
    按脚本 full_exit 里的顺序取第一个成立的做出场原因。"""
    px, ep = ind["close"], pos.entry_price
    profit = (px - ep) / ep * 100
    # v7 回到 v5:P-09 / P-10 加回来(v6 去掉过)
    if ind["low"] < ind["base_low"] * p["base_stop"]:
        return "P-09", f"最低价 ${ind['low']:.2f} 跌破 Base 低点 ${ind['base_low']:.2f} 的 {p['base_stop']:.2f} 倍 —— 止损 A,按收盘出。"
    if px < ep * p["fixed_stop"]:
        return "P-10", f"收盘 ${px:.2f} 跌破进场价 × {p['fixed_stop']:.2f} = ${ep * p['fixed_stop']:.2f} —— 固定止损。"
    if pos.stop and px < pos.stop:
        be = pos.stop >= pos.avg_cost - 1e-9
        return "P-11", (f"收盘 ${px:.2f} 跌破{'保本止损(持仓均价)' if be else '初始止损'} ${pos.stop:.2f} —— 出场。")
    ex = pos.extra or {}
    if ex.get("half20_done") and ind.get("ema20") is not None and px < ind["ema20"]:
        return "P-19", f"+20% 减半之后,收盘跌破 EMA20 ${ind['ema20']:.2f} —— 移动止盈清仓。"
    if ind["ema21"] is not None and px < ind["ema21"] and profit > p["exit_ema21_profit"]:
        return "P-13", f"浮盈 {profit:.1f}% 时收盘跌破 EMA21 ${ind['ema21']:.2f} —— 趋势出场。"
    if ind["sma50"] is not None and px < ind["sma50"] and profit > p["exit_sma50_profit"]:
        return "P-14", f"浮盈 {profit:.1f}% 时收盘跌破 50 日线 ${ind['sma50']:.2f} —— 趋势出场。"
    if market is not None and market.get("above") is False:
        return "P-15", f"大盘转弱({market['text']})—— 清仓。"
    return None, ""


def manage_position(pos: av.Position, ind: dict, state: dict, p: dict = PARAMS, want_text: bool = True) -> list[dict]:
    px, ep = ind["close"], pos.entry_price
    pos.bars_held += 1
    n = pos.bars_held
    ex = pos.extra
    market = state.get("market")
    profit = (px - ep) / ep * 100
    base = (f"进场价 ${ep:.2f},今收 ${px:.2f}({profit:+.1f}%),持有 {n} 个交易日。") if want_text else ""
    fills: list = []
    rule, why = exit_rule(pos, ind, market, p)
    if rule:
        _sell(pos, pos.size, px, rule, why, state, fills, n, want_text, base)
        pos.highest = max(pos.highest, px)
        state["closed"].append(pos)
        return fills
    # v14 P-24 财报前减仓:排在完全出场之后、其他减仓 / 加仓之前;每次财报只做一次(extra.earn_done = 财报日)
    earn_on = p.get("earn_filter", True)
    ei = ed.info(pos.code, state.get("earn")) if earn_on else None
    earn_sold = False
    e_iso = str(ei["date"]) if ei and ei["date"] else None
    if (ei and ei["known"] and ei["tdays"] is not None and ei["tdays"] <= p["earn_exit_days"]
            and ex.get("earn_done") != e_iso):
        keep = p["earn_keep_profit"]
        half = int(pos.size * p["earn_keep_frac"])
        head = (f"{ei['text']};浮盈 {profit:+.1f}%(相对首笔进场价 ${ep:.2f};"
                f"按持仓均价 ${pos.avg_cost:.2f} 算 {(px / pos.avg_cost - 1) * 100:+.1f}%)")
        if profit > keep + 1e-9 and half >= 1:
            _sell(pos, half, px, "P-24", f"{head} > {keep:.0f}% —— 财报前卖出一半({half} 股),余仓照原来的止损止盈走。",
                  state, fills, n, want_text, base)
            ex["earn_done"] = e_iso
            earn_sold = True
        else:
            why = (f"{head} > {keep:.0f}%,但只剩 {pos.size} 股、卖一半取整为 0 —— 整股清掉。" if profit > keep + 1e-9
                   else f"{head} 不大于 {keep:.0f}% —— 财报前清仓。")
            _sell(pos, pos.size, px, "P-24", why, state, fills, n, want_text, base)
            pos.highest = max(pos.highest, px)
            state["closed"].append(pos)
            return fills
    # P-23:财报窗口内(或日历缺、不知道)不加仓
    add_ok = (not earn_on) or (ei["known"] and (ei["tdays"] is None or ei["tdays"] > p["earn_block_days"]))
    if earn_sold:
        pass                                   # 财报前减半当天,不再做别的减仓 / 加仓
    # v5 减仓:+20% 减半(一次)→ 之后跌破 EMA10 再减半(一次)。清仓那条 P-19 在 exit_rule 里
    elif (ex.get("half20_done") and not ex.get("ema10_done") and ind.get("ema10") is not None
            and px < ind["ema10"] and pos.size >= 2):
        _sell(pos, pos.size // 2, px, "P-18", f"+20% 减半之后收盘跌破 EMA10 ${ind['ema10']:.2f} —— 卖出余仓一半。",
              state, fills, n, want_text, base)
        ex["ema10_done"] = True
    elif not ex.get("half20_done") and px >= ep * p["half_trigger"] and pos.size >= 2:
        _sell(pos, pos.size // 2, px, "P-17", f"收盘到进场价 × {p['half_trigger']:.2f} = ${ep * p['half_trigger']:.2f} 以上 —— 减半,余仓按 EMA10 / EMA20 移动止盈。",
              state, fills, n, want_text, base)
        ex["half20_done"] = True
    # 部分止盈(一次)
    elif (profit >= p["partial_profit"] and not ex.get("partial_done") and px < ind["prev_high"]
            and ind["av10"] is not None and ind["volume"] < ind["av10"] and pos.size >= 2):
        qty = max(1, int(round(pos.size * p["partial_frac"])))
        _sell(pos, qty, px, "P-12", f"浮盈 {profit:.1f}% ≥ {p['partial_profit']:.0f}%,收盘低于昨天最高 ${ind['prev_high']:.2f}、"
                                     f"量低于 10 日均量 —— 上涨出现暂停,卖出约 {p['partial_frac'] * 100:.0f}%({qty} 股)。",
              state, fills, n, want_text, base)
        ex["partial_done"] = True
    elif add_ok and market and market.get("ok") and pos.level < 3 and ind["av20"]:
        # 加仓:按顺序,一天最多一次;股数按进场时定下的完整仓位
        unit = int(ex.get("unit") or pos.initial_size)
        add, why = 0, ""
        if (pos.level == 1 and px > ep * p["add1_pct"] and ind["volume"] > ind["av20"] * p["add1_vol"]
                and ind["ema8"] is not None and px > ind["ema8"]):
            add, why = int(unit * p["add1_frac"]), (f"收盘高出进场价 {profit:.1f}%(> {(p['add1_pct'] - 1) * 100:.0f}%),量 "
                                                    f"{ind['volume'] / ind['av20']:.2f} × 20 日均量,站上 EMA8 —— 第一次加仓 {p['add1_frac'] * 100:.0f}%")
        elif (pos.level == 2 and px > ep * p["add2_pct"] and ind["volume"] > ind["av20"] * p["add2_vol"]
                and ind["ema21"] is not None and px > ind["ema21"]):
            add, why = int(unit * p["add2_frac"]), (f"收盘高出进场价 {profit:.1f}%(> {(p['add2_pct'] - 1) * 100:.0f}%),量 "
                                                    f"{ind['volume'] / ind['av20']:.2f} × 20 日均量,站上 EMA21 —— 第二次加仓 {p['add2_frac'] * 100:.0f}%")
        if add > 0 and add * px <= state["cash"]:
            state["cash"] -= add * px
            pos.avg_cost = (pos.avg_cost * pos.size + add * px) / (pos.size + add)
            pos.size += add
            pos.level += 1
            fills.append(_fill("buy", pos, add, px, "P-08", (base + why + f"({add} 股)。") if want_text else "",
                               amount=round(add * px, 2), position_pct=round(add * px / state["equity"] * 100, 2)))
    pos.highest = max(pos.highest, px)
    # 保本:最高收盘到过进场价 × 1.05 → 止损上移到持仓均价(只上不下;明天起按新止损判断)
    if pos.highest >= ep * p["be_trigger"] and pos.avg_cost > pos.stop:
        pos.stop = pos.avg_cost
    if pos.size <= 0:
        state["closed"].append(pos)
    return fills


def try_entry(code, name, ind, state: dict, p: dict = PARAMS, want_text: bool = True, score=None):
    market = state.get("market")
    checks = entry_checks(ind, p, score, market)
    if not all(c["ok"] for c in checks[1:]):
        return None, None
    if not checks[0]["ok"]:
        return None, f"突破成立,但市场环境不满足:{checks[0]['text']}(P-01)"
    ei = ed.info(code, state.get("earn"))
    if p.get("earn_filter", True):
        if not ei["known"]:
            return None, f"突破成立,但{ei['text']} —— 不知道就不买(P-23)"
        if ei["tdays"] is not None and ei["tdays"] <= p["earn_block_days"]:
            return None, f"突破成立,但{ei['text']},财报前 {int(p['earn_block_days'])} 个交易日内不买(P-23)"
    if state.get("halt_reason"):
        return None, f"突破成立,但护栏挡下:{state['halt_reason']}(P-16)"
    if len(state["positions"]) >= p["max_holdings"]:
        return None, f"突破成立,但已持有 {len(state['positions'])} 只,达到上限 {p['max_holdings']}(P-07)"
    equity, px = state["equity"], ind["close"]
    atr = ind.get("atr20")
    if not atr or atr <= 0:
        return None, "突破成立,但 ATR(20) 算不出,没法定初始止损(不拿 8% 顶替)(P-11)"
    stop = stop_of(px, atr, p)
    if ind.get("gf") is None:
        return None, "突破成立,但评分字段没算出来(日线不足或缺值),不定档不买(P-20)"
    pv = ind.get("pivot")
    chase_pct = (px / pv - 1) * 100 if pv else None
    if chase_pct is not None and chase_pct > p["chase_block"]:
        return None, f"突破成立,但收盘高出枢轴 ${pv:.2f} {chase_pct:.1f}%,超过 {p['chase_block']:.0f}% —— 追高不买(P-20)"
    corr, corr_txt = corridor(ind, stop)
    if corr is not None and corr < p["corr_min"]:
        return None, f"突破成立,但空间受限:{corr_txt},不足 {p['corr_min']:.0f}R —— 不买(P-21)"
    gr = grade(ind, stop, score, p)
    if p.get("block_d") and gr["grade"] == "D":
        return None, f"突破成立,但评分 {gr['text']} —— D 级不买(P-20)"
    if p.get("size_by_grade"):
        pct = p["grade_pct"][gr["grade"]]
        unit = size = int(equity * pct / px)
        size_txt = f"P-07 {gr['grade']} 级首次买入总资产 {pct * 100:.0f}%"
    else:
        unit = int(equity * p["unit_pct"] / px)
        size = int(unit * p["initial_frac"])
        size_txt = (f"P-07 统一仓位(评分只记录,不影响仓位):完整仓位 = 总资产 {p['unit_pct'] * 100:.0f}% ÷ ${px:.2f} = {unit} 股,"
                    f"首次买入 {p['initial_frac'] * 100:.0f}%")
    if size <= 0:
        return None, f"突破成立({gr['grade']} 级),但按仓位算出的股数为 0"
    cash_cut = False
    if size * px > state["cash"]:
        size = int(state["cash"] / px)
        cash_cut = True
        if size <= 0:
            return None, f"突破成立,但现金只剩 ${state['cash']:.0f},买不起 1 股"
    cost = size * px
    state["cash"] -= cost
    pos = av.Position(code=code, name=name or code, size=size, initial_size=unit, entry_price=px,
                      entry_date=state["date"], avg_cost=px, highest=px, level=1, bars_held=0,
                      entry_rule=ENTRY_RULE, stop=stop, risk=px - stop,
                      extra={"unit": unit, "pivot": ind["pivot"], "atr": atr, "grade": gr["grade"], "points": gr["points"]})
    state["positions"].append(pos)
    extra = {"amount": round(cost, 2), "position_pct": round(cost / equity * 100, 2),
             "grade": gr["grade"], "points": gr["points"], "grade_detail": gr["text"]}
    if not want_text:
        return _fill("buy", pos, size, px, ENTRY_RULE, "", **extra), None
    rationale = ("".join(f"{c['rule']} {c['text']};" for c in checks)
                 + f"P-21 {corr_txt}(≥ {p['corr_min']:.0f}R)。"
                 + (f"P-23 {ei['text']}(财报前 {int(p['earn_block_days'])} 个交易日内不买)。" if p.get("earn_filter", True) else "")
                 + f"P-20 评分 {gr['text']}。"
                 + size_txt
                 + (f",现金只够 {size} 股" if cash_cut else f",买入 {size} 股")
                 + f",占总资产 {cost / equity * 100:.1f}%。"
                 + f"初始止损 = max(收盘 − {p['stop_atr']:.0f} × ATR ${atr:.2f}, 收盘 × {1 - p['stop_cap']:.2f}) = ${stop:.2f}"
                 + f"(距收盘 {(1 - stop / px) * 100:.1f}%{',被 8% 上限截住' if px - p['stop_atr'] * atr < px * (1 - p['stop_cap']) else ''})。")
    return _fill("buy", pos, size, px, ENTRY_RULE, rationale, **extra), None


def run_day(date_iso: str, positions, cash: float, bars_of, watch, prev_equity, consec_losses: int,
            p: dict = PARAMS, g: dict = av.GUARDS, ind_of=None, want_text: bool = True) -> dict:
    """接口与其他引擎相同。市场环境从 ind_of(MARKET_KEY) 取,财报日视图从 ind_of(EARNINGS_KEY) 取(v14)。"""
    state = {"date": date_iso, "cash": cash, "positions": list(positions), "closed": [], "closed_pnl": [],
             "equity": None, "halt_reason": None, "market": None, "earn": None}
    if ind_of is None:
        def ind_of(code):
            if code in (MARKET_KEY, SECTORS_KEY, EARNINGS_KEY):
                return None
            return indicators(bars_of(code) or [], p)
    state["market"] = ind_of(MARKET_KEY)
    ev = ind_of(EARNINGS_KEY)
    state["earn"] = ev if isinstance(ev, ed.EarningsView) else None     # 不是视图 = 没接上,按「日历缺」处理
    ind_cache = {pos.code: ind_of(pos.code) for pos in state["positions"]}
    mv = sum(pos.size * (ind_cache[pos.code]["close"] if ind_cache[pos.code] else pos.avg_cost) for pos in state["positions"])
    equity = cash + mv
    state["equity"] = equity
    if prev_equity and (equity / prev_equity - 1) * 100 <= g["daily_loss_halt_pct"]:
        state["halt_reason"] = f"今日权益 {(equity / prev_equity - 1) * 100:+.1f}%,触及单日亏损熔断 {g['daily_loss_halt_pct']:.0f}%,今天不开新仓"
    elif consec_losses >= g["consecutive_loss_pause"]:
        state["halt_reason"] = f"此前连亏 {consec_losses} 笔,按护栏今天不开新仓"
        consec_losses = 0
    fills: list = []
    for pos in list(state["positions"]):
        if pos.extra is None:
            pos.extra = {}
        ind = ind_cache[pos.code]
        if ind is None:
            pos.bars_held += 1
            continue
        fills += manage_position(pos, ind, state, p, want_text)
    state["positions"] = [x for x in state["positions"] if x.size > 0]
    held = {x.code for x in state["positions"]}
    watch_items = []
    for code, name, score in watch:
        ind = ind_cache[code] if code in ind_cache else ind_of(code)
        ind_cache[code] = ind
        blocked = None
        if code not in held and ind is not None:
            f, blocked = try_entry(code, name, ind, state, p, want_text, score)
            if f:
                fills.append(f)
                held.add(code)
        if want_text:
            watch_items.append(watch_item(code, name, ind, code in held and not any(
                x["symbol"] == code and x["side"] == "buy" and x["rule_id"] == ENTRY_RULE for x in fills),
                blocked, score, p, state["market"]))
    for pnl in state["closed_pnl"]:
        consec_losses = consec_losses + 1 if pnl < 0 else 0
    mv = sum(pos.size * (ind_cache.get(pos.code) or ind_of(pos.code) or {"close": pos.avg_cost})["close"]
             for pos in state["positions"])
    watch_items.sort(key=lambda x: (bool(x.get("blocked")), -(x.get("progress_pct") or 0)))
    return {"fills": fills, "positions": state["positions"], "cash": state["cash"],
            "equity": state["cash"] + mv, "watch_items": watch_items, "halt_reason": state["halt_reason"],
            "consec_losses": consec_losses, "closed": state["closed"], "market": state["market"]}
