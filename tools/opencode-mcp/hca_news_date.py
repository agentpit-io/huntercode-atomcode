"""`stock_news` 的 date 补洞（待办池 P1-17）—— watchlist 与 hcapack 共用这一份。

为什么单独成一个模块（I2）：`hcapack` 的 `stocks_intel` 也直接打同一个后端接口，
原先补洞只写在 `watchlist_mcp.py` 里，组合工具走的是另一条路 —— 新闻照样没有日期，
而题面明确要求「每条都要给出来源与日期」。一份实现两处用，改一处不会漏另一处。
"""
from __future__ import annotations

import json
import re
from datetime import date as _date


# ── stock_news 的 date 补洞（待办池 P1-17）────────────────────────────────
# 实测：`stock_news` 一次返回 30 条新闻,**30 个 `date` 字段全是空串**
# (`docs/eval/pilot-q5/`)。模型只能从东方财富 URL 里的日期串去推
# (`http://stock.eastmoney.com/a/202609213879940651.html` → 2026-09-21),
# 推对了,但那是运气 —— 而且推出来的日期会被当成「工具给的」写进报告。
#
# 根因在 `apps/api` 的 `/api/internal/watchlist/stock_news`(导入来的既有问题,
# 不是本发行版引入的),那一侧用的是公开 GHCR 镜像,本仓库不重建它。
# 所以补在我们这一跳:**推得出来就填上,并标明是推的**;推不出来就留空,不编。
_NEWS_URL_DATE = re.compile(r"/a/(\d{8})\d*\.html")


def _fill_news_dates(text: str) -> str:
    """把 items[].date 的空串按 URL 里的日期串补上,并标 `date_source`。"""
    try:
        d = json.loads(text)
    except Exception:                                          # noqa: BLE001
        return text
    if not isinstance(d, dict) or not isinstance(d.get("items"), list):
        return text
    derived = 0
    for it in d["items"]:
        if not isinstance(it, dict) or (it.get("date") or "").strip():
            continue
        m = _NEWS_URL_DATE.search(str(it.get("url") or ""))
        if not m:
            continue
        y, mo, day = m.group(1)[:4], m.group(1)[4:6], m.group(1)[6:8]
        # **推不出合理日期就当没推出来**。`/a/(\d{8})` 只是「开头八位数字」,
        # 换个栏目的 URL(比如 `/a/12345678.html`)会推出「1234-56-78」——
        # 那是编的,比留空更糟。这里只认真日期。
        try:
            _date(int(y), int(mo), int(day))
        except ValueError:
            continue
        if not 1990 <= int(y) <= 2100:
            continue
        it["date"] = f"{y}-{mo}-{day}"
        it["date_source"] = "从文章 URL 推断（上游未给日期字段）"
        derived += 1
    if derived:
        d["_hca_note"] = (f"{derived} 条新闻的 date 是**从文章 URL 推断**的"
                          f"（上游 stock_news 把 date 返回成空串）。"
                          f"引用日期时请注明来源为 URL 推断,不要说成接口返回。")
    return json.dumps(d, ensure_ascii=False)
