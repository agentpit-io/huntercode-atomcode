#!/usr/bin/env python3
"""M5 稳定性浸泡测试：在测试机上连续 N 小时自动对话，采样内存 / 会话文件 / 错误数。

**在测试机宿主上跑**（要用 docker CLI 采样），走的是真实用户路径
（api 登录 → web BFF 建会话 → 发消息 → 读历史），与 `tools/e2e/web_turn.py` 同一条路。
故意不直连 daemon：BFF 的 live-hub 一直挂着 daemon 的 `/live`，另开一条会抢不到
snapshot 帧（M3 §4.1），而且直连测不到 BFF。

    nohup setsid python3 tools/stability/soak.py --hours 4 --interval 300 \
        --out ~/hca/m5-soak > ~/hca/m5-soak/soak.log 2>&1 &

每轮落一份 JSON（问题、工具、技能、耗时、配额差值、前后采样），
外加 rounds.jsonl / samples.jsonl / errors.jsonl / summary.json。
**所有数字都来自真实调用；取不到就写 null，不猜。**
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

SH = timezone(timedelta(hours=8))
CONTAINERS = ["hca-daemon", "hca-web", "hca-api", "hca-postgres", "hca-redis", "hca-llm-shim"]

# 12 题循环：6 个技能 + 6 类 MCP 直用。故意混长短题，模拟真实投研节奏。
QUESTIONS = [
    ("k1-deep_analysis", "skill", "帮我写一份 600519 贵州茅台的深度投研报告"),
    ("m1-quickview",     "mcp",   "600036 招商银行现在什么价？涨跌幅多少？"),
    ("k2-investor_panel","skill", "看看 66 位大佬对 000001 平安银行的投票结果"),
    ("m2-portfolio",     "mcp",   "看一下我的持仓，现在整体盈亏怎么样？"),
    ("k3-lhb_analyzer",  "skill", "分析 002594 比亚迪的龙虎榜，看看是哪家游资在买"),
    ("m3-akshare",       "mcp",   "用 akshare 取 601398 工商银行最近 10 个交易日的收盘价（新浪源），列个表"),
    ("k4-risk_profile",  "skill", "我风险偏保守 · 现金还有 5 万 · 单票别超过 20%"),
    ("m4-screener",      "mcp",   "帮我筛一下市盈率低于 15 倍的 A 股，前 10 只就行"),
    ("k5-trap_detector", "skill", "帮我测一下 300750 宁德时代是不是杀猪盘"),
    ("m5-news",          "mcp",   "000858 五粮液最近有什么消息？挑三条重要的"),
    ("k6-uzi",           "skill", "对 600036 招商银行进行 UZI 全方位深度扫描"),
    ("m6-watchlist",     "mcp",   "我的自选股都有哪些？各自今天涨跌如何？"),
]

ERR_RE = re.compile(r"(?i)\b(error|panic|fatal|exception|traceback|unhandled)\b")


def now_sh() -> str:
    return datetime.now(SH).strftime("%Y-%m-%d %H:%M:%S")


def sh(args: list[str], timeout: float = 60.0) -> str:
    """子进程一律用参数数组，不拼 shell 字符串（总控规则 红线 3）。"""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.stdout
    except Exception:  # noqa: BLE001
        return ""


def req(method: str, url: str, token: str = "", body=None, timeout: float = 60.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


# ── 采样 ───────────────────────────────────────────────────────────────

def mem_bytes() -> dict:
    """docker stats 的 MemUsage，解析成字节。取不到写 None。"""
    out = sh(["docker", "stats", "--no-stream", "--format",
              "{{.Name}}|{{.MemUsage}}|{{.CPUPerc}}"] + CONTAINERS, timeout=90)
    res = {}
    unit = {"B": 1, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "KB": 1000, "MB": 1000**2, "GB": 1000**3}
    for ln in out.strip().splitlines():
        parts = ln.split("|")
        if len(parts) != 3:
            continue
        name, usage, cpu = parts
        m = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)\s*/", usage)
        res[name] = {
            "mem_bytes": int(float(m.group(1)) * unit.get(m.group(2).upper(), 0)) if m else None,
            "mem_raw": usage.strip(),
            "cpu_pct": cpu.strip(),
        }
    for c in CONTAINERS:
        res.setdefault(c, {"mem_bytes": None, "mem_raw": None, "cpu_pct": None})
    return res


def restart_counts() -> dict:
    res = {}
    for c in CONTAINERS:
        out = sh(["docker", "inspect", c, "--format", "{{.RestartCount}}|{{.State.StartedAt}}|{{.State.Status}}"]).strip()
        p = out.split("|")
        res[c] = {"restarts": int(p[0]) if p and p[0].isdigit() else None,
                  "started_at": p[1] if len(p) > 1 else None,
                  "status": p[2] if len(p) > 2 else None}
    return res


def session_files() -> dict:
    """daemon 的会话落盘：/data/atomcode/sessions（文件数 + 字节数）与 logs、工作区审计日志。"""
    script = (
        "printf '%s ' $(find /data/atomcode/sessions -type f 2>/dev/null | wc -l); "
        "printf '%s ' $(du -sb /data/atomcode/sessions 2>/dev/null | cut -f1); "
        "printf '%s ' $(du -sb /data/atomcode/logs 2>/dev/null | cut -f1); "
        "printf '%s ' $(du -sb /data/atomcode 2>/dev/null | cut -f1); "
        "printf '%s ' $(stat -c %s /workspace/.atomcode/audit.jsonl 2>/dev/null || echo 0); "
        "printf '%s\\n' $(stat -c %s /workspace/.atomcode/guard.jsonl 2>/dev/null || echo 0)"
    )
    out = sh(["docker", "exec", "hca-daemon", "sh", "-c", script]).strip().split()
    keys = ["session_file_count", "sessions_bytes", "logs_bytes", "atomcode_home_bytes",
            "audit_jsonl_bytes", "guard_jsonl_bytes"]
    res = {}
    for i, k in enumerate(keys):
        try:
            res[k] = int(out[i])
        except (IndexError, ValueError):
            res[k] = None
    return res


def host_stats() -> dict:
    out = sh(["free", "-b"])
    mem = {}
    for ln in out.splitlines():
        if ln.startswith("Mem:"):
            f = ln.split()
            mem = {"total": int(f[1]), "used": int(f[2]), "available": int(f[6])}
    la = None
    try:
        la = os.getloadavg()
    except OSError:
        pass
    df = sh(["df", "-B1", "--output=avail", "/"]).strip().splitlines()
    return {"mem": mem or None,
            "loadavg": [round(x, 2) for x in la] if la else None,
            "disk_avail_bytes": int(df[-1]) if len(df) > 1 and df[-1].strip().isdigit() else None}


def log_errors(since_iso: str) -> dict:
    """自浸泡开始以来各容器日志里命中错误词的行数 + 去重样本。"""
    counts, samples = {}, {}
    for c in CONTAINERS:
        try:
            p = subprocess.run(["docker", "logs", "--since", since_iso, c],
                               capture_output=True, text=True, timeout=120)
            lines = (p.stdout + p.stderr).splitlines()
        except Exception:  # noqa: BLE001
            counts[c] = None
            continue
        hit = [ln.strip() for ln in lines if ERR_RE.search(ln)]
        counts[c] = len(hit)
        seen = []
        for ln in hit:
            norm = re.sub(r"\d", "#", ln)[:180]
            if norm not in seen:
                seen.append(norm)
                samples.setdefault(c, []).append(ln[:400])
            if len(seen) >= 12:
                break
    return {"counts": counts, "samples": samples}


def sample(tag: str, since_iso: str, with_logs: bool) -> dict:
    s = {"tag": tag, "ts": now_sh(), "epoch": int(time.time()),
         "mem": mem_bytes(), "sessions": session_files(), "host": host_stats(),
         "restarts": restart_counts()}
    if with_logs:
        s["errors"] = log_errors(since_iso)
    return s


# ── 对话 ───────────────────────────────────────────────────────────────

def admin_creds(secrets: Path):
    txt = (secrets / "admin.txt").read_text(encoding="utf-8")
    def get(k):
        for ln in txt.splitlines():
            if ln.startswith(k + ":"):
                return ln.split(":", 1)[1].strip()
        return ""
    e, p = get("email"), get("password")
    if not e or not p:
        raise SystemExit("admin.txt 里没读到 email/password")
    return e, p


def quota(secrets: Path):
    f = secrets / "llm-test-key"
    if not f.is_file():
        return None
    key = f.read_text(encoding="utf-8").strip()
    url = os.environ.get("HCA_QUOTA_URL") or "https://hunter.agentpit.io/api/saas/llm/quota"
    try:
        r = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(r, timeout=20) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        return d.get("used_today") if isinstance(d.get("used_today"), int) else None
    except Exception:  # noqa: BLE001
        return None


def login(api: str, secrets: Path):
    email, pw = admin_creds(secrets)
    st, tok = req("POST", f"{api}/api/auth/login", "", {"email": email, "password": pw}, 30)
    if st != 200 or not isinstance(tok, dict) or not tok.get("access_token"):
        return None, f"登录失败 HTTP {st}：{str(tok)[:200]}"
    return tok["access_token"], None


def turn(web: str, token: str, sid: str, ask: str, timeout: float, seen_tools: int = 0) -> dict:
    """发一轮并读回历史。

    `seen_tools` = 这个会话在**本轮之前**已经累计了多少个 tool part。
    `GET /session/{id}/message` 回的是整条会话的历史，同一会话的第 2、3 轮
    直接数就会把前几轮的工具也算进来（第一版就是这么错的，r002 明明只问了个
    行情却显示「工具 18 · 技能 deep_analysis」——那是第 1 轮留下的）。
    所以这里同时记 `tools`（本轮新增）与 `tools_cumulative`（整条会话至今）。
    """
    t0 = time.time()
    st, res = req("POST", f"{web}/api/opencode/session/{sid}/message", token,
                  {"parts": [{"type": "text", "text": ask}]}, timeout)
    wall = time.time() - t0
    st2, hist = req("GET", f"{web}/api/opencode/session/{sid}/message", token, None, 60)
    all_tools, all_skills, text_len, text_head = [], [], 0, ""
    if st2 == 200 and isinstance(hist, list):
        last = hist[-1] if hist else {}
        for part in (last.get("parts") or []) if last.get("role") == "assistant" else []:
            if part.get("type") == "text":
                t = str(part.get("text") or "")
                text_len += len(t)
                if not text_head:
                    text_head = t[:200]
        for m in hist:
            for part in (m.get("parts") or []):
                if part.get("type") == "tool":
                    nm = part.get("tool") or part.get("name") or ""
                    if not nm:
                        continue
                    args = part.get("state", {}).get("input") or part.get("input") or {}
                    all_tools.append(nm)
                    all_skills.append(str(args.get("name") or args.get("skill") or nm)
                                      if "skill" in nm else "")
    tools = all_tools[seen_tools:]
    skills = sorted({x for x in all_skills[seen_tools:] if x})
    return {"http": st, "ok": st == 200, "wall_seconds": round(wall, 1),
            "stop_reason": res.get("stop_reason") if isinstance(res, dict) else None,
            "tools": tools, "tool_count": len(tools), "skills_used": skills,
            "tools_cumulative": len(all_tools),
            "skills_cumulative": sorted({x for x in all_skills if x}),
            "text_len": text_len, "text_head": text_head,
            "error": None if st == 200 else str(res)[:300]}


def mcp_status(port: str) -> dict:
    """daemon 只绑容器内回环（待办池 P1-9），BFF 也没开放这个接口（实测
    `/api/opencode/mcp/status` 回 `该接口未开放`），所以走 `docker exec`。
    注意它只说明 server 连上了，**不等于模型手里有工具**（P0-10 / P0-13）。"""
    out = sh(["docker", "exec", "hca-daemon", "sh", "-c",
              f'curl -fsS -m 10 -H "Authorization: Bearer $(cat /run/hca/daemon-token)" '
              f'http://127.0.0.1:{port}/mcp/status'], timeout=40)
    try:
        d = json.loads(out)
    except Exception:  # noqa: BLE001
        return {"connected": None, "total": None, "tool_count": None}
    servers = d.get("servers") or []
    conn = [x for x in servers if isinstance(x, dict) and x.get("status") == "connected"]
    return {"connected": len(conn), "total": len(servers),
            "tool_count": sum(x.get("tool_count") or 0 for x in servers),
            "not_connected": [x.get("name") for x in servers if x.get("status") != "connected"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--web", default="http://127.0.0.1:3200")
    ap.add_argument("--api", default="http://127.0.0.1:8200")
    ap.add_argument("--secrets", default=f"{os.environ['HOME']}/hca/secrets")
    ap.add_argument("--out", default=f"{os.environ['HOME']}/hca/m5-soak")
    ap.add_argument("--hours", type=float, default=4.0)
    ap.add_argument("--interval", type=float, default=300.0, help="每题间隔（秒），从上一题**开始**计时")
    ap.add_argument("--turns-per-session", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--daemon-port", default=os.environ.get("HCA_DAEMON_PORT", "13456"))
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    secrets = Path(a.secrets)

    start = time.time()
    # docker logs --since 要 UTC RFC3339
    since_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    deadline = start + a.hours * 3600

    token, err = login(a.api, secrets)
    if err:
        print(err, flush=True)
        return 2
    print(f"[{now_sh()}] 登录成功；浸泡 {a.hours}h，每 {a.interval:.0f}s 一题，"
          f"每 {a.turns_per_session} 轮换一个会话", flush=True)

    base = sample("baseline", since_iso, with_logs=True)
    (out / "baseline.json").write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
    q_start = quota(secrets)

    rounds_f = (out / "rounds.jsonl").open("a", encoding="utf-8")
    samples_f = (out / "samples.jsonl").open("a", encoding="utf-8")
    samples_f.write(json.dumps(base, ensure_ascii=False) + "\n")
    samples_f.flush()

    rounds, sid, sess_turn, n, seen_tools = [], None, 0, 0, 0
    while time.time() < deadline:
        n += 1
        slot_t0 = time.time()
        name, kind, ask = QUESTIONS[(n - 1) % len(QUESTIONS)]
        rid = f"r{n:03d}-{name}"

        # token 过期就重登（长跑必需）
        stm, _ = req("GET", f"{a.api}/api/auth/me", token, None, 20)
        if stm in (401, 403):
            token, err = login(a.api, secrets)
            if err:
                print(f"[{now_sh()}] {rid} 重登失败：{err}", flush=True)
                token = ""

        if sid is None or sess_turn >= a.turns_per_session:
            st, created = req("POST", f"{a.web}/api/opencode/session", token,
                              {"title": f"M5 浸泡 {rid}"}, 60)
            sid = created.get("id") if st == 200 and isinstance(created, dict) else None
            sess_turn, seen_tools = 0, 0
        sess_turn += 1

        pre = sample(f"{rid}:pre", since_iso, with_logs=False)
        mcp = mcp_status(a.daemon_port)
        q0 = quota(secrets)
        if sid:
            t = turn(a.web, token, sid, ask, a.timeout, seen_tools)
            seen_tools = t.get("tools_cumulative", seen_tools)
        else:
            t = {"http": 0, "ok": False, "wall_seconds": 0.0, "stop_reason": None, "tools": [],
                 "tool_count": 0, "skills_used": [], "tools_cumulative": seen_tools,
                 "skills_cumulative": [], "text_len": 0, "text_head": "",
                 "error": "建会话失败"}
        q1 = quota(secrets)
        post = sample(f"{rid}:post", since_iso, with_logs=False)

        rec = {"round": n, "id": rid, "kind": kind, "ask": ask, "session_id": sid,
               "session_turn": sess_turn, "ts": now_sh(), "mcp": mcp,
               "quota_used_before": q0, "quota_used_after": q1,
               "quota_delta": (q1 - q0) if isinstance(q0, int) and isinstance(q1, int) else None,
               **t,
               "pre": pre, "post": post}
        rounds.append(rec)
        rounds_f.write(json.dumps(rec, ensure_ascii=False) + "\n"); rounds_f.flush()
        samples_f.write(json.dumps(pre, ensure_ascii=False) + "\n")
        samples_f.write(json.dumps(post, ensure_ascii=False) + "\n"); samples_f.flush()
        (out / f"{rid}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

        dm = post["mem"].get("hca-daemon", {}).get("mem_bytes")
        print(f"[{now_sh()}] {rid} {'✓' if t['ok'] else '✗'} {t['wall_seconds']}s "
              f"工具{t['tool_count']} 技能{t['skills_used'] or '—'} 正文{t['text_len']}字 "
              f"MCP {mcp['connected']}/{mcp['total']} 配额Δ{rec['quota_delta']} "
              f"daemon内存{(dm/1048576):.0f}MiB" if dm else
              f"[{now_sh()}] {rid} {'✓' if t['ok'] else '✗'} {t['wall_seconds']}s", flush=True)
        if not t["ok"]:
            print(f"    错误：{t['error']}", flush=True)

        sleep_for = a.interval - (time.time() - slot_t0)
        if sleep_for > 0 and time.time() + sleep_for < deadline + a.interval:
            time.sleep(sleep_for)

    fin = sample("final", since_iso, with_logs=True)
    (out / "final.json").write_text(json.dumps(fin, ensure_ascii=False, indent=2), encoding="utf-8")
    samples_f.write(json.dumps(fin, ensure_ascii=False) + "\n")
    rounds_f.close(); samples_f.close()
    q_end = quota(secrets)

    ok = sum(1 for r in rounds if r["ok"])
    summary = {
        "started_at": datetime.fromtimestamp(start, SH).strftime("%Y-%m-%d %H:%M:%S"),
        "ended_at": now_sh(),
        "hours": round((time.time() - start) / 3600, 2),
        "interval_seconds": a.interval,
        "rounds": len(rounds), "ok": ok, "failed": len(rounds) - ok,
        "quota_used_start": q_start, "quota_used_end": q_end,
        "quota_total_delta": (q_end - q_start) if isinstance(q_start, int) and isinstance(q_end, int) else None,
        "baseline": base, "final": fin,
        "failures": [{"id": r["id"], "http": r["http"], "error": r["error"]} for r in rounds if not r["ok"]],
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[{now_sh()}] 浸泡结束：{len(rounds)} 轮，成功 {ok}，失败 {len(rounds)-ok}；结果在 {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
