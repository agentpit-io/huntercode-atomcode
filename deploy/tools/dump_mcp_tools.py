#!/usr/bin/env python3
"""直连工作区 `.mcp.json` 里每个 stdio MCP，跑真实 `initialize` + `tools/list`。

两个用途：

1. **生成 `distro/mcp-tools.json`**（`--write`）——
   `tools/build_skills.py` 的工具名映射表。这张表是**跑出来的，不是抄源码的**：
   抄源码会漏掉装饰器改名、条件注册之类的情况。
2. **部署自检**（默认）—— 每个 server 能不能起来、起来要多久、暴露几个工具。
   `GET /mcp/status` 只告诉你 `connected` 与 `tool_count`，出问题时
   看不到 stderr；这个脚本直连，能把 server 的报错原文打出来。

跑法（在 daemon 容器里）：

    docker compose -p hca exec daemon python3 /opt/hca/tools/dump_mcp_tools.py
    docker compose -p hca exec daemon python3 /opt/hca/tools/dump_mcp_tools.py --json

安全（总控红线 3）：子进程一律用参数数组 `[command, *args]`，不拼 shell 字符串；
`.mcp.json` 走 json.loads，不 eval。env 里的值不打印（可能是 key）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_MCP_JSON = Path(os.environ.get("HCA_WORKSPACE", "/workspace")) / ".mcp.json"
# 与 AtomCode 的 expand_env_vars 同口径（上游 mcp/config.rs:524）：${VAR} 与 ${VAR:-默认值}
VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
# initialize 首次要 import pandas/akshare，M0 实测 2.65–5.88 秒，给足余量
DEFAULT_TIMEOUT = 60.0


def expand_env(value: str) -> str:
    """复刻 AtomCode 的 `expand_env_vars`。**必须复刻**，不能直接把 `${...}`
    当字面量传给子进程 —— 否则这个探针测的就不是 daemon 实际跑的那套 env，
    "直连能通、daemon 里不通" 这类问题会被它糊过去。"""
    def sub(m: re.Match[str]) -> str:
        name, default = m.group(1), m.group(2)
        val = os.environ.get(name)
        if val:
            return val
        return default if default is not None else ""
    return VAR_RE.sub(sub, value)


def load_mcp_json(path: Path) -> dict:
    """读 `.mcp.json`。AtomCode 支持 `//` 与 `/* */` 注释、不支持尾逗号，
    这里按同样的口径先剥注释再 json.loads，然后展开 command / args / env 里的变量。"""
    raw = path.read_text(encoding="utf-8")
    raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    raw = re.sub(r"^\s*//.*$", "", raw, flags=re.M)
    data = json.loads(raw)
    servers = data.get("mcpServers") or data.get("servers") or {}
    for cfg in servers.values():
        if isinstance(cfg.get("command"), str):
            cfg["command"] = expand_env(cfg["command"])
        if isinstance(cfg.get("args"), list):
            cfg["args"] = [expand_env(a) if isinstance(a, str) else a for a in cfg["args"]]
        if isinstance(cfg.get("env"), dict):
            cfg["env"] = {k: expand_env(v) if isinstance(v, str) else v
                          for k, v in cfg["env"].items()}
    return servers


class StdioClient:
    """最小的 MCP stdio JSON-RPC 客户端：只做 initialize + tools/list。"""

    def __init__(self, name: str, cfg: dict, timeout: float):
        self.name = name
        self.cfg = cfg
        self.timeout = timeout
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "StdioClient":
        argv = [self.cfg["command"], *self.cfg.get("args", [])]
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in (self.cfg.get("env") or {}).items()})
        self.proc = subprocess.Popen(
            argv,  # 参数数组，绝不拼 shell 字符串
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        return self

    def __exit__(self, *exc) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def _send(self, obj: dict) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _recv(self, deadline: float) -> dict:
        assert self.proc and self.proc.stdout
        while True:
            if time.time() > deadline:
                raise TimeoutError(f"{self.timeout:.0f} 秒内没等到响应")
            line = self.proc.stdout.readline()
            if not line:
                err = (self.proc.stderr.read() if self.proc.stderr else "") or ""
                raise RuntimeError(f"server 提前退出（exit={self.proc.poll()}）：{err[:1500]}")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                # server 往 stdout 打了非协议内容 —— 记下来，继续等
                continue
            if "id" in msg or "error" in msg:
                return msg

    def list_tools(self) -> tuple[list[str], float, dict]:
        deadline = time.time() + self.timeout
        t0 = time.time()
        self._send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "hca-dump-mcp-tools", "version": "1"},
            },
        })
        init = self._recv(deadline)
        if "error" in init:
            raise RuntimeError(f"initialize 失败：{init['error']}")
        init_ms = (time.time() - t0) * 1000
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        res = self._recv(deadline)
        if "error" in res:
            raise RuntimeError(f"tools/list 失败：{res['error']}")
        tools = [t["name"] for t in res.get("result", {}).get("tools", [])]
        return tools, init_ms, init.get("result", {}).get("serverInfo", {})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="直连 .mcp.json 里的 stdio MCP 跑 tools/list")
    ap.add_argument("--mcp-json", type=Path, default=DEFAULT_MCP_JSON)
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--only", action="append", default=[], help="只测这些 server（可重复）")
    ap.add_argument("--write", type=Path, help="把结果写成 distro/mcp-tools.json 格式")
    ap.add_argument("--json", dest="as_json", action="store_true", help="输出 JSON 而不是表格")
    args = ap.parse_args(argv)

    servers = load_mcp_json(args.mcp_json)
    if args.only:
        servers = {k: v for k, v in servers.items() if k in args.only}
    if not servers:
        print(f"✗ {args.mcp_json} 里没有 mcpServers", file=sys.stderr)
        return 2

    results: dict[str, dict] = {}
    for name, cfg in servers.items():
        if cfg.get("disabled"):
            results[name] = {"ok": False, "error": "disabled", "tools": [], "init_ms": None}
            continue
        try:
            with StdioClient(name, cfg, args.timeout) as c:
                tools, init_ms, info = c.list_tools()
            results[name] = {
                "ok": True, "tools": tools, "init_ms": round(init_ms, 1),
                "server_info": info,
            }
        except Exception as e:  # noqa: BLE001 —— 要把每个 server 的失败原因原样留住
            results[name] = {"ok": False, "error": f"{type(e).__name__}: {e}", "tools": [], "init_ms": None}

    if args.as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(f"{'服务':<14}{'状态':<8}{'工具数':<8}{'initialize':<14}工具")
        for name, r in results.items():
            status = "✅ ok" if r["ok"] else "✗ 失败"
            ms = f"{r['init_ms']} ms" if r["init_ms"] is not None else "—"
            print(f"{name:<14}{status:<8}{len(r['tools']):<8}{ms:<14}{' '.join(r['tools'])}")
            if not r["ok"]:
                print(f"{'':<14}原因：{r['error']}")
        ok = sum(1 for r in results.values() if r["ok"])
        print(f"\n合计：{ok}/{len(results)} 个 server 可用，"
              f"{sum(len(r['tools']) for r in results.values())} 个工具")

    if args.write:
        failed = [n for n, r in results.items() if not r["ok"]]
        if failed:
            print(f"✗ 有 server 起不来，不写注册表（会写出残缺的表）：{failed}", file=sys.stderr)
            return 1
        payload = json.loads(args.write.read_text(encoding="utf-8")) if args.write.exists() else {}
        payload["servers"] = {n: r["tools"] for n, r in results.items()}
        payload["verified_on"] = os.environ.get("HCA_VERIFIED_ON") or time.strftime("%Y-%m-%d %H:%M %Z")
        args.write.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"已写入 {args.write}")

    return 0 if all(r["ok"] for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
