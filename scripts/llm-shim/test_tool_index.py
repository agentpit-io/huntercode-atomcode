"""并行工具调用补 index 的回归用例(HCA M0 · 2026-09-22)。

背景:hunter 网关(Gemini 经 OneAPI)流式发并行 tool_calls 时**不带 `index`**,
而 AtomCode 的 openai_compat.rs 是 `tc.index.unwrap_or(0)` —— 三个调用全挤进
槽位 0,name 互相覆盖、arguments 首尾相接,产出
`write_file(file_path=..., command="python3 -c ...", content=...)` 这种脏调用。
shim 按 tool_call `id` 的首次出现顺序把 index 补回去。

跑法:python3 -m unittest discover -p 'test_*.py'
"""
import json
import unittest

from shim import ToolCallIndexer, rewrite_sse_line


def sse(obj):
    return b"data: " + json.dumps(obj).encode()


def tc_line(*calls):
    return sse({"choices": [{"delta": {"tool_calls": list(calls)}}]})


def idx_of(raw, n=0):
    return json.loads(raw[6:])["choices"][0]["delta"]["tool_calls"][n]["index"]


class ToolIndexTests(unittest.TestCase):
    def test_parallel_calls_without_index_get_numbered(self):
        """三个并行调用缺 index → 补成 0/1/2(这正是网关的真实形状)"""
        ix = ToolCallIndexer()
        got = [
            idx_of(rewrite_sse_line(tc_line({"id": cid, "function": {"name": nm, "arguments": a}}), None, ix))
            for cid, nm, a in [
                ("call_1", "read_file", '{"file_path":"a.txt"}'),
                ("call_2", "bash", '{"command":"echo hi"}'),
                ("call_3", "write_file", '{"file_path":"b.txt","content":"x"}'),
            ]
        ]
        self.assertEqual(got, [0, 1, 2])

    def test_continuation_frames_keep_index(self):
        """同一调用分多帧续传 arguments(后续帧没有 id)→ index 必须稳定"""
        ix = ToolCallIndexer()
        first = rewrite_sse_line(tc_line({"id": "c1", "function": {"name": "read", "arguments": '{"pa'}}), None, ix)
        cont = rewrite_sse_line(tc_line({"function": {"arguments": 'th":"a"}'}}), None, ix)
        self.assertEqual([idx_of(first), idx_of(cont)], [0, 0])

    def test_existing_index_is_passthrough(self):
        """网关本来就守规矩时,这层必须是纯透传(一个字节都不改)"""
        ix = ToolCallIndexer()
        src = tc_line({"index": 5, "id": "c9", "function": {"name": "x", "arguments": "{}"}})
        self.assertEqual(rewrite_sse_line(src, None, ix), src)

    def test_new_call_after_explicit_index_continues_numbering(self):
        """先来一个自带 index=0 的,再来个没 index 的新 id → 接着排 1,不撞车"""
        ix = ToolCallIndexer()
        rewrite_sse_line(tc_line({"index": 0, "id": "c1", "function": {"name": "a", "arguments": "{}"}}), None, ix)
        second = rewrite_sse_line(tc_line({"id": "c2", "function": {"name": "b", "arguments": "{}"}}), None, ix)
        self.assertEqual(idx_of(second), 1)

    def test_multiple_calls_in_one_delta(self):
        """一帧里塞了多个调用 → 各自拿到自己的 index"""
        ix = ToolCallIndexer()
        raw = rewrite_sse_line(tc_line(
            {"id": "a", "function": {"name": "f1", "arguments": "{}"}},
            {"id": "b", "function": {"name": "f2", "arguments": "{}"}},
        ), None, ix)
        self.assertEqual([idx_of(raw, 0), idx_of(raw, 1)], [0, 1])

    def test_non_tool_lines_untouched(self):
        """纯文本 / [DONE] / 注释行一律原样"""
        ix = ToolCallIndexer()
        for src in (sse({"choices": [{"delta": {"content": "你好"}}]}),
                    b"data: [DONE]", b": ping", b"data: not-json"):
            self.assertEqual(rewrite_sse_line(src, None, ix), src)


if __name__ == "__main__":
    unittest.main()
