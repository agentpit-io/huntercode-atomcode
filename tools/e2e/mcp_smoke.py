#!/usr/bin/env python3
"""9 个 MCP 的真实冒烟 —— 直连 stdio JSON-RPC，**不经过模型**（所以零 token）。

为什么要有它：`/mcp/status` 的 `connected` 只说明 server 起来了、工具列出来了，
**不说明调用能拿到数据**。M1 就踩过：9/9 connected 但 6 个 hunter 系薄代理
调用全部 `Temporary failure in name resolution`（当时 compose 里没有 api）。
所以回归清单里的「全部 MCP」必须逐个真调一次。

    docker exec -i hca-daemon python3 /opt/hca/tools/mcp_smoke.py            # 在容器里跑
    python3 tools/e2e/mcp_smoke.py --json-out out.json

每个 server 挑一个**代表工具**（尽量选只读、便宜、不依赖额外 key 的），
拿到数据算过；明确报错（比如没配 key）也算"行为正确"但标成 `expected-error`，
**绝不把报错当成成功**。
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

# server → (工具名, 参数)。参数都用真实的标的/字段，不用占位。
# 参数按每个工具的 inputSchema.required 填**真实值**（一开始拍脑袋填的少了必填项，
# 被 server 的 Input validation error 挡回来 —— 那是探针写错，不是 MCP 坏了）。
PROBES = {
    "akshare":     ("akshare_search", {"keyword": "实时行情", "limit": 5}),
    "kronos":      ("kronos_health", {}),
    "truesource":  ("truesource_macro", {"days": 3}),
    "uzi":         ("stock_deep_analysis", {"code": "600519"}),
    "watchlist":   ("stock_quickview", {"code": "600519"}),
    "portfolio":   ("portfolio_stress", {"shock_code": "600519", "shock_pct": -10}),
    # market_screen 要么给 script 要么给 preset（只给 limit 会被它自己挡回来）。
    # 用 A 股的预置脚本，省得在探针里维护一份筛选语法。
    "screener":    ("market_screen", {"preset": "value_oversold", "market": "a", "limit": 5}),
    "hunter_cap":  ("skill_staged", {}),
    "hunter_user": ("list_my_sources", {}),
}

# 这些 server 拿到「没配 key / 没有数据」这类**明确说明**也算行为正确
KEY_DEPENDENT = {"kronos", "truesource"}


def load_mcp_json(path: Path) -> dict:
    raw = re.sub(r"(?m)^\s*//.*$", "", path.read_text(encoding="utf-8"))
    d = json.loads(raw)
    return d.get("mcpServers") or d


def expand(v: str, _env: dict) -> str:
    """把 `${VAR}` / `${VAR:-默认}` 按**进程环境**展开 —— 和 AtomCode 自己的做法一致。

    ⚠️ 不能拿 `.mcp.json` 里那份 env 字典去查：它的值本身就是 `${AKSHARE_MAX_ROWS:-50}`
    这种占位符，查到的是自己，替换完还是占位符原文，server 一 `int()` 就崩
    （第一版就这么写的，表现是 akshare 的 initialize 直接 EOF）。
    """
    def rep(m):
        name, default = m.group(1), m.group(3)
        return os.environ.get(name) or (default or "")
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}", rep, v)


def call(server: str, cfg: dict, tool: str, args: dict, timeout: float, uid: str):
    """起一个 stdio server，initialize → tools/list → tools/call，返回 (ok, 说明, 摘要)。"""
    env = dict(os.environ)
    for k, v in (cfg.get("env") or {}).items():
        env[k] = expand(str(v), cfg.get("env") or {})
    argv = [cfg["command"], *[expand(a, cfg.get("env") or {}) for a in cfg.get("args", [])]]
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=env, text=True, bufsize=1)

    def send(obj):
        p.stdin.write(json.dumps(obj) + "\n")
        p.stdin.flush()

    def recv(want_id, deadline):
        while time.time() < deadline:
            line = p.stdout.readline()
            if not line:
                return None
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            if m.get("id") == want_id:
                return m
        return None

    t_end = time.time() + timeout
    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "hca-mcp-smoke", "version": "1"}}})
        if recv(1, t_end) is None:
            return False, "initialize 没回应（或超时）", p.stderr.read()[:300]
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tl = recv(2, t_end)
        names = [t.get("name") for t in ((tl or {}).get("result", {}).get("tools") or [])]
        if tool not in names:
            return False, "工具 {} 不在 tools/list（实得 {}）".format(tool, names[:6]), ""
        a = dict(args)
        # hunter 系薄代理从参数里取身份（P0-5），这里直接给
        if server in ("uzi", "watchlist", "portfolio", "hunter_cap", "hunter_user") and uid:
            a.setdefault("_hermes_user_id", uid)
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": tool, "arguments": a}})
        r = recv(3, t_end)
        if r is None:
            return False, "tools/call 没回应（或超时）", p.stderr.read()[:300]
        if "error" in r:
            return False, "JSON-RPC error：{}".format(str(r["error"])[:200]), ""
        content = (r.get("result") or {}).get("content") or []
        text = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        is_err = bool((r.get("result") or {}).get("isError"))
        if is_err:
            return False, "isError=true", text
        # 返回体里自己报了错（没配 key / 后端不可达）**不算拿到数据** ——
        # 这类要么是期望内（KEY_DEPENDENT），要么就是真问题，不许混成"过"。
        low = (text or "")[:600].lower()
        if any(k in low for k in ('"error"', "invalid_api_key", "call failed",
                                  "missing an \'http", "connecterror")):
            return False, "返回体里是错误对象（不是数据）", text
        # **空返回也不算"拿到数据"**。上面几条判的是"返回里写着错"，
        # 可 `content: []` / `""` / `{}` / `[]` / `null` 这几种是"什么都没给"——
        # 原来会直接判过，于是"这个 MCP 通了"这个结论可以在零数据的情况下成立。
        # 真实返回最短的也有几十字节（都是 JSON 对象），20 字节这个下限不会误伤。
        stripped = (text or "").strip()
        if len(stripped) < 20 or stripped in ("{}", "[]", "null", '""'):
            return False, "返回是空的（{} 字节），不算拿到数据".format(len(stripped)), text
        return True, "ok", text
    finally:
        try:
            p.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            p.wait(timeout=5)
        except Exception:  # noqa: BLE001
            p.kill()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mcp-json", default=os.environ.get("HCA_MCP_JSON", "/workspace/.mcp.json"))
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--json-out", default="")
    ap.add_argument("--uid", default=os.environ.get("HUNTER_USER_ID", ""))
    a = ap.parse_args()

    servers = load_mcp_json(Path(a.mcp_json))
    print("MCP 冒烟 · {} 个 server · 直连 stdio，不经模型".format(len(servers)))
    print("{:<13} {:<24} {:<8} {:<9} {}".format("server", "工具", "结果", "耗时", "返回摘要 / 原因"))
    print("─" * 112)
    recs, bad = [], 0
    for name, cfg in servers.items():
        tool, args = PROBES.get(name, (None, None))
        if not tool:
            print("{:<13} {:<24} {:<8} {:<9} {}".format(name, "—", "跳过", "—", "没有配探针"))
            continue
        t0 = time.time()
        try:
            ok, why, text = call(name, cfg, tool, args, a.timeout, a.uid)
        except Exception as e:  # noqa: BLE001
            ok, why, text = False, "{}：{}".format(type(e).__name__, e), ""
        dt = time.time() - t0
        head = re.sub(r"\s+", " ", (text or "")).strip()[:58]
        state = "过" if ok else ("期望内报错" if name in KEY_DEPENDENT else "失败")
        if not ok and name not in KEY_DEPENDENT:
            bad += 1
        print("{:<13} {:<24} {:<8} {:<9} {}".format(
            name, tool, state, "{:.1f}s".format(dt), head if ok else (why + " | " + head)))
        recs.append({"server": name, "tool": tool, "ok": ok, "state": state,
                     "seconds": round(dt, 2), "why": why,
                     "text_len": len(text or ""), "text_head": (text or "")[:400]})
    print("─" * 112)
    print("{} 个探针，过 {}，失败 {}".format(len(recs), sum(1 for r in recs if r["ok"]), bad))
    if a.json_out:
        Path(a.json_out).write_text(json.dumps({"servers": recs}, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
        print("明细写到 " + a.json_out)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
