"""`akshare_call` 的字节预算与列投影（待办池 P0-11）。

**要 pandas**，开发机上没装 → 自动跳过；真正的证据是在 **daemon 容器里**跑这同一份
文件（`/opt/hca/venv/bin/python -m pytest`），结果记在 M5 报告里。

为什么光限行数不够：`AKSHARE_MAX_ROWS=50` 对宽表没用 ——
`stock_financial_analysis_indicator` 一行就有八十几列，50 行照样几十 KB，
照样被内核砍成头尾各 4 KB 且 JSON 断裂。
"""
import json
import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas", reason="开发机没装 pandas；容器里跑才是真证据")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "akshare-mcp"))


def _server(monkeypatch=None):
    """akshare_mcp.server 顶层 import akshare，开发机没有 —— 只取纯函数。"""
    src = (Path(__file__).resolve().parents[1] / "akshare-mcp" / "akshare_mcp" / "server.py").read_text(encoding="utf-8")
    i = src.index("def _to_json(")
    ns = {"json": json, "MAX_ROWS": 50, "MAX_BYTES": 15000}
    exec(src[i:src.index("def main()")], ns)
    return ns


NS = _server()
to_json = NS["_to_json"]
MAX_BYTES = NS["MAX_BYTES"]


def wide_df(rows: int, cols: int = 84):
    """仿财务指标接口：很多列、每列一个长小数。"""
    return pd.DataFrame({f"指标_{c}_同比增长率(%)": [f"{c}.{r}23456789" for r in range(rows)]
                         for c in range(cols)})


def nbytes(s: str) -> int:
    return len(s.encode("utf-8"))


def test_宽表会被字节预算压住():
    df = wide_df(50)
    raw = json.dumps(json.loads(df.to_json(orient="records")), ensure_ascii=False)
    assert nbytes(raw) > 16 * 1024, "夹具本身就得超过内核阈值，否则这条测试没意义"

    out = to_json("stock_financial_analysis_indicator", df)
    assert nbytes(out) <= MAX_BYTES
    d = json.loads(out)                       # 合法 JSON，不是断裂的头尾
    assert d["truncated"] is True
    assert d["rows"] < 50
    assert d["total"] == 50
    assert "不是不存在" in d["note"]


def test_列投影把预算花在需要的列上():
    df = wide_df(50)
    want = ["指标_0_同比增长率(%)", "指标_1_同比增长率(%)", "不存在的列"]
    out = to_json("stock_financial_analysis_indicator", df, want)
    d = json.loads(out)
    assert nbytes(out) <= MAX_BYTES
    assert d["columns"] == want[:2]
    assert d["columns_not_found"] == ["不存在的列"]
    assert d["columns_dropped_by_projection"] == 82
    # 投影之后 50 行全放得下 —— 这正是投影的意义
    assert d["rows"] == 50
    assert d.get("truncated") is None


def test_小表一个字节都不动():
    df = pd.DataFrame({"日期": ["2026-09-21", "2026-09-22"], "收盘": [1580.0, 1592.5]})
    d = json.loads(to_json("stock_zh_a_daily", df))
    assert d["rows"] == 2 and d["total"] == 2
    assert d.get("truncated") is None
    assert d["data"][1]["收盘"] == 1592.5


def test_非DataFrame不炸():
    d = json.loads(to_json("some_scalar_api", "一段纯文本"))
    assert d["data"] == "一段纯文本"
