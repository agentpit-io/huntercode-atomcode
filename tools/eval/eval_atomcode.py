#!/usr/bin/env python3
"""A/B 评测 · HCA 侧跑一道题 —— **在 daemon 容器里跑**，走 `/live`。

与 M1 的 `tools/probe/skill_e2e.py` 是同一套机制（那份是技能端到端探针），
这里的差别只有三处，都是评分需要的：

  1. **落全文**，不只前 400 字 —— 准确性 / 规范符合度要读原文逐条给分。
  2. **记下每一次工具调用的参数与返回摘要**（不只 `mcp__*`）—— B3 要数
     重复调用、无关工具、拿 bash 抓数据。
  3. **不自动放行权限**，默认 `deny`。理由：本发行版的研究会话就是"无人值守"，
     真实用户不在场时没人点确认；自动 allow 等于把一个现场不存在的人补进去，
     测出来的行为不是用户会遇到的行为。每一次权限请求都记进 `permissions`。

用法（容器内）：

    python3 /opt/hca/tools/eval_atomcode.py --id q1-fundamental-r1 \\
        --message "…" --out /workspace/.eval

输出：`<out>/<id>.sse`（原始流）+ `<out>/<id>.json`（结构化）+ 一行 JSON（stdout）。
所有数字都取自真实 SSE，取不到就是 null，**不填默认值**。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

PORT = os.environ.get("HCA_DAEMON_PORT", "13456")
BASE = f"http://127.0.0.1:{PORT}"
TOKEN_FILE = Path(os.environ.get("HCA_TOKEN_DIR", "/run/hca")) / "daemon-token"


def auth() -> dict:
    return {"Authorization": "Bearer " + TOKEN_FILE.read_text(encoding="utf-8").strip()}


def post(path: str, body=None, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body or {}).encode(), method="POST",
        headers={**auth(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else {}


def get(path: str, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(BASE + path, headers=auth())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def quota_used():
    """网关已用配额。取不到返回 None —— 不猜、不填 0。"""
    key = (os.environ.get("HCA_LLM_API_KEY") or "").strip()
    if not key:
        f = (os.environ.get("HCA_LLM_API_KEY_FILE") or "").strip()
        if f and Path(f).is_file():
            key = Path(f).read_text(encoding="utf-8").strip()
    if not key:
        return None
    url = os.environ.get("HCA_QUOTA_URL") or "https://hunter.agentpit.io/api/saas/llm/quota"
    try:
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    for k in ("used_today", "used", "used_tokens", "consumed"):
        if isinstance(d.get(k), int):
            return d[k]
    lim, rem = d.get("limit_daily") or d.get("limit"), d.get("remaining")
    return lim - rem if isinstance(lim, int) and isinstance(rem, int) else None


def fresh_session(reload_timeout: float = 150.0):
    """开干净会话并**把 MCP 挂回来**。

    切会话之后 MCP 工具会全部消失而 `/mcp/status` 仍是 9/9 connected（待办池 P0-10）。
    绕法：`POST /mcp/reload` 之后**等到没有 connecting 为止**。只 reload 不等没用。
    """
    try:
        created = post("/sessions", {})
        sid = created.get("id") or created.get("session_id")
        if not sid:
            return None, f"POST /sessions 没回 id：{created}"
        res = post("/live/switch_session", {"session_id": sid})
        if not res.get("ok"):
            return None, f"switch_session 被拒：{res}"
        post("/mcp/reload", {}, timeout=30)
        deadline = time.time() + reload_timeout
        while time.time() < deadline:
            try:
                st = get("/mcp/status")
            except Exception:  # noqa: BLE001
                break
            if not any(x.get("status") == "connecting" for x in st.get("servers", [])):
                break
            time.sleep(2)
        return sid, ""
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


class LiveStream:
    def __init__(self, sink: Path, decision: str):
        self.sink, self.decision = sink, decision
        self.permissions, self.events = [], []
        self.done, self.started = threading.Event(), threading.Event()
        self.error = None
        self._fh = sink.open("w", encoding="utf-8")
        self._t = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._t.start()

    def _run(self):
        try:
            req = urllib.request.Request(BASE + "/live", headers=auth())
            with urllib.request.urlopen(req, timeout=900) as r:
                for line in r:
                    s = line.decode("utf-8", "replace")
                    self._fh.write(s); self._fh.flush()
                    if not s.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(s[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    self.events.append(ev)
                    t = ev.get("type")
                    if t == "snapshot":
                        self.started.set()
                    elif t == "permission_request":
                        self._answer(ev)
                    elif t == "state" and ev.get("running") is False:
                        self.done.set(); return
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            self.done.set()
        finally:
            self._fh.close()

    def _answer(self, ev):
        body = {"decision": self.decision}
        if ev.get("tool_name"):
            body["tool_name"] = ev["tool_name"]
        try:
            res = post("/live/permission", body, timeout=15)
        except Exception as e:  # noqa: BLE001
            res = {"error": f"{type(e).__name__}: {e}"}
        self.permissions.append({"tool_name": ev.get("tool_name"), "reason": ev.get("reason"),
                                 "arguments": ev.get("arguments"),
                                 "decision": self.decision, "response": res})


def summarize(events, wall_ms):
    starts = [e for e in events if e.get("type") == "tool_start"]
    # 按出现顺序配对 tool_start / tool_result：同名工具一轮里被调多次时，
    # 按名字取 [0] 会让几次调用显示成同一个结果。
    pending = {}
    for e in events:
        if e.get("type") == "tool_result":
            pending.setdefault(e.get("name"), []).append(e)
    cursor, calls = {}, []
    for e in starts:
        n = e.get("name") or ""
        i = cursor.get(n, 0); cursor[n] = i + 1
        lst = pending.get(n, [])
        res = lst[i] if i < len(lst) else None
        try:
            a = json.loads(e.get("arguments") or "{}")
        except json.JSONDecodeError:
            a = e.get("arguments")
        out = (res.get("output") or "") if res else ""
        calls.append({"seq": len(calls) + 1, "tool": n, "arguments": a,
                      "success": res.get("success") if res else None,
                      "duration_ms": res.get("duration_ms") if res else None,
                      "output_len": len(out), "output_head": out[:600]})

    final = next((e for e in reversed(events)
                  if e.get("type") == "state" and e.get("running") is False), {})
    stats = final.get("stats") or {}
    text = "".join(e.get("content") or e.get("text") or ""
                   for e in events if e.get("type") == "text")
    return {
        "wall_ms": round(wall_ms), "stop_reason": final.get("stop_reason"),
        "rounds": stats.get("rounds"), "tool_calls_stat": stats.get("tool_calls"),
        "duration_ms": stats.get("duration_ms"),
        "prompt_tokens": stats.get("prompt_tokens"),
        "completion_tokens": stats.get("completion_tokens"),
        "calls": calls, "text": text, "text_len": len(text),
        "errors": [e for e in events if e.get("type") == "error"],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--message", required=True)
    ap.add_argument("--out", type=Path, default=Path("/workspace/.eval"))
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--permission", default="deny",
                    choices=["allow", "deny", "always_allow"])
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    sse = args.out / f"{args.id}.sse"

    sid, switch_err = fresh_session()
    if sid is None:
        print(f"[eval] ⚠ 没能切到新会话（{switch_err}）", file=sys.stderr)

    stream = LiveStream(sse, args.permission)
    stream.start()
    if not stream.started.wait(timeout=30):
        rec = {"id": args.id, "side": "atomcode",
               "error": f"30 秒内没收到 /live snapshot：{stream.error}"}
        (args.out / f"{args.id}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(rec, ensure_ascii=False)); return 1

    try:
        st = get("/mcp/status")
        mcp_ok = sum(1 for x in st.get("servers", []) if x.get("status") == "connected")
        mcp_all = len(st.get("servers", []))
    except Exception:  # noqa: BLE001
        mcp_ok = mcp_all = None

    q0 = quota_used()
    t0 = time.time()
    try:
        accepted = post("/live/message", {"message": args.message}, timeout=60)
    except Exception as e:  # noqa: BLE001
        rec = {"id": args.id, "side": "atomcode", "error": f"POST /live/message 失败：{e}"}
        (args.out / f"{args.id}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(rec, ensure_ascii=False)); return 1

    finished = stream.done.wait(timeout=args.timeout)
    wall = (time.time() - t0) * 1000
    q1 = quota_used()

    rec = {"id": args.id, "side": "atomcode", "message": args.message,
           "accepted": accepted, "finished": finished, "sse": str(sse),
           "session_id": sid, "session_switch_error": switch_err,
           "mcp_connected": mcp_ok, "mcp_total": mcp_all,
           "quota_used_before": q0, "quota_used_after": q1,
           "quota_delta": (q1 - q0) if isinstance(q0, int) and isinstance(q1, int) else None,
           "permissions": stream.permissions}
    rec.update(summarize(stream.events, wall))
    if stream.error:
        rec["stream_error"] = stream.error
    (args.out / f"{args.id}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    # stdout 只打一行摘要，全文在 .json 里（避免 docker exec 的输出被截断）
    print(json.dumps({k: rec[k] for k in
                      ("id", "side", "finished", "wall_ms", "rounds", "stop_reason",
                       "text_len", "quota_delta")
                      if k in rec}, ensure_ascii=False))
    return 0 if finished else 2


if __name__ == "__main__":
    raise SystemExit(main())
