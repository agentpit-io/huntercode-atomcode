"""check_truncation.py 的单测 —— 截断标记要认得出，没截断的不能误报。"""
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
import check_truncation  # noqa: E402


def sse(events) -> str:
    return "\n".join("data: " + json.dumps(e, ensure_ascii=False) for e in events)


def result(name, output):
    return {"type": "tool_result", "name": name, "output": output}


MARK = ("\n\n[atomcode: output truncated — 40030 bytes total, showing first 4096 "
        "+ last 4094 bytes. Full output saved as artifact 0ec31a7bd97218d4. "
        'To read more: fetch_output(artifact_id="0ec31a7bd97218d4", offset, limit).]')


class TestScan(unittest.TestCase):
    def _scan(self, text):
        with TemporaryDirectory() as td:
            p = Path(td) / "x.sse"
            p.write_text(text, encoding="utf-8")
            return check_truncation.scan(p)

    def test_没有截断时不误报(self):
        n, total, cut = self._scan(sse([result("a", "短返回"), result("b", "也很短")]))
        self.assertEqual((n, total, cut), (0, 2, []))

    def test_认出截断标记并取到全长字节数(self):
        n, total, cut = self._scan(sse([result("a", "头" + MARK)]))
        self.assertEqual(n, 1)
        self.assertEqual(total, 1)
        self.assertEqual(cut, [("a", 40030)])

    def test_只数_tool_result_不数别的事件(self):
        n, total, _ = self._scan(sse([
            {"type": "text", "text": "output truncated — 999 bytes total"},
            result("a", "短"),
        ]))
        self.assertEqual((n, total), (0, 1))

    def test_坏行跳过不抛异常(self):
        n, total, _ = self._scan("data: {坏 json\n" + sse([result("a", "短")]))
        self.assertEqual((n, total), (0, 1))

    def test_混合多次调用(self):
        n, total, cut = self._scan(sse([
            result("a", "短"), result("b", "头" + MARK), result("c", "短"),
        ]))
        self.assertEqual((n, total), (1, 3))
        self.assertEqual(cut[0][0], "b")


if __name__ == "__main__":
    unittest.main()
