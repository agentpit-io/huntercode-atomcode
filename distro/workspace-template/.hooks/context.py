#!/usr/bin/env python3
"""HCA 上下文注入 · UserPromptSubmit · 把当天真实的时间 / 市场状态 / 工作区资产清单
追加到用户这一条消息后面。

上游行为（`cc_hooks.rs:625-684`）：UserPromptSubmit 的 hook 并发跑，
每个 hook 的 stdout（或 `hookSpecificOutput.additionalContext`）被 **join 后追加到
用户消息正文**。也就是说这里打印什么，模型就多看到什么。

## 为什么需要它

AtomCode 的系统提示里只有一行 `## ENVIRONMENT: Today's date: …`，而且那是
**会话开始时冻结**的（`persona.rs` 的 `date_anchor_line`，assemble 一次会话只跑一次）。
投研场景里「现在是不是交易时段」「今天是不是交易日」直接决定答案对不对 ——
收盘后问"今天涨了多少"和盘中问是两件事。冻结的日期还会让跨天的长会话答错日期。

## 数据一律真取，取不到就不注入（总控红线 1）

| 行 | 来源 | 取不到时 |
|---|---|---|
| 日期 / 时间 / 星期 | 系统时钟（容器 TZ；未设则按 Asia/Shanghai 偏移换算） | 不会取不到 |
| 是否交易日 | **AKShare 真实交易日历** `tool_trade_date_hist_sina()`，缓存到 `.atomcode/trade_calendar.json`（24 小时） | 整行不输出 |
| 交易时段 | 交易所公开的固定时段（9:15 集合竞价 / 9:30–11:30 / 13:00–15:00），在"是交易日"成立时才给 | 同上 |
| 工作区资产 | 真实目录清单与文件数 | 目录不存在就不列 |

**不会**注入的东西：任何行情数字。盘口/涨跌幅要走 MCP 工具，在这里塞一个
"今日上证 +0.8%" 既慢又会变成没有出处的数字。
"""
from __future__ import annotations

import datetime
import json
import os
import sys

WORKSPACE = os.path.normpath(os.environ.get("HCA_WORKSPACE") or os.getcwd())
CACHE = os.path.join(WORKSPACE, ".atomcode", "trade_calendar.json")
CACHE_TTL_S = 24 * 3600
WEEKDAY = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
ASSET_DIRS = [
    ("holdings", "持仓与交易记录"),
    ("theses", "投资论点"),
    ("factors", "因子定义与回测"),
    ("reports", "已生成的研究报告"),
]


def now_shanghai() -> datetime.datetime:
    """容器里 TZ 通常没设，UTC 时钟 + 8 小时就是上海时间（中国不用夏令时）。"""
    tz = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(tz)


def load_calendar() -> set:
    """真实交易日历。缓存新鲜就用缓存，否则问 AKShare；都失败返回空集合。"""
    try:
        st = os.stat(CACHE)
        import time
        if time.time() - st.st_mtime < CACHE_TTL_S:
            with open(CACHE, encoding="utf-8") as f:
                return set(json.load(f).get("trade_dates") or [])
    except Exception:  # noqa: BLE001
        pass

    try:
        import akshare as ak  # 只有缓存过期时才付这几秒的 import 代价
        df = ak.tool_trade_date_hist_sina()
        dates = sorted({str(d)[:10] for d in df["trade_date"].tolist()})
    except Exception:  # noqa: BLE001
        return set()

    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({"source": "akshare.tool_trade_date_hist_sina",
                       "fetched_at": now_shanghai().isoformat(timespec="seconds"),
                       "trade_dates": dates}, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass
    return set(dates)


def session_label(t: datetime.datetime) -> str:
    """A 股固定时段（交易所公开规则，不是取来的数字）。"""
    hm = t.hour * 60 + t.minute
    if hm < 9 * 60 + 15:
        return "未开盘"
    if hm < 9 * 60 + 25:
        return "集合竞价"
    if hm < 9 * 60 + 30:
        return "竞价结束待开盘"
    if hm <= 11 * 60 + 30:
        return "上午连续竞价（交易中）"
    if hm < 13 * 60:
        return "午间休市"
    if hm <= 15 * 60:
        return "下午连续竞价（交易中）"
    return "已收盘"


def assets() -> list:
    lines = []
    for name, desc in ASSET_DIRS:
        d = os.path.join(WORKSPACE, name)
        if not os.path.isdir(d):
            continue
        files = [f for f in sorted(os.listdir(d))
                 if os.path.isfile(os.path.join(d, f)) and f != "README.md"]
        if files:
            head = "、".join(files[:6]) + ("…" if len(files) > 6 else "")
            lines.append(f"- `{name}/`（{desc}）：{len(files)} 个文件 —— {head}")
        else:
            lines.append(f"- `{name}/`（{desc}）：空")
    return lines


def main() -> int:
    # stdin 必须读掉（上游会把 payload 写进来），但这里用不到它的内容
    try:
        sys.stdin.read()
    except Exception:  # noqa: BLE001
        pass

    t = now_shanghai()
    today = t.strftime("%Y-%m-%d")
    out = ["<hca-context>",
           f"当前时间：{today} {t.strftime('%H:%M')} {WEEKDAY[t.weekday()]}（上海时间，由运行环境实时取得）"]

    cal = load_calendar()
    if cal:
        if today in cal:
            out.append(f"A 股今日为交易日，当前时段：{session_label(t)}")
        else:
            nxt = next((d for d in sorted(cal) if d > today), None)
            line = "A 股今日非交易日"
            if nxt:
                line += f"，下一个交易日 {nxt}"
            out.append(line)
        out.append("（交易日历来自 AKShare `tool_trade_date_hist_sina`，非推算）")
    # 取不到日历就整段不提市场状态 —— 宁可不说，也不说不确定的话

    a = assets()
    if a:
        out.append("工作区现有研究资产：")
        out.extend(a)

    out.append("以上由系统注入，不是用户输入；回答时不要复述，也不要为它单独致谢。")
    out.append("</hca-context>")
    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
