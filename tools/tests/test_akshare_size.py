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

try:
    import pytest
    pd = pytest.importorskip("pandas", reason="开发机没装 pandas；容器里跑才是真证据")
except ImportError:
    # 容器里没有 pytest（镜像精简过，连 pip 都没有），而这几条**只有容器里跑才算数**。
    # 走文件末尾的自跑入口时直接 import pandas —— 容器里它是有的。
    import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "akshare-mcp"))


def _server_src() -> Path:
    """找到被测的 server.py。

    三处候选，按优先级：`HCA_AKSHARE_SERVER` 环境变量 → 仓库内相对路径 →
    容器里的安装位置。加后两个是因为这份文件**要被 cp 进容器单独跑**
    （容器里没有 pytest 也没有 pip，见文件末尾的自跑入口），
    那时 `parents[1]` 指向的是 /tmp，仓库结构不在。
    """
    import os
    cands = [
        Path(os.environ["HCA_AKSHARE_SERVER"]) if os.environ.get("HCA_AKSHARE_SERVER") else None,
        Path(__file__).resolve().parents[1] / "akshare-mcp" / "akshare_mcp" / "server.py",
        Path("/opt/hca/tools/akshare-mcp/akshare_mcp/server.py"),
    ]
    for c in cands:
        if c and c.is_file():
            return c
    raise FileNotFoundError(
        "找不到 akshare 的 server.py —— 用 HCA_AKSHARE_SERVER 指一下。"
        f"试过：{[str(c) for c in cands if c]}")


def _server(monkeypatch=None):
    """akshare_mcp.server 顶层 import akshare，开发机没有 —— 只取纯函数。"""
    src = _server_src().read_text(encoding="utf-8")
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


def test_单行长文本也能压进预算():
    """回归：只限行数压不住**长文本单元格**。

    公告 / 研报 / 新闻正文这类接口，一行里的某一列就有几万字。M5 加的行数二分
    在这里会一路砍到 1 行然后原样返回 —— 实测 `stock_notice_report` 形状的
    单行仍返回 **90 168 字节**（内核阈值的 5.5 倍），照样被内核砍成断裂 JSON。
    所以补了第三道：裁单元格。
    """
    to_json, MAX_BYTES = NS["_to_json"], NS["MAX_BYTES"]
    df = pd.DataFrame([{"标题": "某公司年度报告", "日期": "2026-09-22", "全文": "甲" * 30000}])
    out = to_json("stock_notice_report", df)
    assert len(out.encode("utf-8")) <= MAX_BYTES
    assert len(out.encode("utf-8")) <= 16 * 1024          # 更要紧：内核阈值
    d = json.loads(out)                                   # 仍是合法 JSON
    assert d["cells_truncated"] == 1
    assert "不是不存在" in d["cells_truncated_note"]
    assert "只给了前" in d["data"][0]["全文"]              # 裁过的标了原长度


def test_多行长文本二分到一行仍超时会改裁单元格():
    to_json, MAX_BYTES = NS["_to_json"], NS["MAX_BYTES"]
    df = pd.DataFrame([{"标题": f"公告{i}", "全文": "乙" * 8000} for i in range(20)])
    out = to_json("probe", df)
    d = json.loads(out)
    assert len(out.encode("utf-8")) <= MAX_BYTES
    assert d["rows"] == 1 and d["total"] == 20            # 行数先被压到 1
    assert d["truncated"] is True                         # 行被裁要说
    assert d["cells_truncated"] == 1                      # 单元格也被裁了


def test_没超预算的小表一个字都不动():
    to_json = NS["_to_json"]
    df = pd.DataFrame([{"代码": "600519", "价格": 1680.5}])
    d = json.loads(to_json("probe", df))
    assert d["rows"] == 1 and d["total"] == 1
    assert "truncated" not in d and "cells_truncated" not in d
    assert d["data"][0]["价格"] == 1680.5





def test_非DataFrame的超大返回也守预算():
    """回归：标量路径原先按**原始**字节截（`MAX_BYTES - 500`），之后才 json.dumps。

    与第 3 档同一个坑 —— JSON 转义会膨胀，一段全是 `"` 和 `\\` 的文本进 JSON
    后体积翻倍，裁完照样超预算、照样被内核砍成断裂 JSON。
    改成对**序列化之后**的字节数二分。
    """
    to_json, MAX_BYTES = NS["_to_json"], NS["MAX_BYTES"]
    for name, payload in [
        ("全转义字符", '"\\' * 20000),
        ("长中文", "甲" * 30000),
        ("普通长文本", "x" * 60000),
    ]:
        out = to_json("some_scalar_api", payload)
        assert len(out.encode("utf-8")) <= MAX_BYTES, f"{name} 超预算：{len(out.encode())}"
        assert len(out.encode("utf-8")) <= 16 * 1024, f"{name} 超内核阈值"
        d = json.loads(out)                                # 仍是合法 JSON
        assert "不是不存在" in d["data"]                    # 裁了要说


def test_小标量原样给不加工():
    to_json = NS["_to_json"]
    d = json.loads(to_json("some_scalar_api", "一段纯文本"))
    assert d["data"] == "一段纯文本"
    d2 = json.loads(to_json("some_dict_api", {"a": 1, "b": [1, 2, 3]}))
    assert d2["data"] == {"a": 1, "b": [1, 2, 3]}


# ---------------------------------------------------------------------------
# 容器里没有 pytest 也没有 pip（镜像精简过），而这几条**只有在容器里跑才算数**
# （开发机没 pandas）。所以给一个零依赖的自跑入口：
#
#     docker cp tools/tests/test_akshare_size.py hca-daemon:/tmp/
#     docker exec hca-daemon /opt/hca/venv/bin/python /tmp/test_akshare_size.py
#
# 必须裹在 __main__ 里 —— 模块级的 sys.exit() 会让 pytest 收集阶段直接崩。
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import traceback

    fails = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"  过  {_name}")
            except Exception:                              # noqa: BLE001
                fails += 1
                print(f"  失败 {_name}")
                traceback.print_exc()
    print(f"\n共 {sum(1 for k, v in globals().items() if k.startswith('test_') and callable(v))} 条，失败 {fails}")
    sys.exit(1 if fails else 0)
