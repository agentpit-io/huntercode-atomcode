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
from unittest import mock
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


class FakeTable:
    """够用的 DataFrame 替身（公告那一路要的那几件事）：`len()`、按列取子表、
    `head(n)`、`to_dict("records")`。`columns` 故意做成可以缺列的 ——
    实测里 600519 那次回的就是一张列不全的表（akshare MCP 因此 KeyError）。
    """

    def __init__(self, columns, rows):
        self.columns = list(columns)
        self._rows = rows

    def __len__(self):
        return len(self._rows)

    def __getitem__(self, cols):
        for c in cols:
            if c not in self.columns:
                raise KeyError(c)            # 真 pandas 就是这么炸的，替身也得这么炸
        return FakeTable(cols, [{c: r.get(c) for c in cols} for r in self._rows])

    def head(self, n):
        return FakeTable(self.columns, self._rows[:n])

    def to_dict(self, how):
        assert how == "records"
        return list(self._rows)


class TestNotices(unittest.TestCase):
    """`stocks_intel` 的公告那一块。

    为什么有这一组：I2 的 opt-b 第 1 轮里，q5 题面点名要"公告"，而包里只有新闻，
    模型于是自己去走 akshare 三连补公告 —— 12 次调用 / 89.7 秒。补上公告之后，
    这一块必须满足两条才算真把那几步省掉：
      · **「没有公告」和「取不到公告」在返回里分得开**（题面要求"没有就直接说没有"）；
      · 上游回一张**列不全的表**时不许整块炸掉。
    """

    @classmethod
    def setUpClass(cls):
        cls.m = load_pack()

    def _fake_ak(self, table_or_exc):
        fake = types.ModuleType("akshare")

        def call(security, symbol=None, begin_date=None, end_date=None):
            self.seen = {"security": security, "symbol": symbol,
                         "begin_date": begin_date, "end_date": end_date}
            if isinstance(table_or_exc, Exception):
                raise table_or_exc
            return table_or_exc

        fake.stock_individual_notice_report = call
        sys.modules["akshare"] = fake

    def test_正常取到公告_只留四列(self):
        self._fake_ak(FakeTable(
            ["代码", "名称", "公告标题", "公告类型", "公告日期", "网址"],
            [{"代码": "601088", "名称": "中国神华", "公告标题": "投资者关系活动记录表",
              "公告类型": "调研活动", "公告日期": "2026-09-18", "网址": "http://x"}]))
        d = self.m._notices("601088", 7)
        self.assertNotIn("error", d)
        self.assertEqual(len(d["公告"]), 1)
        self.assertEqual(set(d["公告"][0]), {"代码", "公告标题", "公告类型", "公告日期"})
        self.assertIn("stock_individual_notice_report", d["source"])

    def test_空表是没有公告_不是取数失败(self):
        self._fake_ak(FakeTable([], []))
        d = self.m._notices("600519", 7)
        self.assertEqual(d["公告"], [])
        self.assertNotIn("error", d)          # 空 ≠ 失败，这一条是题面"没有就说没有"的底座

    def test_列不全时不炸_退回现有列(self):
        # 实测：600519 那次上游回的表里没有「代码」列，akshare MCP 因此 KeyError
        self._fake_ak(FakeTable(["公告标题", "公告日期"],
                                [{"公告标题": "股东大会通知", "公告日期": "2026-09-19"}]))
        d = self.m._notices("600519", 7)
        self.assertNotIn("error", d)
        self.assertEqual(d["公告"], [{"公告标题": "股东大会通知", "公告日期": "2026-09-19"}])

    def test_akshare挂了是error而不是空公告(self):
        self._fake_ak(RuntimeError("connection refused"))
        d = self.m._notices("600519", 7)
        self.assertIn("error", d)
        self.assertNotIn("公告", d)           # 不许把失败伪装成「没有公告」

    def test_列名KeyError按零条公告处理(self):
        """`KeyError: '代码'` 是 AKShare 在**零条公告**时的表现，不是取数失败。

        两条证据（都在容器里实测过）：
          · `_stock_notice_report` 先算 `total_page = ceil(total_hits / 100)`，
            为 0 时那个 for 一次都不进，`big_df` 保持空表，接着取列就 KeyError；
          · 直接打上游 `np-anotice-stock.eastmoney.com/api/security/ann`，
            600519 在 20260916~20260923 区间 `total_hits = 0`（601088 是 3）。

        报成 error 的后果很具体：题面要求「没有就直接说没有」，而模型看到 error
        会以为取数失败、再换个工具找一遍 —— 正是要消掉的那几步。
        """
        self._fake_ak(KeyError("代码"))
        d = self.m._notices("600519", 7)
        self.assertEqual(d["公告"], [])
        self.assertNotIn("error", d)
        self.assertIn("0 条公告", d["说明"])

    def test_不是列名的KeyError仍然报error(self):
        """只对「像列名」那种 KeyError 网开一面，别的一律照实报错。"""
        self._fake_ak(KeyError("some_internal_key"))
        d = self.m._notices("600519", 7)
        self.assertIn("error", d)
        self.assertNotIn("公告", d)

    def test_回溯天数按days算_区间写进返回(self):
        self._fake_ak(FakeTable([], []))
        d = self.m._notices("600519", 7)
        self.assertRegex(d["查询区间"], r"^\d{8} ~ \d{8}$")
        import datetime as _dt
        b, e = d["查询区间"].split(" ~ ")
        self.assertEqual((_dt.datetime.strptime(e, "%Y%m%d")
                          - _dt.datetime.strptime(b, "%Y%m%d")).days, 7)
        self.assertEqual(self.seen["security"], "600519")

    def test_公告进了stocks_intel的返回(self):
        m = load_pack(HUNTER_API_KEY="")
        self._fake_ak(FakeTable(["公告标题", "公告日期"],
                                [{"公告标题": "A", "公告日期": "2026-09-20"}]))
        with mock.patch.object(m, "_news", lambda c, limit, uid="": {"items": []}):
            out = json.loads(m.stocks_intel("600519,601088", limit=3, days=7))
        self.assertEqual(set(out["按票分组的公告"]), {"600519", "601088"})
        self.assertEqual(out["公告回溯天数"], 7)
        self.assertEqual(out["按票分组的公告"]["600519"]["公告"],
                         [{"公告标题": "A", "公告日期": "2026-09-20"}])


class TestAsOf(unittest.TestCase):
    """取数时刻必须有，而且**不能被说成行情时间戳**。

    上游 `stock_quickview` 的返回里一个时间字段都没有（I2 实测），
    而 q1 题面要求「给出最新股价并说明数据时点」—— 含糊带过就等于让模型
    把「我们打接口的时刻」当成「交易所的行情时刻」写进报告。
    """

    @classmethod
    def setUpClass(cls):
        cls.m = load_pack()

    def test_取数时刻是上海时间的合法时间串(self):
        import re
        self.assertRegex(self.m._now_sh(), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_说明里点明了这不是行情时间戳(self):
        note = self.m._QUOTE_ASOF_NOTE
        self.assertIn("没有行情时间戳", note)
        self.assertIn("截至本次取数", note)


class TestSessionIdentity(unittest.TestCase):
    """P0-5 在组合工具上的落法：**会话身份优先于容器级 HUNTER_USER_ID**。

    `hcapack` 内部打的就是 watchlist / portfolio 那一批 `/api/internal/*` 接口。
    guard hook 会把这个会话真正的 user_id 注入到 `_hermes_user_id`（M3 修的 P0-5）；
    拿不到才退回容器级的那个。顺序反了就是网页多用户形态下的串户。
    """

    def _capture(self, m):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["headers"] = {k.lower(): v for k, v in req.headers.items()}
            seen["body"] = json.loads(req.data.decode())

            class R:
                def read(self_inner):
                    return b"{}"

                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *a):
                    return False
            return R()
        return seen, fake_urlopen

    def test_注入的会话身份优先(self):
        m = load_pack(HUNTER_USER_ID="容器级用户", HUNTER_INTERNAL_KEY="k")
        seen, fake = self._capture(m)
        with mock.patch("urllib.request.urlopen", fake):
            m._api("stock_quickview", {"code": "600519"}, "会话真正的用户")
        self.assertEqual(seen["headers"].get("x-hunter-user-id"), "会话真正的用户")

    def test_没注入时才退回容器级(self):
        m = load_pack(HUNTER_USER_ID="容器级用户", HUNTER_INTERNAL_KEY="k")
        seen, fake = self._capture(m)
        with mock.patch("urllib.request.urlopen", fake):
            m._api("stock_quickview", {"code": "600519"}, "")
        self.assertEqual(seen["headers"].get("x-hunter-user-id"), "容器级用户")

    def test_下划线字段不进请求体(self):
        """后端对未知字段会 422 —— 内部字段只能走 header。"""
        m = load_pack(HUNTER_USER_ID="u", HUNTER_INTERNAL_KEY="k")
        seen, fake = self._capture(m)
        with mock.patch("urllib.request.urlopen", fake):
            m._api("stock_quickview", {"code": "600519"}, "u2")
        self.assertEqual(seen["body"], {"code": "600519"})

    def test_三个工具都收得下guard注入的身份(self):
        """参数名**不能**带下划线前缀 —— mcp 2.x 的 func_metadata 直接拒绝
        （`InvalidSignature: Parameter _hermes_user_id ... cannot start with '_'`，
        I2 实测 server 起都起不来）。所以 guard 那边要按 server 用不同的键。"""
        import inspect
        m = load_pack()
        for name in ("stock_snapshot", "stocks_intel", "thesis_evidence"):
            with self.subTest(name):
                params = inspect.signature(getattr(m, name)).parameters
                self.assertIn("hermes_user_id", params,
                              f"{name} 收不下 guard 注入的身份，调用会因为多了个参数而失败")
                self.assertNotIn("_hermes_user_id", params,
                                 f"{name} 用了下划线前缀的参数名，mcp 2.x 会拒绝加载")

    def test_guard按server用对了参数名(self):
        src = (REPO / "distro" / "workspace-template" / ".hooks" / "guard.py").read_text(
            encoding="utf-8")
        i = src.index("HUNTER_MCP_UID_FIELD = {")
        seg = src[i:i + 400]
        self.assertIn('"hcapack": "hermes_user_id"', seg)
        self.assertIn('"watchlist": "_hermes_user_id"', seg)


if __name__ == "__main__":
    unittest.main()


class TestThesisEvidencePack(unittest.TestCase):
    """`thesis_evidence` 必须把公告与新闻也带上。

    I2 的 opt-b 实测：q2 三次运行**每一次**都是 `thesis_evidence` → `stocks_intel`
    两连（论点里的证伪条件是「长协价跌破 X」这类要看最新消息才判得了的事），
    于是每次都多花一整轮模型。这一组钉住「包里有了，那一步才有理由不发生」。
    """

    @classmethod
    def setUpClass(cls):
        cls.m = load_pack(HUNTER_API_KEY="")

    def test_包里有公告和新闻两块(self):
        m = self.m
        fake = types.ModuleType("akshare")
        fake.stock_individual_notice_report = lambda **kw: FakeTable(
            ["公告标题", "公告日期"], [{"公告标题": "长协价公告", "公告日期": "2026-09-19"}])
        fake.stock_financial_abstract = lambda symbol: FakeDF(["选项", "指标"], [])
        fake.stock_dividend_cninfo = lambda symbol: FakeTable([], [])
        sys.modules["akshare"] = fake
        with mock.patch.object(m, "_api", lambda tool, body, uid="": {"tool": tool}), \
             mock.patch.object(m, "_news", lambda c, limit, uid="": {"items": [{"title": "新闻一条"}]}):
            out = json.loads(m.thesis_evidence("601088"))
        for block in ("论点原文", "持仓账本", "行情", "财务", "分红", "公告", "新闻"):
            self.assertIn(block, out, f"包里少了「{block}」这一块")
        self.assertEqual(out["公告"]["公告"],
                         [{"公告标题": "长协价公告", "公告日期": "2026-09-19"}])
        self.assertEqual(out["新闻"]["items"][0]["title"], "新闻一条")

    def test_某一块挂了其余块照样在(self):
        """总控红线 1 在组合工具上的落法：包是合起来的，出处是分开的。"""
        m = self.m
        fake = types.ModuleType("akshare")

        def boom(**kw):
            raise RuntimeError("connection refused")
        fake.stock_individual_notice_report = boom
        fake.stock_financial_abstract = lambda symbol: FakeDF(["选项", "指标"], [])
        fake.stock_dividend_cninfo = lambda symbol: FakeTable([], [])
        sys.modules["akshare"] = fake
        with mock.patch.object(m, "_api", lambda tool, body, uid="": {"tool": tool}), \
             mock.patch.object(m, "_news", lambda c, limit, uid="": {"items": []}):
            out = json.loads(m.thesis_evidence("601088"))
        self.assertIn("error", out["公告"])       # 挂了的那一块写 error
        self.assertIn("行情", out)                # 其余块照样在
        self.assertIn("新闻", out)


class TestNotInPack(unittest.TestCase):
    """包里**没有**的东西要在包里自己说出来。

    由来：`opt-fork-b` 的 q2 第 1 轮里，模型为了算分红率用了「总股本 198.7 亿股」——
    这个数不在任何一次工具返回里，是从记忆里拿的（`audit_numbers.py` 报 ✗，
    人工复核确认）。数值恰好是对的，但 A1 的口径是「每个数字都来自本次工具返回」。
    根因是数据缺口：`stock_quickview` 的 `valuation` 实测是空的 `{}`，
    这台机器又拉不通东方财富 `*_em`，巨潮的 profile 里也没有总股本。
    拿不到就要说拿不到 —— 所以包里明写一行。
    """

    @classmethod
    def setUpClass(cls):
        cls.m = load_pack(HUNTER_API_KEY="")

    def _ak(self):
        fake = types.ModuleType("akshare")
        fake.stock_financial_abstract = lambda symbol: FakeDF(["选项", "指标"], [])
        fake.stock_dividend_cninfo = lambda symbol: FakeTable([], [])
        fake.stock_individual_notice_report = lambda **kw: FakeTable([], [])
        sys.modules["akshare"] = fake

    def test_两个带行情的包都声明了缺什么(self):
        m = self.m
        self._ak()
        with mock.patch.object(m, "_api", lambda tool, body, uid="": {"tool": tool}), \
             mock.patch.object(m, "_news", lambda c, limit, uid="": {"items": []}):
            for fn in (m.stock_snapshot, m.thesis_evidence):
                out = json.loads(fn("601088"))
                self.assertIn("本包未提供", out, fn.__name__)
                note = out["本包未提供"]
                for kw in ("总股本", "取不到", "不要用记忆里的数"):
                    self.assertIn(kw, note, f"{fn.__name__} 的声明里少了「{kw}」")

    def test_声明里点明了为什么拿不到(self):
        """只说「没有」不够 —— 说清是上游空字段还是本机房拉不通，
        否则下一个人会以为是我们忘了加。"""
        note = self.m._NOT_IN_PACK
        self.assertIn("valuation", note)
        self.assertIn("_em", note)
