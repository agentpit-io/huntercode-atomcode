"""fill_manual.py 的单测 —— 关键是「有问题就一条都不写」，不能写一半。"""
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "eval"))
import fill_manual  # noqa: E402


def scores_doc():
    return {"runs": {
        "r1": {"manual": {"A1": {"score": None, "why": "", "max": 10},
                          "A2": {"score": None, "why": "", "max": 8},
                          "A3": {"score": None, "why": "", "max": 7},
                          "B1": {"score": None, "why": "", "max": 10},
                          "C2": {"score": None, "why": "", "max": 5},
                          "C3": {"score": None, "why": "", "max": 5},
                          "C5": {"score": None, "why": "", "max": 5},
                          "B3": {"score": None, "why": "", "max": 7},
                          "C4": {"score": None, "why": "", "max": 5}}}}}


class TestFill(unittest.TestCase):
    def run_fill(self, manual):
        with TemporaryDirectory() as td:
            m = Path(td) / "manual.json"
            s = Path(td) / "scores.json"
            m.write_text(json.dumps(manual, ensure_ascii=False), encoding="utf-8")
            s.write_text(json.dumps(scores_doc(), ensure_ascii=False), encoding="utf-8")
            rc = fill_manual.main(["--manual", str(m), "--scores", str(s)])
            return rc, json.loads(s.read_text(encoding="utf-8"))

    def test_正常写入分数与理由(self):
        rc, d = self.run_fill({"r1": {"A1": [10, "全部能对上"]}})
        self.assertEqual(rc, 0)
        self.assertEqual(d["runs"]["r1"]["manual"]["A1"]["score"], 10)
        self.assertEqual(d["runs"]["r1"]["manual"]["A1"]["why"], "全部能对上")

    def test_注释键被跳过(self):
        rc, _ = self.run_fill({"_说明": "随便写", "r1": {"A1": [10, "理由"]}})
        self.assertEqual(rc, 0)

    def test_超过满分整份拒绝(self):
        rc, d = self.run_fill({"r1": {"A2": [10, "A2 满分只有 8"]}})
        self.assertEqual(rc, 2)
        self.assertIsNone(d["runs"]["r1"]["manual"]["A2"]["score"])

    def test_负分整份拒绝(self):
        rc, _ = self.run_fill({"r1": {"A1": [-1, "理由"]}})
        self.assertEqual(rc, 2)

    def test_理由为空整份拒绝(self):
        rc, _ = self.run_fill({"r1": {"A1": [10, "   "]}})
        self.assertEqual(rc, 2)

    def test_不认识的运行整份拒绝(self):
        rc, _ = self.run_fill({"不存在": {"A1": [10, "理由"]}})
        self.assertEqual(rc, 2)

    def test_自动项不让人工写(self):
        # B2 / C1 / D1 / D2 是纯自动项，人工不该覆盖
        rc, _ = self.run_fill({"r1": {"B2": [8, "理由"]}})
        self.assertEqual(rc, 2)

    def test_B3_C4_允许人工覆盖(self):
        rc, d = self.run_fill({"r1": {"B3": [7, "自动判的重复是误判"],
                                      "C4": [5, "「本报告不设目标价」是否定句，不算指令"]}})
        self.assertEqual(rc, 0)
        self.assertEqual(d["runs"]["r1"]["manual"]["B3"]["score"], 7)

    def test_一条出错时其余也不写(self):
        rc, d = self.run_fill({"r1": {"A1": [10, "好的那条"], "A2": [99, "坏的那条"]}})
        self.assertEqual(rc, 2)
        self.assertIsNone(d["runs"]["r1"]["manual"]["A1"]["score"])

    def test_格式不是二元组则拒绝(self):
        rc, _ = self.run_fill({"r1": {"A1": 10}})
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
