"""I2 · 组合工具 MCP（hcapack）的纯逻辑单测。

`hca_pack_mcp` 顶层要 `mcp` 与（懒加载的）`akshare`，开发机上都没有 ——
用假模块顶掉 `mcp` 再 import，测的是我们自己写的那部分：

  · 组合包的**诚实性**：每一块要么有数据要么有 error，子调用挂了不影响其余块；
  · 财务表的取字段与 NaN 处理（**不补 0、不拿上一期顶替**）；
  · 并行取数保持声明顺序、单个 job 抛异常不拖垮整包；
  · 工作区读取不许越界。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def load_pack(**env):
    """用假 mcp 模块顶掉依赖后 import hca_pack_mcp。"""
    fake = types.ModuleType("mcp")
    server = types.ModuleType("mcp.server")
    mcpserver = types.ModuleType("mcp.server.mcpserver")

    class MCPServer:
        def __init__(self, name):
            self.name = name

        def tool(self, *a, **kw):
            def deco(fn):
                return fn
            return deco

        def run(self, **kw):
            raise AssertionError("单测不该真的起 server")

    mcpserver.MCPServer = MCPServer
    server.mcpserver = mcpserver
    fake.server = server
    for k, v in (("mcp", fake), ("mcp.server", server), ("mcp.server.mcpserver", mcpserver)):
        sys.modules[k] = v
    sys.path.insert(0, str(REPO / "tools" / "opencode-mcp"))
    old = {k: os.environ.get(k) for k in env}
    os.environ.update({k: str(v) for k, v in env.items()})
    try:
        spec = importlib.util.spec_from_file_location(
            "hca_pack_under_test", REPO / "tools" / "opencode-mcp" / "hca_pack_mcp.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["hca_pack_under_test"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class FakeDF:
    """够用的 DataFrame 替身：只支持 columns / iterrows。"""

    def __init__(self, columns, rows):
        self.columns = columns
        self._rows = rows

    def iterrows(self):
        return enumerate(self._rows)


class TestFinancials(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_pack()

    def _fake_ak(self, df):
        fake = types.ModuleType("akshare")
        fake.stock_financial_abstract = lambda symbol: df
        sys.modules["akshare"] = fake

    def test_取出八个指标并保留报告期顺序(self):
        cols = ["选项", "指标", "20260630", "20260331", "20251231",
                "20250930", "20250630", "20250331"]
        rows = [{"选项": "常用指标", "指标": "营业总收入", "20260630": 1.0, "20260331": 2.0,
                 "20251231": 3.0, "20250930": 4.0, "20250630": 5.0, "20250331": 6.0},
                {"选项": "成长能力", "指标": "营业总收入增长率", "20260630": 7.9323,
                 "20260331": 1.0, "20251231": 1.0, "20250930": 1.0, "20250630": 1.0,
                 "20250331": 1.0}]
        self._fake_ak(FakeDF(cols, rows))
        d = self.m._financials("601088")
        self.assertEqual(d["报告期"], ["20260630", "20260331", "20251231",
                                       "20250930", "20250630"])   # PERIODS=5
        self.assertEqual(d["指标"]["营业总收入_元"]["20260630"], 1.0)
        self.assertEqual(d["指标"]["营业总收入同比_%"]["20260630"], 7.9323)
        self.assertIn("未取到的指标", d)                            # 其余 6 个没给

    def test_NaN写null而不是补0(self):
        nan = float("nan")
        cols = ["选项", "指标", "20260630", "20260331"]
        rows = [{"选项": "常用指标", "指标": "毛利率", "20260630": nan, "20260331": 35.4}]
        self._fake_ak(FakeDF(cols, rows))
        d = self.m._financials("600519")
        self.assertIsNone(d["指标"]["毛利率_%"]["20260630"])
        self.assertEqual(d["指标"]["毛利率_%"]["20260331"], 35.4)

    def test_同名指标只取第一份(self):
        cols = ["选项", "指标", "20260630"]
        rows = [{"选项": "常用指标", "指标": "毛利率", "20260630": 1.0},
                {"选项": "盈利能力", "指标": "毛利率", "20260630": 999.0}]
        self._fake_ak(FakeDF(cols, rows))
        d = self.m._financials("600519")
        self.assertEqual(d["指标"]["毛利率_%"]["20260630"], 1.0)

    def test_akshare挂了返回error而不是空壳(self):
        fake = types.ModuleType("akshare")

        def boom(symbol):
            raise RuntimeError("上游 503")
        fake.stock_financial_abstract = boom
        sys.modules["akshare"] = fake
        d = self.m._financials("601088")
        self.assertIn("error", d)
        self.assertIn("503", d["error"])


class TestParallel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_pack()

    def test_保持声明顺序(self):
        import time
        jobs = {"慢": lambda: (time.sleep(0.05), "a")[1], "快": lambda: "b"}
        self.assertEqual(list(self.m._parallel(jobs)), ["慢", "快"])

    def test_单个job抛异常不拖垮整包(self):
        def boom():
            raise ValueError("炸")
        got = self.m._parallel({"好": lambda: {"ok": 1}, "坏": boom})
        self.assertEqual(got["好"], {"ok": 1})
        self.assertIn("error", got["坏"])
        self.assertIn("ValueError", got["坏"]["error"])

    def test_空任务不炸(self):
        self.assertEqual(self.m._parallel({}), {})


class TestWorkspaceRead(unittest.TestCase):
    def test_只读工作区内的文件(self):
        with tempfile.TemporaryDirectory() as ws:
            Path(ws, "theses").mkdir()
            Path(ws, "theses", "601088.md").write_text("我的论点", encoding="utf-8")
            m = load_pack(HCA_WORKSPACE=ws)
            self.assertEqual(m._read_workspace("theses/601088.md")["text"], "我的论点")
            self.assertIn("error", m._read_workspace("theses/没有的.md"))
            # 越界必须挡住 —— 组合工具也是工具，同样受工作区边界约束
            self.assertIn("工作区外", m._read_workspace("../../etc/passwd")["error"])


class TestCodes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_pack()

    def test_中英文逗号都认_且最多十只(self):
        self.assertEqual(self.m._codes("600519，601088, 300750"),
                         ["600519", "601088", "300750"])
        self.assertEqual(len(self.m._codes(",".join(str(i) for i in range(30)))), 10)

    def test_空串返回空列表(self):
        self.assertEqual(self.m._codes(""), [])
        self.assertEqual(self.m._codes(None), [])


class TestTruesource(unittest.TestCase):
    def test_没有key时明说而不是假装没信号(self):
        m = load_pack(HUNTER_API_KEY="")
        d = m._truesource_brief("600519")
        self.assertIn("error", d)
        self.assertIn("HUNTER_API_KEY", d["error"] + json.dumps(d, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
