#!/usr/bin/env python3
"""技能端到端探针 · 在 daemon 容器里跑，走 `/live` 发一个真实问题，把整条 SSE 存下来。

为什么走 `/live` 而不是任务书写的 `/chat`：M0 §5.4 实测 **`/chat` 不挂载 MCP 工具**
（模型可见 36 个工具，没有一个 `mcp__*`；调用直接返回 `unknown or unmounted tool`），
只有 `/live` 挂载（39 个）。投研问题没有 MCP 就等于没有数据，在 `/chat` 上测"技能
调用了哪些 MCP 工具"会得到一个注定为空的答案。已记入待办池 P0-1。

用法（容器内）：

    python3 /opt/hca/tools/skill_e2e.py --id d1 \\
        --message "帮我写一份 600519 贵州茅台的深度投研报告" \\
        --out /workspace/.e2e

输出：`<out>/<id>.sse`（原始流）+ 一行 JSON 汇总（stdout）。
所有数字都从真实 SSE 里取，取不到就是 null，**不填默认值**。

## tokens 为什么另外查网关配额

`/live` 的 `tokens` 事件与 `state.stats` 里的 token 字段**大多数轮次是 0**
（M1 实测：一条 12 轮的会话里 12 个 `tokens` 事件全 0，而同一会话的第一轮
拿到过 prompt 25610 / completion 43）。也就是说网关只在部分响应里回 `usage`，
AtomCode 如实透传。想知道一个用例真花了多少 token，只能**在用例前后各查一次
网关配额**，差值就是实际消耗 —— 这是真实计量，不是估算。
配额端点：`GET {上游}/quota`，与 M0 的探针脚本用的是同一个。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

PORT = os.environ.get("HCA_DAEMON_PORT", "13456")
BASE = f"http://127.0.0.1:{PORT}"
TOKEN_FILE = Path(os.environ.get("HCA_TOKEN_DIR", "/run/hca")) / "daemon-token"


def auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer " + TOKEN_FILE.read_text(encoding="utf-8").strip()}


def post(path: str, body: dict | None = None, timeout: float = 30.0) -> dict:
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=data, method="POST",
        headers={**auth_header(), "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else {}


def quota_used() -> int | None:
    """查网关已用配额。取不到返回 None —— **不猜、不填 0**。"""
    key = (os.environ.get("HCA_LLM_API_KEY") or "").strip()
    if not key:
        f = (os.environ.get("HCA_LLM_API_KEY_FILE") or "").strip()
        if f and Path(f).is_file():
            key = Path(f).read_text(encoding="utf-8").strip()
    if not key:
        return None
    url = (os.environ.get("HCA_QUOTA_URL")
           or "https://hunter.agentpit.io/api/saas/llm/quota")
    try:
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    # hunter 网关实测返回：{"ok":true,"used_today":…,"limit_daily":…,"remaining":…,…}
    for k in ("used_today", "used", "used_tokens", "consumed"):
        if isinstance(d.get(k), int):
            return d[k]
    # 只给 limit/remaining 时用差值
    lim, rem = d.get("limit_daily") or d.get("limit"), d.get("remaining")
    if isinstance(lim, int) and isinstance(rem, int):
        return lim - rem
    return None


def new_session_and_switch(reload_timeout: float = 120.0) -> tuple[str | None, str]:
    """开一个干净会话，切过去，**再把 MCP 重新挂上并等它连好**。

    两个坑，都是 M1 实测出来的：

    1. 不能用 `POST /cd` 换会话：cd 到**同一个目录**时 `session_id` 不变
       （上游注释说会「cd + 开新会话」，实际没有），上一轮的上下文照样在。
       要干净会话得 `POST /sessions` + `POST /live/switch_session`。
    2. **切完会话之后 MCP 工具会全部消失。** 受控实验：同一个 daemon，
       不切会话时模型自述能调 28 个 `mcp__*`；`switch_session` 之后模型自述
       「没有任何名字以 mcp__ 开头的工具（数量为 0）」。这和 M0 §5.4 的
       `/chat` 不挂 MCP 是同一类根因 —— 只有走 `wait_mcp_ready()` 的路径才等
       MCP 就绪，`resume_session_with_lease` 不在其中。
       表现极具误导性：`/mcp/status` 照样是 9/9 connected，模型却一个都看不到，
       于是它改用内置 `bash` + `python3 -c ... requests` 自己去抓数据。

       解法（已验证）：切完再 `POST /mcp/reload`，并且**等到 `/mcp/status`
       里没有 `connecting` 为止**，MCP 工具就回来了（模型自述 28 个）。
       只 reload 不等是没用的 —— 立刻发问仍然是 0 个。
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


def get(path: str, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(BASE + path, headers=auth_header())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class LiveStream:
    """GET /live 的 SSE 读取线程。整条流原样落盘，解析只在内存里做。

    **会自动应答 `permission_request`**（默认 allow）。不应答就是死等：
    M1 实测一个 `bash` 的审批请求把整轮挂了 300 秒（探针超时才收场），
    而前端在那种情况下本来就是弹个框让人点。每一次应答都记进 `permissions`，
    报告里照实写"这一轮弹了几次权限、都是什么工具"。

    默认 `build` 档下 MCP 工具因为 `.mcp.json` 的 autoApprove 不弹，
    但模型还会用内置 `bash`/`write_file`，那些照样弹。
    """

    def __init__(self, sink: Path, permission_decision: str = "allow"):
        self.sink = sink
        self.permission_decision = permission_decision
        self.permissions: list[dict] = []
        self.events: list[dict] = []
        self.done = threading.Event()
        self.started = threading.Event()
        self.error: str | None = None
        self._fh = sink.open("w", encoding="utf-8")
        self._t = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._t.start()

    def _run(self) -> None:
        try:
            req = urllib.request.Request(BASE + "/live", headers=auth_header())
            with urllib.request.urlopen(req, timeout=600) as r:
                for line in r:
                    s = line.decode("utf-8", "replace")
                    self._fh.write(s)
                    self._fh.flush()
                    if not s.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(s[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    self.events.append(ev)
                    if ev.get("type") == "snapshot":
                        self.started.set()
                    if ev.get("type") == "permission_request":
                        self._answer(ev)
                    if ev.get("type") == "state" and ev.get("running") is False:
                        self.done.set()
                        return
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            self.done.set()
        finally:
            self._fh.close()

    def _answer(self, ev: dict) -> None:
        body = {"decision": self.permission_decision}
        name = ev.get("tool_name")
        if name:
            body["tool_name"] = name
        try:
            res = post("/live/permission", body, timeout=15)
        except Exception as e:  # noqa: BLE001
            res = {"error": f"{type(e).__name__}: {e}"}
        self.permissions.append({
            "tool_name": name,
            "reason": ev.get("reason"),
            "arguments": ev.get("arguments"),
            "decision": self.permission_decision,
            "response": res,
        })


def summarize(events: list[dict], wall_ms: float) -> dict:
    tool_starts = [e for e in events if e.get("type") == "tool_start"]
    # 按**出现顺序**配对 tool_start 与 tool_result：同一个工具在一轮里被调多次时，
    # 按名字取 [0] 会让三次调用显示成同一个结果（第一版就是这么错的，
    # 报告里会出现三行一模一样的 duration_ms）。
    pending: dict[str, list[dict]] = {}
    for e in events:
        if e.get("type") == "tool_result":
            pending.setdefault(e.get("name"), []).append(e)
    cursor: dict[str, int] = {}

    skills_used: list[str] = []
    for e in tool_starts:
        if e.get("name") == "use_skill":
            try:
                a = json.loads(e.get("arguments") or "{}")
            except json.JSONDecodeError:
                a = {}
            skills_used.append(a.get("name") or a.get("skill") or "?")

    mcp_calls = []
    for e in tool_starts:
        n = e.get("name") or ""
        i = cursor.get(n, 0)
        cursor[n] = i + 1
        if not n.startswith("mcp__"):
            continue
        lst = pending.get(n, [])
        res = lst[i] if i < len(lst) else None
        out = (res.get("output") or "") if res else ""
        try:
            call_args = json.loads(e.get("arguments") or "{}")
        except json.JSONDecodeError:
            call_args = e.get("arguments")
        mcp_calls.append({
            "tool": n,
            "seq": i + 1,
            "arguments": call_args,
            "success": res.get("success") if res else None,
            "duration_ms": res.get("duration_ms") if res else None,
            "output_head": out[:300],
        })

    final_state = next(
        (e for e in reversed(events) if e.get("type") == "state" and e.get("running") is False),
        {},
    )
    stats = final_state.get("stats") or {}
    tokens = [e for e in events if e.get("type") == "tokens"]
    text = "".join(e.get("content") or e.get("text") or "" for e in events if e.get("type") == "text")

    return {
        "wall_ms": round(wall_ms),
        "stop_reason": final_state.get("stop_reason"),
        "rounds": stats.get("rounds"),
        "tool_calls": stats.get("tool_calls"),
        "duration_ms": stats.get("duration_ms"),
        "prompt_tokens": stats.get("prompt_tokens"),
        "completion_tokens": stats.get("completion_tokens"),
        "cached_tokens": stats.get("cached_tokens"),
        "tokens_events": [
            {"prompt": t.get("prompt"), "completion": t.get("completion"), "total": t.get("total")}
            for t in tokens
        ],
        "skills_used": skills_used,
        "all_tools": [e.get("name") for e in tool_starts],
        "mcp_calls": mcp_calls,
        "text_len": len(text),
        "text_head": re.sub(r"\s+", " ", text[:400]),
        "errors": [e for e in events if e.get("type") == "error"],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--message", required=True)
    ap.add_argument("--out", type=Path, default=Path("/workspace/.e2e"))
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--permission", default="allow",
                    choices=["allow", "deny", "always_allow"],
                    help="自动应答 permission_request 的决定（默认 allow）")
    ap.add_argument("--fresh", action="store_true",
                    help="每个用例开一个干净会话（POST /sessions + /live/switch_session），"
                         "免得上一轮的上下文进了下一轮的 prompt")
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    sse = args.out / f"{args.id}.sse"

    switched_to, switch_err = None, ""
    if args.fresh:
        switched_to, switch_err = new_session_and_switch()
        if switched_to is None:
            print(f"[skill_e2e] ⚠ 没能切到新会话（{switch_err}），本用例会带着上一轮的上下文跑",
                  file=sys.stderr)

    stream = LiveStream(sse, permission_decision=args.permission)
    stream.start()
    if not stream.started.wait(timeout=30):
        print(json.dumps({"id": args.id, "error": f"30 秒内没收到 /live 的 snapshot 帧：{stream.error}"},
                         ensure_ascii=False))
        return 1

    try:
        mcp_snapshot = get("/mcp/status")
        mcp_connected = sum(1 for x in mcp_snapshot.get("servers", [])
                            if x.get("status") == "connected")
        mcp_total = len(mcp_snapshot.get("servers", []))
    except Exception:  # noqa: BLE001
        mcp_connected = mcp_total = None

    quota_before = quota_used()
    t0 = time.time()
    try:
        accepted = post("/live/message", {"message": args.message})
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"id": args.id, "error": f"POST /live/message 失败：{e}"}, ensure_ascii=False))
        return 1

    finished = stream.done.wait(timeout=args.timeout)
    wall = (time.time() - t0) * 1000
    quota_after = quota_used()

    summary = {"id": args.id, "message": args.message, "accepted": accepted,
               "finished": finished, "sse": str(sse),
               "session_switched_to": switched_to, "session_switch_error": switch_err,
               "quota_used_before": quota_before, "quota_used_after": quota_after,
               "quota_delta": (quota_after - quota_before)
               if isinstance(quota_before, int) and isinstance(quota_after, int) else None,
               "permissions": stream.permissions,
               "mcp_connected": mcp_connected, "mcp_total": mcp_total}
    summary.update(summarize(stream.events, wall))
    if stream.error:
        summary["stream_error"] = stream.error
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if finished else 2


if __name__ == "__main__":
    raise SystemExit(main())
