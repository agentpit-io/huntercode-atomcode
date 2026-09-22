#!/usr/bin/env python3
"""A/B 评测 · 基线（opencode 版）跑一道题 —— 在**宿主**上跑，直接打 opencode 的 HTTP API。

为什么不经 Next.js 的 BFF：HCA 侧是直接打 daemon 的 `/live`，基线也直接打
opencode:3901 才对等。多一层 BFF 会把它自己的超时/重试算进耗时里，而那一层
两边根本不是同一个东西。

鉴权照 `apps/web/app/api/opencode/[...path]/route.ts` 的做法：
Basic（用户名 opencode、口令为空，对应 compose 里两个 OPENCODE_SERVER_* 都留空）
+ `X-Hunter-User-Token: <用户 JWT>`，后者供镜像里的 hunter-auth / hunter-mcp-context
插件识别用户并往 MCP 参数里注入 `_hermes_user_id`。

opencode 的 `POST /session/{id}/message` 是**同步**的：整轮跑完才返回
（web 那边给它留了 10 分钟预算）。所以耗时直接测这一个请求，再
`GET /session/{id}/message` 把完整的 parts 取回来数工具调用。

输出：`<out>/<id>.json`（结构化，含全文）+ `<out>/<id>.raw.json`（opencode 原始消息）。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("HCA_OPENCODE_URL", "http://127.0.0.1:13931").rstrip("/")


def headers(token: str) -> dict:
    user = os.environ.get("OPENCODE_SERVER_USERNAME", "opencode")
    pw = os.environ.get("OPENCODE_SERVER_PASSWORD", "")
    basic = base64.b64encode(f"{user}:{pw}".encode()).decode()
    h = {"Authorization": f"Basic {basic}", "Content-Type": "application/json"}
    if token:
        h["X-Hunter-User-Token"] = token
    return h


def call(method: str, path: str, token: str, body=None, timeout: float = 600.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers=headers(token))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
        return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"_raw": raw[:1000]}


def claim_session(api: str, token: str, session_id: str, title: str):
    """把新建的会话登记到 api 的 `chat_session_owner` —— **少这一步身份就断了**。

    opencode 镜像里的 `hunter-mcp-context` 插件是这样解析用户的
    （`tools/opencode-mcp/plugins/hunter-mcp-context.ts`）：
    sessionUsers 缓存 → **反查 `GET /api/internal/session/{sid}/user`** → fallback。
    而那张表的数据由 Web 的 BFF 在建完会话后立刻 `POST /api/chat/sessions` 写入
    （`apps/web/app/api/opencode/[...path]/route.ts:458`，登记失败它直接报
    「会话创建成功但归属登记失败」）。

    我们绕过 BFF 直接打 opencode，就必须自己补这一步。不补的话 MCP 拿不到
    `_hermes_user_id`，`watchlist_*` / `portfolio_*` 一律返回「需要登录后才能…」，
    模型于是回答"当前尚未登录" —— 实测过，那会把基线的持仓类题目全判死，
    比出来的是配置错误而不是 agent 能力。
    """
    data = json.dumps({"session_id": session_id, "title": title}).encode()
    req = urllib.request.Request(
        api.rstrip("/") + "/api/chat/sessions", data=data, method="POST",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"_raw": e.read().decode("utf-8", "replace")[:300]}
    except Exception as e:  # noqa: BLE001
        return 0, {"_err": f"{type(e).__name__}: {e}"}


def quota_used():
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


def flatten(raw):
    """`GET /session/{id}/message` 回的是 [{info, parts}]，展平成 parts 列表。"""
    msgs = raw if isinstance(raw, list) else (raw.get("data") or [])
    out = []
    for m in msgs:
        info = m.get("info") if isinstance(m, dict) and "info" in m else m
        parts = (m.get("parts") if isinstance(m, dict) else None) or (info or {}).get("parts") or []
        # `time` 一定要带出来 —— `waterfall()` 全靠 info.time.{created,completed}
        # 与各 part 自己的时间戳拼段落。第一版忘了带，瀑布表整个是空的。
        out.append({"role": (info or {}).get("role"), "id": (info or {}).get("id"),
                    "time": (info or {}).get("time"),
                    "error": (info or {}).get("error"), "parts": parts})
    return out


def waterfall(messages):
    """社区版这一侧的瀑布表。

    ## 为什么之前没有，以及为什么必须有

    I2 §4.1 从「步数相同时 HCA 的墙钟约是社区版的 1.8～2 倍」推出「差的是引擎侧
    固定开销」。那一步推理**缺一个量**：社区版每一轮模型要多久，从来没量过 ——
    HCA 侧有 SSE 逐事件的相对时刻，社区版这边的探针是一次阻塞 POST，只有总墙钟。
    于是"引擎侧开销"成了一个由减法得到、无法证伪的余项。

    其实数据一直都在：`GET /session/{id}/message` 回的每条消息带
    `info.time.{created,completed}`，工具 part 带 `state.time.{start,end}`，
    文本 part 带 `time.{start,end}` —— 全是毫秒时间戳。够拼出和 HCA 侧同口径的
    「模型 → 工具 → 模型 → 出字」四类段落。

    ## 一个坑：并行工具调用

    同一条 assistant 消息里的几个 tool part 是**并行**发出的（社区版 q5 三次
    `stock_news` 就是），它们的 `start` 都早于前一个的 `end`。按「上一段的 end
    就是下一段的 start」串着算会算出**负数**段（第一版就是这样，q5 上出了三个
    负值）。所以游标只许前进：`cursor = max(cursor, end)`，并且 `model` 段只在
    `start > cursor` 时才记 —— 并行的第二、三个工具不再各记一次模型等待。
    """
    t0 = None
    segs = []
    for m in messages:
        if m.get("role") == "user":
            c = (m.get("time") or {}).get("created")
            if isinstance(c, (int, float)):
                t0 = c          # `or t0` 在 created==0 时会把 0 当假值丢掉
    if t0 is None:
        return {}
    cursor = t0
    round_no = 1
    for m in messages:
        if m.get("role") != "assistant":
            continue
        # **游标不因为 assistant 消息的 `created` 而前进。** 上一段结束到下一条
        # assistant 消息被创建之间的那段（实测约 1 秒）是引擎在跑下一步，属于模型段；
        # 把游标推到 `created` 会把它悄悄扣掉。HCA 侧的口径就是「上一段结束 → 下一段
        # 开始」，这边要一致才可比。
        for p in m.get("parts") or []:
            typ = p.get("type")
            if typ == "tool":
                st = ((p.get("state") or {}).get("time") or {})
                a, b = st.get("start"), st.get("end")
                if not isinstance(a, (int, float)):
                    continue
                if a > cursor:
                    segs.append({"kind": "model", "round": round_no,
                                 "start_ms": cursor - t0, "end_ms": a - t0,
                                 "ms": round(a - cursor, 1)})
                seg = {"kind": "tool", "round": round_no, "tool": p.get("tool"),
                       "start_ms": a - t0, "end_ms": None, "ms": None}
                if isinstance(b, (int, float)):
                    seg["end_ms"], seg["ms"] = b - t0, round(b - a, 1)
                    cursor = max(cursor, b)
                segs.append(seg)
                round_no += 1
            elif typ == "text":
                pt = p.get("time") or {}
                a, b = pt.get("start"), pt.get("end")
                if not isinstance(a, (int, float)):
                    continue
                if a > cursor:
                    segs.append({"kind": "model", "round": round_no, "text_start": True,
                                 "start_ms": cursor - t0, "end_ms": a - t0,
                                 "ms": round(a - cursor, 1)})
                cursor = max(cursor, a)
                if isinstance(b, (int, float)):
                    segs.append({"kind": "stream", "round": round_no,
                                 "chars": len(p.get("text") or ""),
                                 "start_ms": a - t0, "end_ms": b - t0,
                                 "ms": round(b - a, 1)})
                    cursor = max(cursor, b)
    def _sum(kind):
        return round(sum(s["ms"] for s in segs if s["kind"] == kind and s["ms"]), 1)
    totals = {"model_ms": _sum("model"), "tool_ms": _sum("tool"),
              "stream_ms": _sum("stream")}
    # 社区版这边没有「最后一个 text → 运行结束」这样一个可观测事件
    # （探针是阻塞 POST，POST 返回就算结束）。所以 finish 一段**不写**，
    # 而不是拿 0 顶上 —— 拿 0 顶会让「HCA 的收尾开销比社区版多」这个结论
    # 看起来像是量出来的，其实是缺项。
    return {"segments": segs, "totals": totals}


def summarize(messages, wall_ms):
    calls, texts, steps, part_types = [], [], 0, {}
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for p in m.get("parts") or []:
            t = p.get("type")
            part_types[t] = part_types.get(t, 0) + 1
            if t == "text":
                texts.append(p.get("text") or "")
            elif t in ("step-start", "step_start"):
                steps += 1
            elif t == "tool":
                st = p.get("state") or {}
                tm = st.get("time") or {}
                dur = None
                if isinstance(tm.get("start"), (int, float)) and isinstance(tm.get("end"), (int, float)):
                    dur = round(tm["end"] - tm["start"])
                out = st.get("output") or ""
                if not isinstance(out, str):
                    out = json.dumps(out, ensure_ascii=False)
                calls.append({"seq": len(calls) + 1, "tool": p.get("tool"),
                              "arguments": st.get("input"),
                              "success": (st.get("status") == "completed"),
                              "status": st.get("status"),
                              "duration_ms": dur, "output_len": len(out),
                              "output_head": out[:600]})
    text = "\n".join(t for t in texts if t)
    return {"wall_ms": round(wall_ms), "rounds": steps or None,
            "tool_calls_stat": len(calls), "calls": calls,
            "text": text, "text_len": len(text), "part_types": part_types,
            "waterfall": waterfall(messages),
            "errors": [m.get("error") for m in messages if m.get("error")]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--message", required=True)
    ap.add_argument("--account", type=Path, required=True,
                    help="deploy/eval/seed_eval_account.py 写的 eval-account.json")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--timeout", type=float, default=600.0)
    args = ap.parse_args(argv)

    acct = json.loads(args.account.read_text(encoding="utf-8"))
    token = acct.get("token") or ""
    args.out.mkdir(parents=True, exist_ok=True)

    st, sess = call("POST", "/session", token, {"title": f"eval {args.id}"}, timeout=60)
    if st != 200 or not sess.get("id"):
        rec = {"id": args.id, "side": "opencode",
               "error": f"POST /session HTTP {st}: {json.dumps(sess, ensure_ascii=False)[:400]}"}
        (args.out / f"{args.id}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(rec, ensure_ascii=False)); return 1
    sid = sess["id"]

    claim_status, claim_body = claim_session(acct.get("api") or "", token, sid,
                                             f"eval {args.id}")
    if claim_status != 200:
        print(f"[eval] ⚠ 会话归属登记失败 HTTP {claim_status}: {claim_body} —— "
              f"MCP 会拿不到用户身份", file=sys.stderr)

    q0 = quota_used()
    t0 = time.time()
    st, resp = call("POST", f"/session/{sid}/message", token,
                    {"parts": [{"type": "text", "text": args.message}]},
                    timeout=args.timeout)
    wall = (time.time() - t0) * 1000
    q1 = quota_used()

    st2, raw = call("GET", f"/session/{sid}/message", token, None, timeout=120)
    messages = flatten(raw) if st2 == 200 else []
    (args.out / f"{args.id}.raw.json").write_text(
        json.dumps({"post_status": st, "post_response": resp,
                    "get_status": st2, "messages": raw},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    rec = {"id": args.id, "side": "opencode", "message": args.message,
           "session_id": sid, "post_status": st, "finished": st == 200,
           "claim_status": claim_status, "claim_body": claim_body,
           "quota_used_before": q0, "quota_used_after": q1,
           "quota_delta": (q1 - q0) if isinstance(q0, int) and isinstance(q1, int) else None,
           "permissions": []}
    rec.update(summarize(messages, wall))
    if st != 200:
        rec["error"] = f"POST message HTTP {st}: {json.dumps(resp, ensure_ascii=False)[:600]}"
    (args.out / f"{args.id}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: rec.get(k) for k in
                      ("id", "side", "finished", "wall_ms", "rounds",
                       "tool_calls_stat", "text_len", "quota_delta")},
                     ensure_ascii=False))
    return 0 if st == 200 else 2


if __name__ == "__main__":
    raise SystemExit(main())
