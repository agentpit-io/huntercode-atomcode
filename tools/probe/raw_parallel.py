#!/usr/bin/env python3
"""直连 hunter 网关，诱发并行工具调用，看原始 SSE 里 tool_calls 的 index 是否正确分片。
用于判定「并行工具调用参数被合并」发生在网关侧还是 AtomCode 侧。"""
import json, os, sys, urllib.request

KEY = open(os.path.expanduser("~/hca/secrets/llm-test-key")).read().strip()
MODEL = sys.argv[1] if len(sys.argv) > 1 else "hunter-chat"
TOOLS = [
 {"type":"function","function":{"name":"read_file","description":"读文件",
  "parameters":{"type":"object","properties":{"file_path":{"type":"string"}},"required":["file_path"]}}},
 {"type":"function","function":{"name":"bash","description":"跑 shell 命令",
  "parameters":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}}},
 {"type":"function","function":{"name":"write_file","description":"写文件",
  "parameters":{"type":"object","properties":{"file_path":{"type":"string"},"content":{"type":"string"}},
  "required":["file_path","content"]}}},
]
body = {"model":MODEL,"stream":True,"tools":TOOLS,"messages":[
  {"role":"user","content":'同时并行发起这三个工具调用，一次全发出来，不要分步：'
   'read_file(file_path="a.txt")、bash(command="echo hi")、write_file(file_path="b.txt", content="x")'}]}
req = urllib.request.Request("https://hunter.agentpit.io/api/saas/llm/v1/chat/completions",
    data=json.dumps(body).encode(), headers={"Authorization":"Bearer "+KEY,"Content-Type":"application/json"})
frag = {}
print("### 模型 %s · 原始 SSE 里的 tool_calls 分片" % MODEL)
for raw in urllib.request.urlopen(req, timeout=120):
    line = raw.decode("utf-8","replace").strip()
    if not line.startswith("data: "): continue
    if line[6:] == "[DONE]": break
    try: d = json.loads(line[6:])
    except Exception: continue
    for ch in d.get("choices", []):
        tc = ch.get("delta", {}).get("tool_calls")
        if not tc: continue
        for c in tc:
            idx = c.get("index")
            fn = c.get("function", {})
            print("  chunk: index=%r id=%r name=%r args=%r" % (idx, c.get("id"), fn.get("name"), fn.get("arguments")))
            s = frag.setdefault(idx, {"name": None, "args": ""})
            if fn.get("name"): s["name"] = fn["name"]
            s["args"] += fn.get("arguments") or ""
print("--- 按 index 重组后 ---")
for k in sorted(frag, key=lambda x: (x is None, x)):
    print("  index=%r  %s(%s)" % (k, frag[k]["name"], frag[k]["args"]))
print("→ 网关%s按 index 正确分片" % ("已" if len([k for k in frag if k is not None]) > 1 else "未"))
