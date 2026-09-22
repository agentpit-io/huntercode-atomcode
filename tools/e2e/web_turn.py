#!/usr/bin/env python3
"""走**真实用户路径**跑一个/一批对话回合：登录 → 建会话 → 发消息 → 读历史。

为什么不用 `tools/probe/skill_e2e.py`（M1 那个直连 daemon 的探针）：
web 服务起来之后，BFF 的 live-hub **一直挂着 daemon 的 `/live`**（M3 设计：
一条上游流扇出给多个浏览器）。探针再去开一条 `/live` 就拿不到 snapshot 帧
（实测「30 秒内没收到 /live 的 snapshot 帧」）。而且从 M3 起，真实用户走的是
web → BFF → daemon 这条路，直连 daemon 反而测不到 BFF。

    python3 tools/e2e/web_turn.py --web http://127.0.0.1:3200 --api http://127.0.0.1:8200 \
        --secrets ~/hca/secrets --out ~/hca/m4-skills --cases skills

`--cases skills` = 6 个技能各一题（问法取自各技能的 hunter.prompt_tpl，
测的是「用户照着前端提示语问，模型会不会走到这个技能」）。
`--ask "……"` = 只跑一题。

每题落一份 JSON：真实耗时、走了哪些工具、是否触发技能、正文长度、
网关配额差值（真实计量）。**拿不到就写 null，不猜。**
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SKILL_CASES = [
    ("s1-deep_analysis", "帮我写一份 600519 贵州茅台的深度投研报告"),
    ("s2-investor_panel", "看看 66 位大佬对 000001 平安银行的投票结果"),
    ("s3-lhb_analyzer", "分析 002594 比亚迪的龙虎榜，看看是哪家游资在买"),
    ("s4-risk_profile", "我风险偏保守 · 现金还有 5 万 · 单票别超过 20%"),
    ("s5-trap_detector", "帮我测一下 300750 宁德时代是不是杀猪盘"),
    ("s6-uzi", "对 600036 招商银行进行 UZI 全方位深度扫描"),
]


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


def judge_turn(http: int, stop_reason, text_len: int, tool_count: int):
    """这一轮到底算不算成功 → (ok, 不算成功的原因)。

    **不能只看 HTTP 200。** M5 实测：Ollama 通道接不上时，`POST /message` 照样 200、
    终态照常收到，而 `stop_reason=provider_error`、正文 0 字，
    脚本打出「1 题，成功 1，失败 0」。这个脚本还被 `docs/stability-report.md`
    的运维建议推荐为**每日探活** —— 模型通道整个断了它也报绿，那条建议就等于没有。

    三条都要：
      1. HTTP 200；
      2. `stop_reason` 不是错误态；
      3. 这一轮**真有产出** —— 正文非空**或**调到过工具
         （只调工具不写正文在多轮任务里是正常的，不能算失败）。
    """
    if http != 200:
        return False, f"HTTP {http}"
    sr = str(stop_reason or "")
    if sr.endswith("error") or sr in ("error", "failed"):
        return False, f"stop_reason={stop_reason}"
    if text_len <= 0 and tool_count <= 0:
        return False, "这一轮既没有正文也没有工具调用"
    return True, None


def run_case(web: str, token: str, name: str, ask: str, secrets: Path, out: Path, timeout: float):
    st, created = req("POST", f"{web}/api/opencode/session", token, {"title": f"M4 回归 {name}"}, 60)
    if st != 200 or not isinstance(created, dict) or not created.get("id"):
        return {"case": name, "ok": False, "error": f"建会话失败 HTTP {st}：{str(created)[:200]}"}
    sid = created["id"]

    q0 = quota(secrets)
    t0 = time.time()
    st, res = req("POST", f"{web}/api/opencode/session/{sid}/message", token,
                  {"parts": [{"type": "text", "text": ask}]}, timeout)
    wall = time.time() - t0
    q1 = quota(secrets)

    st2, hist = req("GET", f"{web}/api/opencode/session/{sid}/message", token, None, 60)
    tools, skills, text_len, text_head = [], [], 0, ""
    if st2 == 200 and isinstance(hist, list):
        for m in hist:
            for part in (m.get("parts") or []):
                if part.get("type") == "tool":
                    nm = (part.get("tool") or part.get("name") or "")
                    if nm:
                        tools.append(nm)
                    if nm in ("use_skill", "skills") or "skill" in nm:
                        args = part.get("state", {}).get("input") or part.get("input") or {}
                        skills.append(str(args.get("name") or args.get("skill") or nm))
                elif part.get("type") == "text" and m.get("role") == "assistant":
                    t = str(part.get("text") or "")
                    text_len += len(t)
                    if not text_head:
                        text_head = t[:300]
    ok, why = judge_turn(st, (res or {}).get("stop_reason") if isinstance(res, dict) else None,
                         text_len, len(tools))
    stop_reason = (res or {}).get("stop_reason") if isinstance(res, dict) else None
    bad_stop = why is not None and str(why).startswith("stop_reason=")

    rec = {
        "case": name, "ask": ask, "session_id": sid,
        "http": st, "stop_reason": stop_reason,
        "ok": ok, "not_ok_because": why,
        "wall_seconds": round(wall, 1),
        "tools": tools, "tool_count": len(tools),
        "skills_used": sorted(set(skills)),
        "text_len": text_len, "text_head": text_head,
        "quota_used_before": q0, "quota_used_after": q1,
        "quota_delta": (q1 - q0) if isinstance(q0, int) and isinstance(q1, int) else None,
        "error": None if st == 200 else str(res)[:300],
        "response_head": str(res)[:300] if bad_stop else None,
    }
    (out / f"{name}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--web", default="http://127.0.0.1:3200")
    ap.add_argument("--api", default="http://127.0.0.1:8200")
    ap.add_argument("--secrets", default=f"{os.environ['HOME']}/hca/secrets")
    ap.add_argument("--out", default=f"{os.environ['HOME']}/hca/m4-web")
    ap.add_argument("--cases", default="")
    ap.add_argument("--ask", default="")
    ap.add_argument("--name", default="ask")
    ap.add_argument("--timeout", type=float, default=600.0)
    a = ap.parse_args()

    secrets, out = Path(a.secrets), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    email, pw = admin_creds(secrets)
    st, tok = req("POST", f"{a.api}/api/auth/login", "", {"email": email, "password": pw}, 30)
    if st != 200 or not isinstance(tok, dict) or not tok.get("access_token"):
        print(f"登录失败 HTTP {st}：{str(tok)[:200]}")
        return 2
    token = tok["access_token"]
    print(f"登录成功（{email}）")

    cases = SKILL_CASES if a.cases == "skills" else [(a.name, a.ask)]
    if not cases[0][1]:
        print("没给题目（--cases skills 或 --ask）")
        return 2

    recs = []
    for name, ask in cases:
        print(f"\n── {name} ──────────────────────────────\n问：{ask}")
        rec = run_case(a.web, token, name, ask, secrets, out, a.timeout)
        recs.append(rec)
        if not rec.get("ok"):
            # 失败也要把真实情况打全 —— 原先只打 rec['error']，
            # 而 provider_error 那一类 HTTP 是 200、error 恰恰是 None，于是屏幕上只有一行空的 ✗。
            print(f"  ✗ {rec.get('not_ok_because') or rec.get('error') or '未知'}"
                  f"（HTTP {rec.get('http')} · stop_reason={rec.get('stop_reason')} · "
                  f"正文 {rec.get('text_len')} 字 · 工具 {rec.get('tool_count')} 次 · "
                  f"耗时 {rec.get('wall_seconds')}s）")
            if rec.get("response_head"):
                print(f"    终态原文：{rec['response_head'][:200]}")
            continue
        print(f"  技能：{rec['skills_used'] or '（没走技能）'}")
        print(f"  工具：{' '.join(rec['tools']) or '（一个没调）'}")
        print(f"  耗时 {rec['wall_seconds']}s · 正文 {rec['text_len']} 字 · "
              f"stop_reason={rec['stop_reason']} · 配额差值 "
              f"{rec['quota_delta'] if rec['quota_delta'] is not None else '—'}")
        print(f"  正文开头：{(rec['text_head'] or '')[:120]}")
    (out / "summary.json").write_text(json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in recs if r.get("ok"))
    print(f"\n{len(recs)} 题，成功 {ok}，失败 {len(recs) - ok}；明细在 {out}")
    return 0 if ok == len(recs) else 1


if __name__ == "__main__":
    sys.exit(main())
