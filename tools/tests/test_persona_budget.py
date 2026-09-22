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

    def test_篇幅上限不含风险提示段(self):
        # 上限若被读成「含那段风险提示」，模型为了压字数就会去删它。
        self.assertIn("不含末尾那段风险提示", self.persona)
        self.assertIn("不含末尾风险提示那一段", self.project)


if __name__ == "__main__":
    unittest.main()
