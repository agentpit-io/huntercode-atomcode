"""check_parallel_calls.py 的单测 —— 关键是别把顺序调用误判成并行。"""
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
import check_parallel_calls as cpc  # noqa: E402


def tool(name, inp, status="completed"):
    return {"type": "tool", "tool": name, "state": {"input": inp, "status": status}}


STEP = {"type": "step-start"}


class TestAnalyse(unittest.TestCase):
    def _run(self, messages):
        with TemporaryDirectory() as td:
            p = Path(td) / "x.raw.json"
            p.write_text(json.dumps({"messages": messages}, ensure_ascii=False),
                         encoding="utf-8")
            return cpc.analyse(p)

    def test_顺序调用不算并行(self):
        # 一个 step 一个工具 —— 这是顺序，不是并行
        steps, worst, groups = self._run([
            {"parts": [STEP, tool("a", {"x": 1}), STEP, tool("b", {"x": 2})]}])
        self.assertEqual(worst, 1)
        self.assertEqual(groups, [])

    def test_同一个step里两个工具算并行(self):
        steps, worst, groups = self._run([
            {"parts": [STEP, tool("a", {"x": 1}), tool("b", {"x": 2})]}])
        self.assertEqual(worst, 2)
        self.assertEqual(len(groups), 1)

    def test_最后一个step的并行组不会漏掉(self):
        # 消息末尾没有再来一个 step-start，收尾时必须把 cur 结算掉
        _, worst, groups = self._run([
            {"parts": [STEP, tool("a", {}), STEP, tool("b", {}), tool("c", {})]}])
        self.assertEqual(worst, 2)
        self.assertEqual(len(groups), 1)

    def test_并行组里参数各不相同(self):
        _, _, groups = self._run([
            {"parts": [STEP, tool("news", {"code": "600519"}),
                       tool("news", {"code": "601088"})]}])
        keys = {(t, a) for t, a, _ in groups[0]}
        self.assertEqual(len(keys), 2)

    def test_同名同参重复能被看出来(self):
        # 被合并的表现之一：同一个调用出现两次、参数一模一样
        _, _, groups = self._run([
            {"parts": [STEP, tool("news", {"code": "600519"}),
                       tool("news", {"code": "600519"})]}])
        keys = {(t, a) for t, a, _ in groups[0]}
        self.assertEqual(len(keys), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_参数键序不同不算不同(self):
        # json.dumps(sort_keys=True)：{"a":1,"b":2} 与 {"b":2,"a":1} 必须算同一个
        _, _, groups = self._run([
            {"parts": [STEP, tool("t", {"a": 1, "b": 2}), tool("t", {"b": 2, "a": 1})]}])
        keys = {(t, a) for t, a, _ in groups[0]}
        self.assertEqual(len(keys), 1)

    def test_没有工具调用时不报并行(self):
        _, worst, groups = self._run([{"parts": [STEP, {"type": "text", "text": "x"}]}])
        self.assertEqual((worst, groups), (0, []))


if __name__ == "__main__":
    unittest.main()
