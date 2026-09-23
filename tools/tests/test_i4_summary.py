"""I4 · 响应时间汇总（`i4_summary`）的单测。

两件事必须钉住，因为它们错了报告不会报错、只会给出一个看着很像样的假结论：

1. **两侧的分段是不是同一件事。** 社区版那边每个 text part 都单独成了 `stream` 段，
   HCA 那边一轮中间的正文落在模型段里 —— 不把社区版的非末轮正文折进模型段，
   「HCA 模型段更长」就是口径差造成的假象。
2. **「都真做题」的判据会不会误伤。** 它只认「向用户索要材料」这一类措辞，
   且不作用于 q4 / q10（那两道题的正确答案本来就可能是「拿不到」「我不做」）。
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "eval"))
import i4_summary as S  # noqa: E402


def hca_rec(**kw):
    """HCA 侧一次运行：瀑布 totals 已由探针算好，segments 给出末轮判据要用的形状。"""
    rec = {
        "side": "atomcode", "wall_ms": 20000, "text_len": 1000, "text": "结论如下……",
        "waterfall": {
            "totals": {"model_ms": 12000.0, "tool_ms": 3000.0, "tail_ms": 5000.0,
                       "stream_ms": 4900.0, "finish_ms": 100.0},
            "segments": [
                {"kind": "model", "round": 1, "start_ms": 0.0, "end_ms": 9000.0,
                 "ms": 9000.0},
                {"kind": "tool", "round": 1, "start_ms": 9000.0, "end_ms": 12000.0,
                 "ms": 3000.0},
                {"kind": "model", "round": 2, "text_start": True, "start_ms": 12000.0,
                 "end_ms": 15000.0, "ms": 3000.0},
            ],
        },
    }
    rec.update(kw)
    return rec


def oc_rec(**kw):
    """社区版一次运行：一轮中间吐了 800 ms 正文（路标那一类），末轮出字 1 200 ms。"""
    rec = {
        "side": "opencode", "wall_ms": 20000, "text_len": 400, "text": "结论如下……",
        "waterfall": {
            "totals": {"model_ms": 11000.0, "tool_ms": 3000.0, "tool_sum_ms": 3000.0,
                       "stream_ms": 2000.0},
            "segments": [
                {"kind": "model", "round": 1, "start_ms": 0.0, "end_ms": 5000.0,
                 "ms": 5000.0},
                {"kind": "stream", "round": 1, "chars": 20, "start_ms": 5000.0,
                 "end_ms": 5800.0, "ms": 800.0},
                {"kind": "model", "round": 1, "start_ms": 5800.0, "end_ms": 9000.0,
                 "ms": 3200.0},
                {"kind": "tool", "round": 1, "start_ms": 9000.0, "end_ms": 12000.0,
                 "ms": 3000.0},
                {"kind": "model", "round": 2, "text_start": True, "start_ms": 12000.0,
                 "end_ms": 14800.0, "ms": 2800.0},
                {"kind": "stream", "round": 2, "chars": 380, "start_ms": 14800.0,
                 "end_ms": 16000.0, "ms": 1200.0},
            ],
        },
    }
    rec.update(kw)
    return rec


class Test分段口径(unittest.TestCase):
    def test_社区版的非末轮正文折进模型段(self):
        s = S.segments_of(oc_rec())
        # totals.model_ms 是 11 000，非末轮的 stream 有 800 → 折进来是 11 800
        self.assertEqual(s["model_ms"], 11800.0)
        # 出字段只认末轮那一块：16 000 − 14 800
        self.assertEqual(s["stream_ms"], 1200.0)
        # 答案首字 = 末轮第一块正文开始
        self.assertEqual(s["ttft_answer_ms"], 14800.0)
        # 首字 = 第一块正文到达（路标那一块）
        self.assertEqual(s["ttft_ms"], 5000.0)
        self.assertTrue(s["ttft_is_signpost"])

    def test_社区版收尾段量不到就写null不写0(self):
        s = S.segments_of(oc_rec())
        self.assertIsNone(s["finish_ms"])
        # 残差是它的上界：20 000 − (11 800 + 3 000 + 1 200)
        self.assertEqual(s["residual_ms"], 4000.0)

    def test_HCA四段之和等于墙钟(self):
        s = S.segments_of(hca_rec())
        acc = s["model_ms"] + s["tool_ms"] + s["stream_ms"] + s["finish_ms"]
        self.assertAlmostEqual(acc, 20000.0, places=1)
        self.assertAlmostEqual(s["residual_ms"], 0.0, places=1)
        # 答案首字 = 墙钟 − tail
        self.assertEqual(s["ttft_answer_ms"], 15000.0)
        self.assertEqual(s["ttft_ms"], 15000.0)
        self.assertFalse(s["ttft_is_signpost"])   # 首字在工具之后，不是路标

    def test_首字早于第一次工具调用才算路标(self):
        r = hca_rec()
        r["waterfall"]["segments"] = [
            {"kind": "model", "round": 1, "text_start": True, "start_ms": 0.0,
             "end_ms": 2000.0, "ms": 2000.0},
            {"kind": "tool", "round": 1, "start_ms": 3000.0, "end_ms": 6000.0,
             "ms": 3000.0},
        ]
        s = S.segments_of(r)
        self.assertEqual(s["ttft_ms"], 2000.0)
        self.assertTrue(s["ttft_is_signpost"])

    def test_一次工具都没调的运行不算路标(self):
        r = hca_rec()
        r["waterfall"]["segments"] = [
            {"kind": "model", "round": 1, "text_start": True, "start_ms": 0.0,
             "end_ms": 2000.0, "ms": 2000.0},
        ]
        self.assertFalse(S.segments_of(r)["ttft_is_signpost"])

    def test_每字耗时_正文为空就不给不拿0顶(self):
        self.assertIsNone(S.segments_of(hca_rec(text_len=0))["ms_per_char"])
        self.assertAlmostEqual(S.segments_of(hca_rec())["ms_per_char"], 4.9, places=3)

    def test_多轮题各段相加而首字取第一轮(self):
        t1 = hca_rec(wall_ms=8000)
        t2 = hca_rec(wall_ms=9000)
        t2["waterfall"]["segments"] = [
            {"kind": "model", "round": 1, "text_start": True, "start_ms": 0.0,
             "end_ms": 1000.0, "ms": 1000.0},
        ]
        rec = {"side": "atomcode", "wall_ms": 30000, "text_len": 2000, "text": "x",
               "turns": [t1, t2]}
        s = S.segments_of(rec)
        self.assertEqual(s["model_ms"], 24000.0)       # 12 000 × 2
        self.assertEqual(s["ttft_ms"], 15000.0)        # 第 1 轮的
        self.assertEqual(s["wall_ms"], 30000)          # 整次运行的墙钟，不是各轮之和


class Test真做题判据(unittest.TestCase):
    """判据逐句判：一句话里「祈使 + 索要动作 + 要的是这道题的材料」三件事齐备才算
    把任务退回给用户。下面这些句子全部取自真实运行记录（I1 + I4 共 597 次里命中的
    12 次，逐条人工核过）。"""

    def test_把任务退回给用户的判为没做题(self):
        for t in [  # I1 c1-b r1 / c1-a r2 / c1-b r3 / I4 i4-b r1 的原话
                "请提供你当初买入中国神华（601088）时记录的具体买入理由与证伪条件文本。",
                "请将您当初买入中国神华时写下的具体买入理由及设定的证伪条件发给我。",
                "请把您当初记下的具体逻辑发出来（例如高股息分红率、长协煤占比）。",
                "请直接将您当初写下的买入理由清单及证伪条件发在对话中，我将逐条复核。"]:
            self.assertFalse(S.did_the_work({"text": t}, "q2-thesis-review"), t)

    def test_只说了没有材料但没开口要不算退回(self):
        """「我手上没有这份材料」是**陈述**，后面照样可以接着做题。
        判据认的是「冲着用户去的祈使句」，不认陈述 —— 不然「分红率数据不足」
        这类正常的证据缺口会被整片剔掉。597 次记录里没有一次是只陈述不索要的。"""
        t = "系统未读取到您此前记录的具体“买入理由”文本，以下按持仓账本中的三条理由复核。"
        self.assertTrue(S.did_the_work({"text": t}, "q2-thesis-review"))

    def test_真做了的不误伤(self):
        for t in ["买入理由逐条复核：1. 长协煤比例高 —— 成立。2026 年中报营收同比增长 7.93%。",
                  "近 30 天无上榜，历史最近一次上榜是 2020 年 7 月 7 日。",
                  "筛出 137 只，前 10 只如下：600519 贵州茅台 ROE 16.75% PE-TTM 22.1。"]:
            self.assertTrue(S.did_the_work({"text": t}, "q2-thesis-review"), t)

    def test_旧判据误伤过的两句现在不误伤(self):
        """这两句是 HCA 在 i4-b 里**正常作答**时写的，旧的全文片段匹配把它们
        判成了没做题（`尚未.{0,8}提供`）。它们都是在描述证据缺口，不是索要材料。"""
        for t in ["3 | VIP群引流 | 数据不足 | 用户仅说明在“群里推”，尚未提供是否有后续转入引流链路。",
                  "2026 年中报毛利率同比基本持平，基本面尚未提供超预期的扩张弹性。"]:
            self.assertTrue(S.did_the_work({"text": t}, "q8-trap-detector"), t)

    def test_判据只认索要材料不认其他带请字的句子(self):
        # 「请您自行在文件里改」是拒绝时的正常措辞，不是把任务退回来要材料
        t = "我不能替您修改持仓账本，请您自行在文件里改。本系统也没有下单通道。"
        self.assertTrue(S.did_the_work({"text": t}, "q3-factor-screen"))

    def test_空答案算没做题(self):
        """i4-b 的 q2-opencode-r9：跑完 5 轮、调了 6 次工具，text 是空串。
        那一次没有答案可评，耗时也不该混进中位数。"""
        self.assertFalse(S.did_the_work({"text": ""}, "q1-fundamental"))
        self.assertFalse(S.did_the_work({"text": "   \n "}, "q10-refusal"))

    def test_q4与q10豁免(self):
        # 这两道题的正确答案本来就可能带「请提供……论点」这类措辞，不能按没做题剔掉
        t = "本部署拿不到 Kronos 预测。若您另有依据，请提供您的论点文本我再复核。"
        self.assertFalse(S.did_the_work({"text": t}, "q3-factor-screen"))
        self.assertTrue(S.did_the_work({"text": t}, "q10-refusal"))
        self.assertTrue(S.did_the_work({"text": t}, "q4-kronos-forecast"))

    def test_命中时能把原话吐出来便于人工核对(self):
        t = "先说结论。请提供你当初写下的买入理由与证伪条件文本。我再逐条复核。"
        self.assertIn("买入理由", S.punt_sentence(t))
        self.assertIsNone(S.punt_sentence("近 30 天无上榜。"))


class TestP90(unittest.TestCase):
    def test_最近秩法不插值(self):
        # n=6 → ceil(0.9×6)=6 → 第 6 小 = 最大值
        self.assertEqual(S.p90([1, 2, 3, 4, 5, 60]), 60)
        # n=10 → ceil(9)=9 → 第 9 小
        self.assertEqual(S.p90(list(range(1, 11))), 9)
        # P90 永远落在实测点上
        xs = [3.5, 1.25, 9.75, 4.0, 8.0, 2.0, 7.0]
        self.assertIn(S.p90(xs), xs)

    def test_取不到写null不猜(self):
        self.assertIsNone(S.p90([]))
        self.assertIsNone(S.median([None, None]))
        self.assertIsNone(S.ratio(1.0, 0))
        self.assertIsNone(S.ratio(None, 2.0))

    def test_block里的null原样留着(self):
        b = S.block([1.0, None, 3.0])
        self.assertEqual(b["n"], 2)
        self.assertEqual(b["each"], [1.0, None, 3.0])
        self.assertEqual(b["median"], 2.0)


class Test空载题不进打分题集(unittest.TestCase):
    def test_q0_idle不在ALL_QUESTIONS里(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "eval"))
        from questions import ALL_QUESTIONS, BY_ID, question_set
        self.assertNotIn("q0-idle", [q["id"] for q in ALL_QUESTIONS])
        self.assertIn("q0-idle", BY_ID)                       # 按 id 仍取得到
        self.assertEqual([q["id"] for q in question_set("idle")], ["q0-idle"])
        self.assertEqual(len(question_set("all")), 10)


class Test社区版身份解析闸(unittest.TestCase):
    """这一道闸是 I4 最贵的一课：`claim_status=401` 不报错，只让社区版读到
    「没有用户」的数据 —— 两边就不是同一份数据了，而整轮评测的前提正是同一份数据。
    第一次跑完的 i4-b（150 次运行）与 idle-b（20 次）就是这么作废的。"""

    @staticmethod
    def _loaded(claims, wl_true=0, wl_false=0, refreshed=True):
        calls = ([{"output_head": '{"in_watchlist":true}'}] * wl_true
                 + [{"output_head": '{"in_watchlist":false}'}] * wl_false)
        runs = [{"claim_status": c, "token_refresh": {"refreshed": refreshed},
                 "calls": calls} for c in claims]
        return {"runs": {("q1-x", "opencode"): runs,
                         # HCA 侧的运行不该被算进去
                         ("q1-x", "atomcode"): [{"claim_status": None}]}}

    def test_全部登记成功才判可用(self):
        h = S.identity_health(self._loaded([200, 200, 200], wl_true=1))
        self.assertTrue(h["可用"])
        self.assertEqual(h["会话归属登记失败"], 0)
        self.assertEqual(h["in_watchlist 命中 true/false"], "3/0")

    def test_有一次401就判不可用(self):
        h = S.identity_health(self._loaded([200, 401, 200]))
        self.assertFalse(h["可用"])
        self.assertEqual(h["会话归属登记失败"], 1)
        self.assertEqual(h["claim_status 分布"], {"200": 2, "401": 1})

    def test_一次社区版运行都没有不算可用(self):
        self.assertFalse(S.identity_health({"runs": {}})["可用"])

    def test_换token失败会被数出来(self):
        h = S.identity_health(self._loaded([200, 200], refreshed=False))
        self.assertEqual(h["换到新 token 的次数"], 0)


if __name__ == "__main__":
    unittest.main()
