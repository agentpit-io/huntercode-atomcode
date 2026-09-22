#!/usr/bin/env python3
"""fork 三个开关的端到端验证 —— 不经网关、不烧 token、不占评测机。

    python3 tools/probe/fork_verify.py --binary <atomcode 二进制> [--persona <文件>]

做的事：起一个只记录请求的 stub provider，用同一个二进制跑三遍 headless：

  A. 不配任何 fork 参数        → 期望：与上游一致（内置编码人设、工具一个不少）
  B. 只配 system_prompt_file   → 期望：系统提示换成人设文件，工具数不变
  C. 再配 [tools] deny         → 期望：系统提示同 B，工具按族少掉

**「与上游一致」这件事只能同版本官方二进制自己比**，所以 `--upstream <二进制>`
给了的话会拿它跑一遍 A，两边的系统提示与工具清单要逐字节相同。

退出码：0 = 三项都如期；1 = 有一项不如期。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RECORD: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            req = json.loads(raw)
        except Exception:
            self.send_response(400); self.end_headers(); return
        systems = [m.get("content") or "" for m in req.get("messages", [])
                   if m.get("role") == "system"]
        RECORD.append({
            "system_parts": [len(x) for x in systems],
            "system_head": (systems[0][:160] if systems else ""),
            "system_full": "\n".join(systems),
            "tools": sorted(t.get("function", {}).get("name", "")
                            for t in (req.get("tools") or [])),
        })
        body = json.dumps({
            "id": "stub", "object": "chat.completion", "created": 0,
            "model": req.get("model") or "stub",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        body = b'{"data":[]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_once(binary: str, port: int, home: Path, work: Path,
             persona: str | None, deny: list[str]) -> dict:
    RECORD.clear()
    cfg = home / "config.toml"
    lines = [
        'default_provider = "stub"',
        "auto_update = false",
        "",
        "[providers.stub]",
        'type = "openai"',
        'api_key = "sk-stub"',
        'model = "stub-model"',
        f'base_url = "http://127.0.0.1:{port}/v1"',
        "context_window = 128000",
    ]
    if persona:
        lines.append(f"system_prompt_file = {json.dumps(persona)}")
    if deny:
        lines += ["", "[tools]", "deny = [" + ", ".join(json.dumps(x) for x in deny) + "]"]
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")

    env = dict(os.environ, ATOMCODE_HOME=str(home), HOME=str(home),
               ATOMCODE_NO_UPDATE="1", NO_COLOR="1")
    p = subprocess.run([binary, "-C", str(work), "-p", "说一句 ok", "--ephemeral",
                        "--dev", "--no-telemetry"],
                       capture_output=True, text=True, timeout=180, env=env)
    if not RECORD:
        return {"error": f"stub 没收到请求；rc={p.returncode}\n{p.stdout[-600:]}\n{p.stderr[-600:]}"}
    return RECORD[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True)
    ap.add_argument("--upstream", default="", help="同版本官方二进制（给了就比 A 档）")
    ap.add_argument("--persona", default="")
    ap.add_argument("--deny", default="group:codeintel,group:atomgit,group:subagent")
    ap.add_argument("--port", type=int, default=18099)
    args = ap.parse_args(argv)

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    tmp = Path(tempfile.mkdtemp(prefix="fork-verify-"))
    work = tmp / "work"; work.mkdir()
    deny = [x.strip() for x in args.deny.split(",") if x.strip()]
    rc = 0
    try:
        a = run_once(args.binary, args.port, _home(tmp, "a"), work, None, [])
        b = run_once(args.binary, args.port, _home(tmp, "b"), work, args.persona or None, []) \
            if args.persona else None
        c = run_once(args.binary, args.port, _home(tmp, "c"), work, args.persona or None, deny) \
            if args.persona else None
        up = run_once(args.upstream, args.port, _home(tmp, "u"), work, None, []) \
            if args.upstream else None

        for name, r in (("A 不配任何参数", a), ("B 只换人设", b),
                        ("C 换人设 + deny", c), ("U 官方二进制", up)):
            if r is None:
                continue
            if "error" in r:
                print(f"✗ {name}：{r['error']}"); rc = 1; continue
            print(f"\n=== {name} ===")
            print(f"  系统提示 {sum(r['system_parts'])} 字符（{len(r['system_parts'])} 条）"
                  f"：{r['system_head'][:90]!r}")
            print(f"  工具 {len(r['tools'])} 个")

        if up and "error" not in up and "error" not in a:
            same_sys = up["system_full"] == a["system_full"]
            same_tools = up["tools"] == a["tools"]
            print(f"\n[A vs 官方] 系统提示逐字节相同：{'✓' if same_sys else '✗'}；"
                  f"工具清单相同：{'✓' if same_tools else '✗'}")
            if not same_sys:
                print(f"  官方 {len(up['system_full'])} 字符 vs fork {len(a['system_full'])} 字符")
                rc = 1
            if not same_tools:
                print(f"  只在官方：{sorted(set(up['tools']) - set(a['tools']))}")
                print(f"  只在 fork：{sorted(set(a['tools']) - set(up['tools']))}")
                rc = 1

        if b and "error" not in b and "error" not in a:
            replaced = b["system_full"] != a["system_full"]
            persona_text = Path(args.persona).read_text(encoding="utf-8").strip()
            carried = persona_text[:80] in b["system_full"]
            print(f"[B] 人设被换掉：{'✓' if replaced else '✗'}；"
                  f"换成的是人设文件的内容：{'✓' if carried else '✗'}；"
                  f"工具数不变：{'✓' if len(b['tools']) == len(a['tools']) else '✗'}")
            if not (replaced and carried and len(b["tools"]) == len(a["tools"])):
                rc = 1

        if c and "error" not in c and "error" not in b:
            gone = sorted(set(b["tools"]) - set(c["tools"]))
            print(f"[C] deny 摘掉了 {len(gone)} 个工具：{gone}")
            print(f"    系统提示与 B 相同：{'✓' if c['system_full'] == b['system_full'] else '✗'}")
            if not gone:
                print("    ✗ 一个都没摘掉 —— deny 没生效"); rc = 1
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nrc={rc}")
    return rc


def _home(tmp: Path, name: str) -> Path:
    h = tmp / name
    h.mkdir(parents=True, exist_ok=True)
    return h


if __name__ == "__main__":
    raise SystemExit(main())
