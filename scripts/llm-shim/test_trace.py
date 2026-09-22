"""llm-shim 请求瀑布追踪（I2）的单测 —— 只测拆解与落盘，不起服务。

这层是**只读观测**：解析不了的请求返回 None、写不进去也不报错，
任何情况下都不能影响转发本身。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shim  # noqa: E402


class TestTraceRequest(unittest.TestCase):
    def test_拆出消息与工具的尺寸构成(self):
        body = json.dumps({
            "model": "hunter-chat", "stream": True,
            "messages": [
                {"role": "system", "content": "abcde"},
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "",
                 "tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": "{}"}}]},
            ],
            "tools": [
                {"type": "function", "function": {
                    "name": "mcp__akshare__akshare_search",
                    "description": "找函数",
                    "parameters": {"type": "object", "properties": {"keyword": {"type": "string"}}}}},
            ],
        }, ensure_ascii=False).encode()
        rec = shim._trace_request(body)
        self.assertEqual(rec["model"], "hunter-chat")
        self.assertTrue(rec["stream"])
        self.assertEqual(rec["n_messages"], 3)
        self.assertEqual(rec["system_chars"], 5)
        self.assertEqual(rec["messages"][1]["chars"], 2)
        self.assertEqual(rec["messages"][2]["tool_calls"], 1)
        self.assertEqual(rec["n_tools"], 1)
        self.assertEqual(rec["tools"][0]["name"], "mcp__akshare__akshare_search")
        self.assertEqual(rec["tools"][0]["desc_chars"], 3)
        self.assertGreater(rec["tools"][0]["params_bytes"], 0)
        self.assertEqual(rec["tools_bytes"], rec["tools"][0]["bytes"])

    def test_多模态消息只数文本片段(self):
        body = json.dumps({"messages": [
            {"role": "user", "content": [{"type": "text", "text": "1234"},
                                         {"type": "image_url", "image_url": {"url": "x"}}]}]}).encode()
        rec = shim._trace_request(body)
        self.assertEqual(rec["messages"][0]["chars"], 4)

    def test_不是JSON就返回None(self):
        self.assertIsNone(shim._trace_request(b"not json"))

    def test_不记录正文(self):
        body = json.dumps({"messages": [{"role": "user", "content": "持仓 601088 2000 股"}]},
                          ensure_ascii=False).encode()
        rec = shim._trace_request(body)
        self.assertNotIn("601088", json.dumps(rec, ensure_ascii=False))


class TestTraceUsage(unittest.TestCase):
    def test_从SSE行里捞usage(self):
        sink = {}
        shim._trace_usage(b'data: {"usage":{"prompt_tokens":100,"completion_tokens":7}}', sink)
        self.assertEqual(sink["prompt_tokens"], 100)

    def test_DONE与无usage的行都不炸(self):
        sink = {}
        shim._trace_usage(b"data: [DONE]", sink)
        shim._trace_usage(b'data: {"choices":[]}', sink)
        shim._trace_usage(b"garbage", sink)
        self.assertEqual(sink, {})


class TestTraceWrite(unittest.TestCase):
    def test_默认关时不写盘(self):
        old = shim.TRACE_DIR
        shim.TRACE_DIR = ""
        try:
            shim._trace_write({"a": 1})
        finally:
            shim.TRACE_DIR = old

    def test_开了就追加一行jsonl(self):
        old = shim.TRACE_DIR
        with tempfile.TemporaryDirectory() as d:
            shim.TRACE_DIR = os.path.join(d, "trace")
            try:
                shim._trace_write({"a": 1})
                shim._trace_write({"a": 2})
                lines = open(os.path.join(shim.TRACE_DIR, "requests.jsonl"),
                             encoding="utf-8").read().strip().split("\n")
            finally:
                shim.TRACE_DIR = old
        self.assertEqual([json.loads(x)["a"] for x in lines], [1, 2])

    def test_目录不可写也不抛(self):
        old = shim.TRACE_DIR
        shim.TRACE_DIR = "/proc/不可能建的目录/x"
        try:
            shim._trace_write({"a": 1})
        finally:
            shim.TRACE_DIR = old


if __name__ == "__main__":
    unittest.main()


class TestToolDeny(unittest.TestCase):
    """I2 工具白名单 —— 只做减法，其余字段一个不碰。"""

    def setUp(self):
        self._old = shim.TOOL_DENY
        shim._TOOL_DENY_LOGGED.clear()

    def tearDown(self):
        shim.TOOL_DENY = self._old

    @staticmethod
    def body(*names):
        return {"model": "m", "messages": [{"role": "user", "content": "x"}],
                "tools": [{"type": "function", "function": {"name": n, "parameters": {}}}
                          for n in names]}

    def test_不配置时一个都不摘(self):
        shim.TOOL_DENY = []
        o = self.body("atomgit_pr", "bash")
        self.assertEqual(shim.drop_tools(o), 0)
        self.assertEqual(len(o["tools"]), 2)

    def test_按名字摘(self):
        shim.TOOL_DENY = ["code_review"]
        o = self.body("code_review", "bash")
        self.assertEqual(shim.drop_tools(o), 1)
        self.assertEqual([t["function"]["name"] for t in o["tools"]], ["bash"])

    def test_按前缀通配摘(self):
        shim.TOOL_DENY = ["atomgit_*", "trace_*"]
        o = self.body("atomgit_pr", "atomgit_api", "trace_callers", "bash",
                      "mcp__watchlist__stock_quickview")
        self.assertEqual(shim.drop_tools(o), 3)
        self.assertEqual([t["function"]["name"] for t in o["tools"]],
                         ["bash", "mcp__watchlist__stock_quickview"])

    def test_通配不会误伤同前缀的别的工具(self):
        # `trace_*` 不该把 `mcp__x__trace_something` 摘掉 —— 匹配的是整名开头
        shim.TOOL_DENY = ["trace_*"]
        o = self.body("mcp__x__trace_it", "trace_chain")
        self.assertEqual(shim.drop_tools(o), 1)
        self.assertEqual([t["function"]["name"] for t in o["tools"]], ["mcp__x__trace_it"])

    def test_没有tools字段不炸(self):
        shim.TOOL_DENY = ["x"]
        o = {"messages": []}
        self.assertEqual(shim.drop_tools(o), 0)

    def test_sanitize_body里串起来生效(self):
        shim.TOOL_DENY = ["atomgit_*"]
        out = json.loads(shim.sanitize_body(json.dumps(self.body("atomgit_pr", "bash")).encode()))
        self.assertEqual([t["function"]["name"] for t in out["tools"]], ["bash"])

    def test_摘不掉的工具其schema原样保留(self):
        shim.TOOL_DENY = ["atomgit_*"]
        o = {"tools": [{"type": "function",
                        "function": {"name": "bash", "description": "跑命令",
                                     "parameters": {"type": "object",
                                                    "properties": {"command": {"type": "string"}}}}}]}
        shim.drop_tools(o)
        self.assertEqual(o["tools"][0]["function"]["description"], "跑命令")
        self.assertEqual(o["tools"][0]["function"]["parameters"]["properties"]["command"],
                         {"type": "string"})
