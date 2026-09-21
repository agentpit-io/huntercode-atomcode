#!/usr/bin/env python3
"""测 parallel_tool_calls:false 能否让网关一次只发一个工具调用（零改码缓解方案）。"""
import json, os, sys, urllib.request
KEY = open(os.path.expanduser("~/hca/secrets/llm-test-key")).read().strip()
TOOLS = [
 {"type":"function","function":{"name":"read_file","description":"读文件",
  "parameters":{"type":"object","properties":{"file_path":{"type":"string"}},"required":["file_path"]}}},
 {"type":"function","function":{"name":"bash","description":"跑 shell 命令",
  "parameters":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}}},
 {"type":"function","function":{"name":"write_file","description":"写文件",
  "parameters":{"type":"object","properties":{"file_path":{"type":"string"},"content":{"type":"string"}},
  "required":["file_path","content"]}}},
]
MSG = ('同时并行发起这三个工具调用，一次全发出来，不要分步：'
       'read_file(file_path="a.txt")、bash(command="echo hi")、write_file(file_path="b.txt", content="x")')

def run(label, extra):
    body = {"model":"hunter-chat","stream":True,"tools":TOOLS,
            "messages":[{"role":"user","content":MSG}]}
    body.update(extra)
    req = urllib.request.Request("https://hunter.agentpit.io/api/saas/llm/v1/chat/completions",
        data=json.dumps(body).encode(), headers={"Authorization":"Bearer "+KEY,"Content-Type":"application/json"})
    calls=[]
    try:
        for raw in urllib.request.urlopen(req, timeout=120):
            line = raw.decode("utf-8","replace").strip()
            if not line.startswith("data: ") or line[6:]=="[DONE]": continue
            try: d=json.loads(line[6:])
            except: continue
            for ch in d.get("choices",[]):
                for c in (ch.get("delta",{}).get("tool_calls") or []):
                    calls.append((c.get("index"), (c.get("function") or {}).get("name")))
    except Exception as e:
        print("  请求失败:", e); return
    print("  %s → 本轮 %d 个工具调用分片: %s" % (label, len(calls), calls))

print("### 对照：默认（不带 parallel_tool_calls）")
run("默认", {})
print("### 试验：parallel_tool_calls=false")
run("ptc=false", {"parallel_tool_calls": False})
