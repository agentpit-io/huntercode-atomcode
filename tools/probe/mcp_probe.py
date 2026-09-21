#!/usr/bin/env python3
"""直连 akshare-mcp 的 stdio JSON-RPC 探针 · 不经过 LLM,验证 MCP 数据面真实可用。"""
import json, subprocess, sys, time

CMD = ["/home/support/hca/probe/venv-akshare/bin/akshare-mcp"]
p = subprocess.Popen(CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True, bufsize=1,
                     env={"PATH": "/usr/bin:/bin", "AKSHARE_MAX_ROWS": "50", "HOME": "/home/support"})

def send(obj):
    p.stdin.write(json.dumps(obj) + "\n"); p.stdin.flush()

def recv():
    while True:
        line = p.stdout.readline()
        if not line: raise SystemExit("MCP server 提前退出: " + p.stderr.read()[:2000])
        line = line.strip()
        if not line: continue
        return json.loads(line)

t0 = time.time()
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{
    "protocolVersion":"2025-06-18","capabilities":{},
    "clientInfo":{"name":"hca-probe","version":"0"}}})
init = recv()
send({"jsonrpc":"2.0","method":"notifications/initialized"})
print("=== initialize 耗时 %.3fs ===" % (time.time()-t0))
print(json.dumps(init, ensure_ascii=False)[:600])

send({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}})
tools = recv()
print("=== tools/list ===")
for t in tools["result"]["tools"]:
    print(" -", t["name"], "|", t["description"][:80].replace("\n"," "))

def call(name, args, tag):
    t = time.time()
    send({"jsonrpc":"2.0","id":99,"method":"tools/call","params":{"name":name,"arguments":args}})
    r = recv()
    print("=== %s (%.2fs) ===" % (tag, time.time()-t))
    c = r.get("result",{}).get("content",[])
    txt = c[0].get("text","") if c else json.dumps(r, ensure_ascii=False)
    print(txt[:2500])
    return txt

call("akshare_search", {"keyword":"历史行情"}, "akshare_search 历史行情")
call("akshare_signature", {"func":"stock_zh_a_hist"}, "akshare_signature stock_zh_a_hist")
call("akshare_call", {"func":"stock_zh_a_hist","kwargs":{"symbol":"600519","period":"daily","start_date":"20260910","end_date":"20260922","adjust":"qfq"}}, "akshare_call 600519 日线")
p.terminate()
