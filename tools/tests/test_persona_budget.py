"""I2 · 人设与项目指令里的「篇幅上限」与「路标收紧」两条。

这两条是**为了墙钟**加的（出字实测 2.3 ms/字 · 一句路标约 0.3 s），
而它们最容易出的事故不是写错，是**被后来的人当套话删掉**，或者
被读成「可以少写内容」。所以钉住三件事：

  1. 两份文件（人设 / `.atomcode.md`）都写了篇幅上限，且数字一致 ——
     只改一处的话，官方二进制那条路（读 `.atomcode.md`）与 fork 那条路
     （整体替换系统提示）会给模型两个不同的上限；
  2. 「不许用来偷工」那句防线还在，且明确把风险提示段排除在「套话」之外；
  3. 路标那一条写明了「只调一次工具就别发」与 12 字上限。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PERSONA = REPO / "distro" / "personas" / "hunter-research.md"
PROJECT = REPO / "distro" / "workspace-template" / ".atomcode.md"


class PersonaBudgetCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.persona = PERSONA.read_text(encoding="utf-8")
        cls.project = PROJECT.read_text(encoding="utf-8")

    def test_两份文件的篇幅上限一致(self):
        for name, text in (("人设", self.persona), (".atomcode.md", self.project)):
            nums = re.findall(r"(\d[\d\s]*)\s*字以内", text)
            got = {int(n.replace(" ", "")) for n in nums}
            self.assertEqual({900, 1300}, got, f"{name} 里的篇幅上限是 {got}")

    def test_出字单价写的是实测值(self):
        # 690 字/秒（= 1.45 ms/字）是 §1.8 那版拟合出来的旧值，实测是 2.3 ms/字。
        # 给模型一个偏快 60% 的价钱，它就不会认真删字。
        self.assertNotIn("690 字/秒", self.persona)
        self.assertIn("2.3 毫秒", self.persona)
        self.assertIn("2.3 毫秒", self.project)

    def test_不许用来偷工那句防线还在(self):
        self.assertIn("这一条不许用来偷工", self.persona)
        for must in ("风险项", "取不到的东西"):
            self.assertIn(must, self.persona)
        # 风险提示段必须被明确排除在「套话」之外，否则模型会把它当套话删掉，
        # 而它是合规要求、必须原样附上的。
        self.assertRegex(self.persona, r"那段风险提示不是套话")

    def test_路标收紧那一条(self):
        seg = self.persona.split("## PROGRESS SIGNPOSTS:")[1].split("\n## ")[0]
        self.assertIn("12 个字", seg)
        self.assertIn("只调一次工具就别发", seg)

    def test_按结构卡的改对版在_且三条护栏一条不少(self):
        """I3：「按结构卡」加回来了，但**只能是改对的那一版**。

        I2 §2.9.4 那一版测过、按护栏回退了，回退理由是实测出来的：`opt4-fork-b` 里
          · q2 把「我的持仓 2000 股 / 38.50 元」整段删了 —— 被读错的是
            「数据表只列题面点名要的指标」这句 → A2 5 个要点只中 4 个（8.0 → 6.4）；
          · q4 把**必须原样附上的 AI 生成标识与风险提示**当成「额外段落」删了。
        A 掉 1.6 分 > 护栏 1 分，所以整项回退。

        所以这条用例钉的不是「别加回来」，是**加回来必须带着三条护栏**
        （用户 2026-09-23 07:45 的任务书原话）：
          1. 题面明确要求的内容不在可砍范围；
          2. 风险提示段不算多余段落；
          3. 篇幅控制只作用于铺陈与重复解释，不作用于数据与依据。
        另外钉住 I2 那一版里**害人的两句**不许原样回来：
        「只列题面点名要的指标」（白名单口径）与「最多 3 条」（卡内容条数）。
        """
        for name, text in (("人设", self.persona), (".atomcode.md", self.project)):
            self.assertIn("按结构卡", text, f"{name} 里没有按结构卡")

            # 护栏 1：题面点名要的不在可砍范围 —— 必须是**排除式**表述。
            self.assertIn("题面点名要的指标一项都不能少", text,
                          f"{name} 里「题面点名要的」写成了白名单口径")
            self.assertNotIn("只列题面点名要的指标", text,
                             f"{name} 里 I2 那句害人的白名单表述又回来了")

            # 护栏 2：风险提示段不算额外段。
            self.assertIn("不算额外段", text,
                          f"{name} 里没写明风险提示段不算额外段")

            # 护栏 3：卡的是铺陈与重复解释，不是数据与依据；条数不限。
            self.assertIn("卡的是铺陈与重复解释", text.replace("**", ""),
                          f"{name} 里没写明这张卡卡的是什么")
            self.assertIn("条数都不限" if name == ".atomcode.md" else "条数不限", text,
                          f"{name} 里还在卡依据/风险项的条数")
            self.assertNotIn("最多 3 条", text, f"{name} 里又出现了「风险项最多 3 条」")
            self.assertNotIn("最多 4 条", text, f"{name} 里又出现了「判断依据最多 4 条」")

    def test_篇幅上限不含风险提示段(self):
        # 上限若被读成「含那段风险提示」，模型为了压字数就会去删它。
        self.assertIn("不含末尾那段风险提示", self.persona)
        self.assertIn("不含末尾风险提示那一段", self.project)


if __name__ == "__main__":
    unittest.main()
