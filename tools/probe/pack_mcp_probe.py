#!/usr/bin/env python3
"""hcapack 组合工具的真实探针 —— 直连 stdio、真调三个工具、把返回原样打出来。

不经 daemon、不烧模型 token：只验「这三个包能不能真取到数、多大、多久」。

    docker run --rm --network hca-eval-net \\
      -e HERMES_API_URL=http://hunter-api:8000 -e HUNTER_INTERNAL_KEY=... \\
      -v <repo>/tools/opencode-mcp:/opt/hca/mcp-src:ro \\
      --entrypoint python3 hca-daemon:i2 /opt/hca/mcp-src/../probe/pack_mcp_probe.py

红线 1 的自检也在这里：**每个块要么有数据要么有 error，不许两头都空**。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time


def rpc(proc, obj):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


def recv(proc, timeout=180.0):
    deadline = time.time() + timeout
    while True:
        if time.time() > deadline:
            raise TimeoutError(f"{timeout:.0f} 秒内没等到响应")
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError(f"server 提前退出 exit={proc.poll()}："
                               f"{(proc.stderr.read() or '')[:1000]}")
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" in msg or "error" in msg:
            return msg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="/opt/hca/mcp/hca_pack_mcp.py")
    ap.add_argument("--python", default="/opt/hca/venv/bin/python")
    ap.add_argument("--code", default="601088")
    ap.add_argument("--codes", default="600519,601088,300750")
    ap.add_argument("--head", type=int, default=900)
    args = ap.parse_args(argv)

    proc = subprocess.Popen([args.python, args.server], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        t0 = time.time()
        rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "hca-pack-probe", "version": "1"}}})
        init = recv(proc)
        if "error" in init:
            print(f"✗ initialize 失败：{init['error']}")
            return 1
        print(f"initialize {(time.time() - t0) * 1000:.0f} ms")
        rpc(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = recv(proc)["result"]["tools"]
        schema_bytes = len(json.dumps(tools, ensure_ascii=False).encode())
        print(f"tools/list: {[t['name'] for t in tools]}  schema 合计 {schema_bytes} 字节")

        cases = [("stock_snapshot", {"code": args.code}),
                 ("stocks_intel", {"codes": args.codes, "limit": 5}),
                 ("thesis_evidence", {"code": args.code})]
        rc = 0
        for i, (name, a) in enumerate(cases, start=3):
            t = time.time()
            rpc(proc, {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                       "params": {"name": name, "arguments": a}})
            res = recv(proc)
            ms = (time.time() - t) * 1000
            if "error" in res:
                print(f"\n✗ {name} {ms:.0f} ms 失败：{res['error']}")
                rc = 1
                continue
            text = res["result"]["content"][0]["text"]
            print(f"\n=== {name}({a}) {ms:.0f} ms，{len(text.encode())} 字节 ===")
            try:
                d = json.loads(text)
            except json.JSONDecodeError:
                print("  ⚠ 返回不是合法 JSON（大小闸应当保证是）")
                print("  " + text[:args.head])
                rc = 1
                continue
            for k, v in d.items():
                if isinstance(v, dict) and "error" in v:
                    print(f"  · {k}: ⚠ {v['error']}")
                elif isinstance(v, dict):
                    print(f"  · {k}: {json.dumps(v, ensure_ascii=False)[:args.head]}")
                else:
                    print(f"  · {k}: {json.dumps(v, ensure_ascii=False)[:200]}")
        return rc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
