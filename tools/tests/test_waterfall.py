"""I2 · 瀑布切分（`eval_atomcode.waterfall`）的单测。

切分只依赖事件到达的先后，不依赖任何 daemon 内部字段 —— 所以能用
合成事件流完整覆盖：单工具题、多工具题、纯文本题、缺时间戳的事件。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "eval"))
from eval_atomcode import waterfall  # noqa: E402


def ev(t, **kw):
    d = {"_t_ms": t}
    d.update(kw)
    return d


class TestWaterfall(unittest.TestCase):
    def test_单工具题切成_首轮模型_工具_二轮模型(self):
        events = [
            ev(None, type="snapshot"),
            ev(120.0, type="state", running=True),
            ev(5000.0, type="tool_start", name="mcp__screener__market_screen"),
            ev(6100.0, type="tool_result", name="mcp__screener__market_screen",
               duration_ms=1050),
            ev(9000.0, type="text", content="结论"),
            ev(14600.0, type="state", running=False),
        ]
        w = waterfall(events)
        kinds = [(s["kind"], s["ms"]) for s in w["segments"]]
        self.assertEqual(kinds[0], ("model", 5000.0))      # 首轮模型调用
        self.assertEqual(kinds[1], ("tool", 1100.0))       # 工具执行（含 hook）
        self.assertEqual(kinds[2], ("model", 2900.0))      # 二轮模型调用到首字
        self.assertEqual(kinds[3][0], "tail")
        self.assertEqual(w["totals"]["tool_ms"], 1100.0)
        self.assertEqual(w["totals"]["model_ms"], 7900.0)

    def test_工具段的自报耗时单独留一列(self):
        events = [ev(0.0, type="state", running=True),
                  ev(1000.0, type="tool_start", name="t"),
                  ev(3000.0, type="tool_result", name="t", duration_ms=1400)]
        seg = [s for s in waterfall(events)["segments"] if s["kind"] == "tool"][0]
        self.assertEqual(seg["ms"], 2000.0)                # SSE 上看到的两端之差
        self.assertEqual(seg["duration_ms_self"], 1400)    # 工具自己报的

    def test_多工具题按轮次递增(self):
        events = [ev(0.0, type="state", running=True)]
        t = 1000.0
        for i in range(3):
            events.append(ev(t, type="tool_start", name=f"t{i}"))
            events.append(ev(t + 500, type="tool_result", name=f"t{i}", duration_ms=400))
            t += 2000
        events.append(ev(t, type="text", content="答"))
        w = waterfall(events)
        tools = [s for s in w["segments"] if s["kind"] == "tool"]
        self.assertEqual([s["round"] for s in tools], [1, 2, 3])
        self.assertEqual(w["totals"]["tool_ms"], 1500.0)

    def test_纯文本题只有一段模型调用(self):
        events = [ev(0.0, type="state", running=True),
                  ev(3200.0, type="text", content="我拿不到"),
                  ev(3900.0, type="state", running=False)]
        w = waterfall(events)
        self.assertEqual([s["kind"] for s in w["segments"]], ["model", "tail"])
        self.assertEqual(w["totals"]["tool_ms"], 0)

    def test_只有第一段文本算首字_后续增量不再切段(self):
        events = [ev(0.0, type="state", running=True),
                  ev(2000.0, type="text", content="第"),
                  ev(2100.0, type="text", content="二"),
                  ev(2200.0, type="text", content="片")]
        w = waterfall(events)
        self.assertEqual(len([s for s in w["segments"] if s["kind"] == "model"]), 1)

    def test_没有时间戳的事件被跳过而不是当成0(self):
        events = [ev(None, type="snapshot"), ev(None, type="user"),
                  ev(1000.0, type="text", content="x")]
        w = waterfall(events)
        self.assertEqual(w["segments"][0]["ms"], 1000.0)

    def test_空事件流不炸(self):
        w = waterfall([])
        self.assertEqual(w["segments"], [])
        self.assertEqual(w["totals"], {"model_ms": 0, "tool_ms": 0, "tail_ms": 0})


if __name__ == "__main__":
    unittest.main()
