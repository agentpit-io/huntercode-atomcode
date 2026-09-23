#!/usr/bin/env python3
"""社区版（opencode 版 1.2.0）的浸泡测试 —— 与 `soak.py` 同口径的对照组（I1）。

    nohup setsid python3 tools/stability/soak_opencode.py --hours 1 --interval 300 \
        --out ~/hca/i1-soak-oc > ~/hca/i1-soak-oc/soak.log 2>&1 &

`docs/对比-opencode版.md` §6 一直写着「长期稳定性：HCA 做了 4 小时浸泡，社区版**未做**」。
这个脚本就是去把那一格填上。**口径刻意与 soak.py 对齐**，这样两份数字能放进同一张表：

  · 同一份 12 题循环（`soak.py` 的 `QUESTIONS`，直接 import，不另抄一份）
  · 同样每 `--interval` 秒一轮，每轮**新建会话**
  · 同样在每轮前后 `docker stats` 采样常驻内存、`free`/`loadavg` 采宿主
  · 同样统计容器日志里的错误行数（同一个正则）
  · 同样：拿不到的写 `null`，不猜

三处**必须不同**，都是两个产品本来就不一样的地方，报告里要写明：

  1. **走的路不同**。HCA 那边是「api 登录 → web BFF → daemon `/live`」；社区版这边是
     「api 登录 → opencode `POST /session/{id}/message`」。社区版的 web 也有一层 BFF，
     但它就是 opencode 官方那层、没有我们写的适配代码 —— 而 HCA 的 BFF 是本项目的产物，
     浸泡它才有意义。两边都绕开各自 web 的静态资源，量的是引擎。
  2. **容器清单不同**（`hca-*` vs `hca-baseline-*`），所以「引擎常驻内存」比的是
     `hca-daemon` 对 `hca-baseline-opencode-1`。
  3. **没有会话落盘那一组采样**：AtomCode 把会话写在 `/data/atomcode/sessions`，
     opencode 的会话在它自己的存储里、结构不同，硬凑一个「等价指标」只会得出
     一个没法解释的数。这一项在报告里写「未测」，不写 0。
"""
from __future__ import annotations

import argparse
import base64
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from soak import QUESTIONS, ERR_RE, host_stats, now_sh, sh  # noqa: E402

SH = timezone(timedelta(hours=8))
CONTAINERS = ["hca-baseline-opencode-1", "hca-baseline-api-1",
              "hca-baseline-postgres-1", "hca-baseline-redis-1",
              "hca-baseline-llm-shim-1"]
ENGINE = "hca-baseline-opencode-1"


def req(method: str, url: str, headers: dict, body=None, timeout: float = 600.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    for k, v in headers.items():
        r.add_header(k, v)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def oc_headers(token: str) -> dict:
    user = os.environ.get("OPENCODE_SERVER_USERNAME", "opencode")
    pw = os.environ.get("OPENCODE_SERVER_PASSWORD", "")
    h = {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}
    if token:
        h["X-Hunter-User-Token"] = token
    return h


def mem_bytes() -> dict:
    out = sh(["docker", "stats", "--no-stream", "--format",
              "{{.Name}}|{{.MemUsage}}|{{.CPUPerc}}"] + CONTAINERS, timeout=90)
    unit = {"B": 1, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3,
            "KB": 1000, "MB": 1000**2, "GB": 1000**3}
    res = {}
    for ln in out.strip().splitlines():
        parts = ln.split("|")
        if len(parts) != 3:
            continue
        name, usage, cpu = parts
        m = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)\s*/", usage)
        res[name] = {"mem_bytes": int(float(m.group(1)) * unit.get(m.group(2).upper(), 0)) if m else None,
                     "mem_raw": usage.strip(), "cpu_pct": cpu.strip()}
    for c in CONTAINERS:
        res.setdefault(c, {"mem_bytes": None, "mem_raw": None, "cpu_pct": None})
    return res


def restart_counts() -> dict:
    res = {}
    for c in CONTAINERS:
        out = sh(["docker", "inspect", c, "--format",
                  "{{.RestartCount}}|{{.State.StartedAt}}|{{.State.Status}}"]).strip()
        p = out.split("|")
        res[c] = {"restarts": int(p[0]) if p and p[0].isdigit() else None,
                  "started_at": p[1] if len(p) > 1 else None,
                  "status": p[2] if len(p) > 2 else None}
    return res


def log_errors(since_iso: str) -> dict:
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
         "mem": mem_bytes(), "host": host_stats(), "restarts": restart_counts(),
         # 会话落盘这一组**故意不给** —— 见文件头第 3 点
         "sessions": None}
    if with_logs:
        s["errors"] = log_errors(since_iso)
    return s


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


def claim_session(api: str, token: str, sid: str, title: str):
    """会话归属登记 —— 不做的话 MCP 拿不到用户身份（eval_opencode.py 同款）。"""
    st, body = req("POST", f"{api}/api/chat/sessions",
                   {"Authorization": "Bearer " + token},
                   {"id": sid, "title": title}, 30)
    return st, body


def turn(oc: str, api: str, token: str, ask: str, idx: int, timeout: float) -> dict:
    h = oc_headers(token)
    t_all = time.time()
    st, sess = req("POST", oc + "/session", h, {"title": f"soak-oc {idx}"}, 60)
    if st != 200 or not isinstance(sess, dict) or not sess.get("id"):
        return {"ok": False, "error": f"建会话 HTTP {st}: {str(sess)[:200]}"}
    sid = sess["id"]
    claim_st, _ = claim_session(api, token, sid, f"soak-oc {idx}")

    t0 = time.time()
    st, resp = req("POST", f"{oc}/session/{sid}/message", h,
                   {"parts": [{"type": "text", "text": ask}]}, timeout)
    wall = round((time.time() - t0) * 1000)

    st2, raw = req("GET", f"{oc}/session/{sid}/message", h, None, 120)
    tools, text_len, errors = [], 0, []
    if st2 == 200 and isinstance(raw, list):
        for item in raw:
            m = item.get("info") if isinstance(item, dict) and "info" in item else item
            parts = (item.get("parts") if isinstance(item, dict) else None) or []
            if isinstance(m, dict) and m.get("error"):
                errors.append(str(m["error"])[:300])
            for p in parts:
                if p.get("type") == "tool":
                    tools.append(p.get("tool"))
                elif p.get("type") == "text":
                    text_len += len(p.get("text") or "")
    return {"ok": st == 200, "session_id": sid, "claim_status": claim_st,
            "post_status": st, "wall_ms": wall,
            "total_ms": round((time.time() - t_all) * 1000),
            "tools": tools, "tool_calls": len(tools), "text_len": text_len,
            "turn_errors": errors,
            "error": None if st == 200 else f"HTTP {st}: {json.dumps(resp, ensure_ascii=False)[:300]}"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=1.0)
    ap.add_argument("--interval", type=float, default=300.0)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--opencode", default=os.environ.get("HCA_OPENCODE_URL",
                                                         "http://127.0.0.1:13931"))
    ap.add_argument("--api", default=os.environ.get("HCA_BASELINE_API",
                                                    "http://127.0.0.1:18200"))
    ap.add_argument("--account", type=Path,
                    default=Path(os.environ.get("HCA_SECRETS_DIR",
                                                str(Path.home() / "hca" / "secrets")))
                    / "eval-account.json")
    ap.add_argument("--secrets", type=Path,
                    default=Path(os.environ.get("HCA_SECRETS_DIR",
                                                str(Path.home() / "hca" / "secrets"))))
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    acct = json.loads(args.account.read_text(encoding="utf-8"))
    token = acct.get("token") or ""
    api = acct.get("api") or args.api
    if not token:
        print("✗ eval-account.json 里没有 token", file=sys.stderr)
        return 2

    since_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    started = time.time()
    deadline = started + args.hours * 3600
    rounds_f = args.out / "rounds.jsonl"
    samples_f = args.out / "samples.jsonl"
    errors_f = args.out / "errors.jsonl"

    def emit(path: Path, obj):
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")

    s0 = sample("start", since_iso, True)
    emit(samples_f, s0)
    print(f"[soak-oc] 开始 {now_sh()}，计划 {args.hours} 小时、每 {args.interval:.0f} 秒一轮",
          flush=True)
    print(f"[soak-oc] 引擎容器 {ENGINE} 起始常驻 "
          f"{(s0['mem'].get(ENGINE) or {}).get('mem_raw')}", flush=True)

    n = 0
    ok_n = 0
    while time.time() < deadline:
        n += 1
        qid, kind, ask = QUESTIONS[(n - 1) % len(QUESTIONS)]
        t0 = time.time()
        pre = sample(f"r{n:03d}-pre", since_iso, False)
        q_before = quota(args.secrets)
        r = turn(args.opencode, api, token, ask, n, args.timeout)
        q_after = quota(args.secrets)
        post = sample(f"r{n:03d}-post", since_iso, False)
        rec = {"round": n, "ts": now_sh(), "qid": qid, "kind": kind, "ask": ask,
               "quota_used_before": q_before, "quota_used_after": q_after,
               "quota_delta": (q_after - q_before)
                              if isinstance(q_before, int) and isinstance(q_after, int) else None,
               "engine_mem_before": (pre["mem"].get(ENGINE) or {}).get("mem_bytes"),
               "engine_mem_after": (post["mem"].get(ENGINE) or {}).get("mem_bytes"),
               **r}
        emit(rounds_f, rec)
        emit(samples_f, pre)
        emit(samples_f, post)
        if r.get("ok"):
            ok_n += 1
        else:
            emit(errors_f, {"round": n, "ts": now_sh(), "error": r.get("error"),
                            "turn_errors": r.get("turn_errors")})
        print(f"[soak-oc] r{n:03d} {qid} ok={r.get('ok')} "
              f"{r.get('wall_ms')}ms tools={r.get('tool_calls')} "
              f"文本 {r.get('text_len')} 字 配额差 {rec['quota_delta']} "
              f"引擎常驻 {(post['mem'].get(ENGINE) or {}).get('mem_raw')}", flush=True)
        rest = args.interval - (time.time() - t0)
        if rest > 0 and time.time() + rest < deadline + args.interval:
            time.sleep(rest)

    s1 = sample("end", since_iso, True)
    emit(samples_f, s1)
    summary = {
        "started_at": datetime.fromtimestamp(started, SH).strftime("%Y-%m-%d %H:%M:%S"),
        "ended_at": now_sh(),
        "hours_planned": args.hours,
        "hours_actual": round((time.time() - started) / 3600, 2),
        "rounds": n, "rounds_ok": ok_n, "rounds_failed": n - ok_n,
        "engine_container": ENGINE,
        "engine_mem_start": (s0["mem"].get(ENGINE) or {}).get("mem_bytes"),
        "engine_mem_end": (s1["mem"].get(ENGINE) or {}).get("mem_bytes"),
        "error_lines_start": (s0.get("errors") or {}).get("counts"),
        "error_lines_end": (s1.get("errors") or {}).get("counts"),
        "restarts_end": s1.get("restarts"),
        "note": "会话落盘指标未测（两边存储结构不同，硬凑等价指标只会得出无法解释的数）",
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[soak-oc] " + json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
