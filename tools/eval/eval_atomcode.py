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


def fresh_session(reload_timeout: float = 150.0, reload_rounds: int = 5):
    """开干净会话并**把 MCP 挂回来**。

    切会话之后 MCP 工具会全部消失而 `/mcp/status` 仍是 9/9 connected（待办池 P0-10）。
    绕法：`POST /mcp/reload` 之后**等到没有 connecting 为止**。只 reload 不等没用。
    """
    try:
        created = post("/sessions", {})
        sid = created.get("id") or created.get("session_id")
        if not sid:
            return None, f"POST /sessions 没回 id：{created}"
        # 刚重建过容器时 /live 还没绑过任何会话，第一次 switch 会被拒成
        # `session switch rejected: Unbound`。那种情况下**本来就没有上一轮的上下文**，
        # 属于良性；重试两次仍然是 Unbound 就按"已经是干净会话"继续。
        res, unbound = {}, False
        for attempt in range(3):
            res = post("/live/switch_session", {"session_id": sid})
            if res.get("ok"):
                break
            if "Unbound" in str(res.get("error") or ""):
                if attempt == 2:
                    unbound = True
                    break
            time.sleep(2)
        if not res.get("ok") and not unbound:
            return None, f"switch_session 被拒：{res}"
        # ⚠️ Unbound 也要往下走 MCP 那一段。早先这里是直接 return 的，结果
        # 「daemon 刚重建、一条消息都没发过」的那种会话**完全跳过了 MCP 校验** ——
        # 实测把 akshare 的 command 改成不存在的路径去验闸，探针照样把题发了出去、
        # 烧掉 24 755 token（rc=0），因为它压根没走到检查那一步。
        unbound_note = ("switch_session 恒为 Unbound（daemon 还没绑过会话，"
                        f"视为已是干净会话）：{res}" if unbound else "")
        # reload 最多试 5 轮：9 个 MCP 同时冷启动在 2 核机器上会有 server 超时
        # （待办池 P2-7，实测撞到过 5 个 `initialize timed out after 60000ms`）。
        # 少连上几个而不自知，后面几十次运行就会在"工具比上一次少"的状态下跑，
        # 数据没法用。所以这里**等到全部 connected 为止**，实在不行也如实记下来。
        note = ""
        for attempt in range(reload_rounds):
            post("/mcp/reload", {}, timeout=30)
            deadline = time.time() + reload_timeout
            st = {}
            while time.time() < deadline:
                try:
                    st = get("/mcp/status")
                except Exception:  # noqa: BLE001
                    break
                if not any(x.get("status") == "connecting" for x in st.get("servers", [])):
                    break
                time.sleep(2)
            servers = st.get("servers", [])
            bad = [x.get("name") for x in servers if x.get("status") != "connected"]
            if servers and not bad:
                return sid, unbound_note
            note = f"第 {attempt + 1} 轮 reload 后仍未连上：{bad}"
            # 退避：实测这种失败几乎全是机器被别的重活占满（load 15 / 2 核）导致
            # server 的 initialize 超时，隔久一点再试比连着试有用
            time.sleep(5 * (attempt + 1))
        return sid, "；".join(x for x in (unbound_note, note) if x)
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


class LiveStream:
    def __init__(self, sink: Path, decision: str):
        self.sink, self.decision = sink, decision
        self.t0 = None
        self.permissions, self.events = [], []
        self.done, self.started = threading.Event(), threading.Event()
        self.error = None
        self._fh = sink.open("w", encoding="utf-8")
        self._t = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._t.start()

    def mark_t0(self, t0: float):
        """把计时原点设成「消息发出去的那一刻」，事件的 _t_ms 都相对它。"""
        self.t0 = t0

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
                    # I2：每个事件记一个相对毫秒数（相对 t0，由 mark_t0 设）。
                    # 瀑布表全靠它 —— daemon 的 SSE 自己不带时间戳，
                    # 而「第 n 轮模型调用花了多久」只能从事件到达的先后算出来。
                    ev["_t_ms"] = (round((time.time() - self.t0) * 1000, 1)
                                   if self.t0 else None)
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


def waterfall(events):
    """把一次运行切成「模型调用 / 工具执行」交替的段落 —— I2 的瀑布表。

    切法只依赖事件到达的先后（`_t_ms`，由 LiveStream 打的相对毫秒）：

      · 从 t=0（消息发出）到第一个 `text`/`tool_start`：**首轮模型调用**
        （含 UserPromptSubmit hook + 首字延迟）
      · `tool_start` → `tool_result`：**工具执行**（含 PreToolUse hook、
        MCP 往返、PostToolUse hook —— 这三段在 SSE 上分不开，实测靠
        `tools/eval/hook_bench.py` 单独量 hook 那部分）
      · `tool_result` → 下一个 `text`/`tool_start`：**下一轮模型调用**
      · 最后一个事件 → `state(running=false)`：收尾

    拿不到 `_t_ms` 的事件跳过，不猜时间。
    """
    ts = [(e.get("_t_ms"), e) for e in events if isinstance(e.get("_t_ms"), (int, float))]
    segs, cursor, round_no = [], 0.0, 1
    first_text_of_round = True
    for t, e in ts:
        typ = e.get("type")
        if typ == "tool_start":
            if t > cursor:
                segs.append({"kind": "model", "round": round_no,
                             "start_ms": cursor, "end_ms": t, "ms": round(t - cursor, 1)})
            segs.append({"kind": "tool", "round": round_no, "tool": e.get("name"),
                         "start_ms": t, "end_ms": None, "ms": None})
            cursor = t
            first_text_of_round = True
        elif typ == "tool_result":
            for s_ in reversed(segs):
                if s_["kind"] == "tool" and s_["end_ms"] is None:
                    s_["end_ms"] = t
                    s_["ms"] = round(t - s_["start_ms"], 1)
                    s_["duration_ms_self"] = e.get("duration_ms")
                    break
            cursor = max(cursor, t)
            round_no += 1
        elif typ == "text" and first_text_of_round:
            if t > cursor:
                segs.append({"kind": "model", "round": round_no, "text_start": True,
                             "start_ms": cursor, "end_ms": t, "ms": round(t - cursor, 1)})
                cursor = t
            first_text_of_round = False
        elif typ == "state" and e.get("running") is False:
            if t > cursor:
                segs.append({"kind": "tail", "round": round_no,
                             "start_ms": cursor, "end_ms": t, "ms": round(t - cursor, 1)})
            cursor = t
    total = {"model_ms": round(sum(s_["ms"] for s_ in segs
                                   if s_["kind"] == "model" and s_["ms"]), 1),
             "tool_ms": round(sum(s_["ms"] for s_ in segs
                                  if s_["kind"] == "tool" and s_["ms"]), 1),
             "tail_ms": round(sum(s_["ms"] for s_ in segs
                                  if s_["kind"] == "tail" and s_["ms"]), 1)}
    return {"segments": segs, "totals": total}


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
        "waterfall": waterfall(events),
        "errors": [e for e in events if e.get("type") == "error"],
    }


def refuse_reason(sid, switch_err: str, require_mcp: bool = True,
                  mcp_ok=None, mcp_all=None):
    """要不要拒绝开跑？返回拒绝理由，没问题返回 None。

    两种情况下这一次运行的数据是不可比的，宁可不跑也不要把它混进 30 次里：

    1. **MCP 没全连上**。q1 试跑里 5 个 MCP 没连上（机器被 cargo 占满，
       server initialize 超时，待办池 P2-7），探针照样把题发了出去，拿回一份
       "只有 4 个数据源"的答案 —— .json 里除了 `mcp_connected=4` 看不出异常。
    2. **没切到干净会话**。上一题的上下文留着，这一题就不是同一个起点。
       `Unbound` 那种是 daemon 还没绑过任何会话，本来就干净，不算。

    判定有两道，因为第一道曾经被控制流绕过去：`fresh_session` 在 `Unbound`
    分支上直接 return 了，压根没走到 MCP 检查，于是 `switch_err` 里当然没有
    「仍未连上」。**第二道直接数 `/mcp/status` 的 connected 数**（`mcp_ok` /
    `mcp_all`），在发消息之前再判一次 —— 这个数是什么就是什么，绕不过去。
    """
    if not require_mcp:
        return None
    if sid is None and "Unbound" not in (switch_err or ""):
        return "没能切到干净会话，拒绝开跑（--require-mcp）"
    if "仍未连上" in (switch_err or ""):
        return "MCP 未全部连上，拒绝开跑（--require-mcp）"
    if mcp_all and mcp_ok is not None and mcp_ok != mcp_all:
        return f"/mcp/status 只有 {mcp_ok}/{mcp_all} connected，拒绝开跑（--require-mcp）"
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--message", required=True)
    ap.add_argument("--out", type=Path, default=Path("/workspace/.eval"))
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--permission", default="deny",
                    choices=["allow", "deny", "always_allow"])
    ap.add_argument("--require-mcp", default="1",
                    help="1（默认）= MCP 没全连上就不发消息、rc=6 退出；0 = 照跑")
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    sse = args.out / f"{args.id}.sse"

    sid, switch_err = fresh_session()
    if sid is None:
        print(f"[eval] ⚠ 没能切到新会话（{switch_err}）", file=sys.stderr)

    require = args.require_mcp not in ("0", "false", "no")
    why = refuse_reason(sid, switch_err, require)
    if why:
        rec = {"id": args.id, "side": "atomcode", "message": args.message,
               "session_id": sid, "session_switch_error": switch_err,
               "error": why, "skipped": True}
        (args.out / f"{args.id}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(rec, ensure_ascii=False))
        return 6

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

    # 第二道闸：直接数 connected，发消息之前最后一次判（见 refuse_reason 的注释）
    why = refuse_reason(sid, switch_err, require, mcp_ok, mcp_all)
    if why:
        rec = {"id": args.id, "side": "atomcode", "message": args.message,
               "session_id": sid, "session_switch_error": switch_err,
               "mcp_connected": mcp_ok, "mcp_total": mcp_all,
               "error": why, "skipped": True}
        (args.out / f"{args.id}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(rec, ensure_ascii=False))
        return 6

    q0 = quota_used()
    t0 = time.time()
    stream.mark_t0(t0)
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
