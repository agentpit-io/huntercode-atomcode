"""I2 · 瀑布切分（`eval_atomcode.waterfall`）的单测。

切分只依赖事件到达的先后，不依赖任何 daemon 内部字段 —— 所以能用
合成事件流完整覆盖：单工具题、多工具题、纯文本题、缺时间戳的事件。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "eval"))
from eval_atomcode import waterfall  # noqa: E402
from eval_opencode import flatten as oc_flatten, waterfall as oc_waterfall  # noqa: E402


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
        self.assertEqual(w["totals"],
                         {"model_ms": 0, "tool_ms": 0.0, "tool_sum_ms": 0, "tail_ms": 0})


if __name__ == "__main__":
    unittest.main()


class TestSplitTail(unittest.TestCase):
    """`tail` 要能劈成「出字」与「收尾」两段。

    这两段的性质完全不同：出字长短由正文决定，砍它就是砍内容；收尾与正文无关，
    砍它不损失任何东西。I2 追平社区版差的就是这个量级，所以不能只有回归出来的截距。
    """

    def test_出字与收尾分开算(self):
        events = [
            ev(120.0, type="state", running=True),
            ev(4000.0, type="text", content="第一块"),
            ev(4500.0, type="text", content="第二块"),
            ev(7000.0, type="text", content="最后一块"),
            ev(8400.0, type="state", running=False),
        ]
        t = waterfall(events)["totals"]
        self.assertEqual(t["stream_ms"], 3000.0)      # 4000 → 7000
        self.assertEqual(t["finish_ms"], 1400.0)      # 7000 → 8400
        self.assertEqual(t["text_chunks"], 3)
        # 两段加起来正好是原来那个 tail（首个 text → 结束）
        self.assertEqual(t["stream_ms"] + t["finish_ms"], t["tail_ms"])

    def test_只有一块文本时出字为零收尾是全部(self):
        events = [ev(120.0, type="state", running=True),
                  ev(3000.0, type="text", content="就一块"),
                  ev(4200.0, type="state", running=False)]
        t = waterfall(events)["totals"]
        self.assertEqual(t["stream_ms"], 0.0)
        self.assertEqual(t["finish_ms"], 1200.0)

    def test_没有文本或没有结束事件时不写这两项(self):
        """拿不到就不写，不拿 0 顶 —— 0 会被读成「收尾不花时间」。"""
        for events in ([ev(120.0, type="state", running=True),
                        ev(900.0, type="state", running=False)],
                       [ev(120.0, type="state", running=True),
                        ev(900.0, type="text", content="没有结束事件")]):
            t = waterfall(events)["totals"]
            self.assertNotIn("stream_ms", t)
            self.assertNotIn("finish_ms", t)


def oc(msgs):
    """社区版侧：把 `GET /session/{id}/message` 的形状喂进去。"""
    return oc_waterfall(oc_flatten(msgs))


class TestOpencodeWaterfall(unittest.TestCase):
    """社区版侧的瀑布表。以前这边只有一个总墙钟，于是「引擎侧开销」是个减法余项。"""

    def test_单工具题切成_模型_工具_模型_出字(self):
        msgs = [
            {"info": {"role": "user", "time": {"created": 1000}}, "parts": []},
            {"info": {"role": "assistant", "time": {"created": 1010, "completed": 6000}},
             "parts": [{"type": "step-start"},
                       {"type": "tool", "tool": "screener_market_screen",
                        "state": {"status": "completed", "input": {},
                                  "time": {"start": 5000, "end": 5050}}}]},
            {"info": {"role": "assistant", "time": {"created": 6010, "completed": 9000}},
             "parts": [{"type": "text", "text": "x" * 600,
                        "time": {"start": 7000, "end": 8700}}]},
        ]
        w = oc(msgs)
        kinds = [(s["kind"], s["ms"]) for s in w["segments"]]
        # 口径与 HCA 侧一致：段落起点是「上一段结束」，不是 assistant 消息的 created
        # —— 工具结束(5050) 到下一条消息被创建(6010) 之间那 960 ms 是引擎在跑下一步，
        # 算进第二轮模型，不许扣掉。
        self.assertEqual(kinds, [("model", 4000.0), ("tool", 50.0),
                                 ("model", 1950.0), ("stream", 1700.0)])
        self.assertEqual(w["totals"]["model_ms"], 5950.0)
        self.assertEqual(w["totals"]["stream_ms"], 1700.0)

    def test_并行工具不许算出负数段(self):
        """同一条消息里的三个 tool part 是并行发的，后两个的 start 早于前一个的 end。
        第一版按「上一段 end = 下一段 start」串着算，q5 上出了三个负值段。"""
        msgs = [
            {"info": {"role": "user", "time": {"created": 0}}, "parts": []},
            {"info": {"role": "assistant", "time": {"created": 10}},
             "parts": [{"type": "tool", "tool": "a",
                        "state": {"time": {"start": 4000, "end": 6700}}},
                       {"type": "tool", "tool": "b",
                        "state": {"time": {"start": 4200, "end": 7600}}},
                       {"type": "tool", "tool": "c",
                        "state": {"time": {"start": 4210, "end": 8100}}}]},
            {"info": {"role": "assistant", "time": {"created": 8110}},
             "parts": [{"type": "text", "text": "hi", "time": {"start": 10500, "end": 11600}}]},
        ]
        w = oc(msgs)
        for s in w["segments"]:
            if s["ms"] is not None:
                self.assertGreaterEqual(s["ms"], 0, s)
        # 并行的三个工具只该在前面记一次模型等待，不是三次
        self.assertEqual(sum(1 for s in w["segments"] if s["kind"] == "model"), 2)

    def test_没有用户消息时间戳就整个不出瀑布(self):
        self.assertEqual(oc([{"info": {"role": "assistant"}, "parts": []}]), {})

    def test_不写finish那一段(self):
        """社区版探针是阻塞 POST，没有「最后一块文本 → 运行结束」这个可观测事件。
        缺项就是缺项，不许拿 0 顶 —— 拿 0 顶会让「HCA 收尾更慢」看起来像量出来的。"""
        msgs = [{"info": {"role": "user", "time": {"created": 0}}, "parts": []},
                {"info": {"role": "assistant", "time": {"created": 10}},
                 "parts": [{"type": "text", "text": "x", "time": {"start": 100, "end": 200}}]}]
        self.assertNotIn("finish_ms", oc(msgs)["totals"])


class TestParallelTools(unittest.TestCase):
    """并行工具调用：`tool_ms` 必须是并集，中间不许冒出一段「模型」。

    第一版在 q1 上把三个并行调用各算一遍，`tool_ms` 加出 54 355 ms（墙钟只有
    42 765 ms），还在两个 `tool_start` 之间记了一段 **25 535 ms 的「模型调用」**
    —— 那段时间里模型什么都没干，是工具在跑。
    """

    def _parallel_events(self):
        return [
            ev(120.0, type="state", running=True),
            ev(4756.0, type="tool_start", name="a"),
            ev(7422.0, type="tool_start", name="b"),
            ev(9000.0, type="tool_start", name="c"),
            ev(33061.0, type="tool_result", name="c", duration_ms=103),
            ev(33162.0, type="tool_result", name="b", duration_ms=25000),
            ev(33266.0, type="tool_result", name="a", duration_ms=28000),
            ev(39202.0, type="text", content="结论"),
            ev(42764.0, type="state", running=False),
        ]

    def test_工具时间取并集而不是加法(self):
        t = waterfall(self._parallel_events())["totals"]
        self.assertEqual(t["tool_ms"], 33266.0 - 4756.0)      # 并集
        self.assertGreater(t["tool_sum_ms"], t["tool_ms"])    # 加法会超出
        self.assertEqual(t["tool_sum_ms"],
                         (33266 - 4756) + (33162 - 7422) + (33061 - 9000))

    def test_工具在跑的时候不记模型段(self):
        segs = waterfall(self._parallel_events())["segments"]
        models = [s for s in segs if s["kind"] == "model"]
        # 只该有两段：首轮（0 → 4756）与末轮（33266 → 39202）
        self.assertEqual([round(s["ms"]) for s in models], [4756, 5936])

    def test_轮次只在工具全回来之后才加一(self):
        segs = waterfall(self._parallel_events())["segments"]
        self.assertEqual(max(s["round"] for s in segs), 2)

    def test_四类段落之和等于墙钟(self):
        t = waterfall(self._parallel_events())["totals"]
        total = t["model_ms"] + t["tool_ms"] + t["tail_ms"]
        self.assertAlmostEqual(total, 42764.0, delta=1.0)

    def test_出字起点是末轮的第一块文本_不是路标(self):
        """人设要求模型在一批工具调用前先发一行「路标」，所以整次运行的第一块文本
        往往在工具之前。拿它当出字起点，出字段会横跨整次运行。"""
        events = [
            ev(120.0, type="state", running=True),
            ev(4300.0, type="text", content="我先查一下行情"),     # 路标，在工具之前
            ev(4400.0, type="tool_start", name="a"),
            ev(5000.0, type="tool_result", name="a", duration_ms=600),
            ev(8000.0, type="text", content="结论第一块"),
            ev(9500.0, type="text", content="结论最后一块"),
            ev(10900.0, type="state", running=False),
        ]
        t = waterfall(events)["totals"]
        self.assertEqual(t["stream_ms"], 1500.0)              # 8000 → 9500
        self.assertEqual(t["finish_ms"], 1400.0)              # 9500 → 10900
        self.assertEqual(t["stream_ms"] + t["finish_ms"], t["tail_ms"])

