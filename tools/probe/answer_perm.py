#!/usr/bin/env python3
"""读 SSE 文件里最后一条 permission_request,按参数给出 allow/deny 决策。"""
import json, os, sys, time, urllib.request
sse, decision = sys.argv[1], sys.argv[2]
tok = open(os.path.expanduser("~/hca/probe/tok.env")).read().split("=", 1)[1].strip()
seen = set()
deadline = time.time() + int(sys.argv[3])
while time.time() < deadline:
    try: lines = open(sse).read().splitlines()
    except FileNotFoundError: lines = []
    for ln in lines:
        if not ln.startswith("data: "): continue
        try: e = json.loads(ln[6:])
        except Exception: continue
        if e.get("type") != "permission_request": continue
        if e["call_id"] in seen: continue
        seen.add(e["call_id"])
        body = json.dumps({"session_id": e["session_id"], "call_id": e["call_id"],
                           "decision": decision}).encode()
        r = urllib.request.Request("http://127.0.0.1:13456/chat/permission", data=body,
                                   headers={"Content-Type": "application/json",
                                            "Authorization": "Bearer " + tok})
        try:
            print("  → 对 %s 回 %s: %s" % (e["tool_name"], decision,
                                           urllib.request.urlopen(r, timeout=10).read().decode()[:120]))
        except Exception as ex:
            print("  → 回复失败:", ex)
    time.sleep(0.5)
