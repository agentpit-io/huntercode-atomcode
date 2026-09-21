#!/usr/bin/env python3
"""HCA M0 探针 · 本地 stub provider(OpenAI 兼容)。

**它不是业务 mock**:不伪造任何行情/额度/评分数字,只把"模型该发什么工具调用"
写死,用来在**内置额度耗尽**时仍能确定性地压测 AtomCode 运行时本身
(四档权限门、hook 拦截、MCP 工具是否挂载、多会话隔离)。
真实模型(hunter-chat)的复测在额度重置后另做,两份结果都进报告。

脚本序列由请求里已出现的 tool 消息条数决定:第 N 轮发第 N 个工具调用,
走完后回一段纯文本。序列从 HCA_STUB_SCRIPT 指定的 JSON 文件读。
"""
import json, os, sys, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("HCA_STUB_PORT", "18080"))
SCRIPT_PATH = os.environ.get("HCA_STUB_SCRIPT", "/home/support/hca/probe/stub_script.json")
LOG = os.environ.get("HCA_STUB_LOG", "/home/support/hca/probe/logs/stub.jsonl")


def load_script():
    with open(SCRIPT_PATH) as f:
        return json.load(f)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._json(200, {"object": "list", "data": [
                {"id": "stub-model", "object": "model", "created": 0, "owned_by": "hca-probe"}]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            req = json.loads(raw)
        except Exception:
            return self._json(400, {"error": "bad json"})

        msgs = req.get("messages", [])
        # 只数"最后一条 user 消息之后"的 tool 消息 —— 这样同一会话里连续多轮
        # 都从脚本第 0 步重新开始,便于重复压测。
        last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
        turn = sum(1 for m in msgs[last_user + 1:] if m.get("role") == "tool")
        tool_names = sorted({t.get("function", {}).get("name", "")
                             for t in (req.get("tools") or [])})
        with open(LOG, "a") as f:
            f.write(json.dumps({
                "ts": time.time(), "turn": turn, "n_messages": len(msgs),
                "n_tools": len(tool_names), "tools": tool_names,
                "last_tool_msg": next((m.get("content") for m in reversed(msgs)
                                       if m.get("role") == "tool"), None),
            }, ensure_ascii=False) + "\n")

        script = load_script()
        step = script[turn] if turn < len(script) else {"type": "text", "text": "脚本结束。"}
        if req.get("stream"):
            self._stream(step)
        else:
            self._json(200, self._blocking(step))

    def _msg(self, step):
        if step["type"] == "tool":
            return {"role": "assistant", "content": None, "tool_calls": [{
                "index": 0, "id": "call_stub_%s" % uuid.uuid4().hex[:12], "type": "function",
                "function": {"name": step["name"],
                             "arguments": json.dumps(step["arguments"], ensure_ascii=False)}}]}
        return {"role": "assistant", "content": step.get("text", "")}

    def _blocking(self, step):
        m = self._msg(step)
        return {"id": "chatcmpl-stub", "object": "chat.completion", "created": int(time.time()),
                "model": "stub-model",
                "choices": [{"index": 0, "message": m,
                             "finish_reason": "tool_calls" if step["type"] == "tool" else "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    def _stream(self, step):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        def chunk(delta, finish=None):
            o = {"id": "chatcmpl-stub", "object": "chat.completion.chunk",
                 "created": int(time.time()), "model": "stub-model",
                 "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(("data: " + json.dumps(o, ensure_ascii=False) + "\n\n").encode())
            self.wfile.flush()

        if step["type"] == "tool":
            cid = "call_stub_%s" % uuid.uuid4().hex[:12]
            chunk({"role": "assistant", "content": None, "tool_calls": [{
                "index": 0, "id": cid, "type": "function",
                "function": {"name": step["name"], "arguments": ""}}]})
            args = json.dumps(step["arguments"], ensure_ascii=False)
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": args}}]})
            chunk({}, "tool_calls")
        else:
            chunk({"role": "assistant", "content": ""})
            for piece in [step.get("text", "")[i:i + 20] for i in range(0, len(step.get("text", "")), 20)] or [""]:
                chunk({"content": piece})
            chunk({}, "stop")
        usage = {"id": "chatcmpl-stub", "object": "chat.completion.chunk",
                 "created": int(time.time()), "model": "stub-model", "choices": [],
                 "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
        self.wfile.write(("data: " + json.dumps(usage) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
