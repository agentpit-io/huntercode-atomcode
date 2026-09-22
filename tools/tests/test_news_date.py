"""`stock_news` 的 date 补洞（待办池 P1-17）。

实测上游一次返回 30 条、30 个 `date` 全是空串。模型只能从东方财富的 URL 里推日期，
推对了是运气，而且推出来的日期会被当成「接口给的」写进报告。
这里钉住：**推得出来就填上并标明是推的；推不出来就留空，不编。**
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "opencode-mcp"))


def _load():
    """watchlist_mcp 顶层 import 了 mcp / httpx，测试环境未必有；只取纯函数。"""
    import re as _re
    src = (Path(__file__).resolve().parents[1] / "opencode-mcp" / "watchlist_mcp.py").read_text(encoding="utf-8")
    i = src.index("_NEWS_URL_DATE = ")
    j = src.index("@server.call_tool()")
    ns: dict = {}
    exec("import json, re\n" + src[i:j], ns)
    return ns["_fill_news_dates"]


fill = _load()

ITEM_URL = "http://stock.eastmoney.com/a/202609213879940651.html"


def test_空date按URL补上并标来源():
    raw = json.dumps({"type": "stock_news", "code": "600519",
                      "items": [{"title": "t", "date": "", "url": ITEM_URL}]}, ensure_ascii=False)
    d = json.loads(fill(raw))
    assert d["items"][0]["date"] == "2026-09-21"
    assert "URL" in d["items"][0]["date_source"]
    assert "推断" in d["_hca_note"]


def test_已经有date的不动():
    raw = json.dumps({"items": [{"date": "2026-01-02", "url": ITEM_URL}]}, ensure_ascii=False)
    d = json.loads(fill(raw))
    assert d["items"][0]["date"] == "2026-01-02"
    assert "date_source" not in d["items"][0]
    assert "_hca_note" not in d          # 一条都没推就不该加提示


def test_URL里没日期就留空不编():
    raw = json.dumps({"items": [{"date": "", "url": "http://example.com/x.html"}]}, ensure_ascii=False)
    d = json.loads(fill(raw))
    assert d["items"][0]["date"] == ""
    assert "date_source" not in d["items"][0]
    assert "_hca_note" not in d


def test_不是新闻结构就原样返回():
    raw = json.dumps({"error": "boom"}, ensure_ascii=False)
    assert fill(raw) == raw
    assert fill("这不是 JSON") == "这不是 JSON"
