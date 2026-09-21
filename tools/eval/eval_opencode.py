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
        out.append({"role": (info or {}).get("role"), "id": (info or {}).get("id"),
                    "error": (info or {}).get("error"), "parts": parts})
    return out


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
