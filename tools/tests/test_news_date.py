"""`stock_news` 的 date 补洞（待办池 P1-17）。

实测上游一次返回 30 条、30 个 `date` 全是空串。模型只能从东方财富的 URL 里推日期，
推对了是运气，而且推出来的日期会被当成「接口给的」写进报告。
这里钉住：**推得出来就填上并标明是推的；推不出来就留空，不编。**
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "opencode-mcp"))


# I2：补洞实现已经挪进 hca_news_date.py（watchlist 与 hcapack 共用一份），
# 测试也跟着改成直接 import —— 原来是按源码切片 exec 出来的，
# 那种写法在实现挪家之后会悄无声息地测到一个空壳。
from hca_news_date import _fill_news_dates as fill  # noqa: E402

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


def test_URL里的八位数字不是合理日期时不编():
    """回归：`/a/(\\d{8})` 只是「开头八位数字」。

    换个栏目的 URL（`/a/12345678.html`）原先会推出「1234-56-78」并标成
    date —— 那是编出来的日期，比留空更糟（红线：数字一律实测，推不出写空）。
    """
    for url, why in [
        ("http://stock.eastmoney.com/a/12345678.html", "月 56 日 78，根本不是日期"),
        ("http://stock.eastmoney.com/a/20261332.html", "13 月"),
        ("http://stock.eastmoney.com/a/20260230.html", "2 月 30 日"),
        ("http://stock.eastmoney.com/a/18000101.html", "年份离谱"),
    ]:
        d = json.loads(fill(json.dumps(
            {"items": [{"title": "t", "url": url, "date": ""}]}, ensure_ascii=False)))
        assert d["items"][0]["date"] == "", why
        assert "date_source" not in d["items"][0], why
        assert "_hca_note" not in d, why


def test_合理日期仍然照补():
    d = json.loads(fill(json.dumps(
        {"items": [{"title": "t", "url": "http://stock.eastmoney.com/a/202609213879940651.html",
                    "date": ""}]}, ensure_ascii=False)))
    assert d["items"][0]["date"] == "2026-09-21"
    assert "推断" in d["items"][0]["date_source"]
