#!/usr/bin/env python3
"""给 A/B 评测建一个账号，并往 api 里播种**两边共用**的持仓与论点。

为什么要播种：评测的第二类问题是"持仓论点复核"。两边必须看到**同一份持仓、
同一份论点**，否则比的是数据不是 agent。opencode 侧从 api 取（`portfolio_*` /
`watchlist_*` 这几个 MCP 是薄代理），HCA 侧走同一个 api（`deploy/eval/
docker-compose.hca-api.yml` 把 daemon 接到了基线那套 api 上），另外还在工作区里
铺一份等价的 `holdings/` 与 `theses/` 文件 —— 那是 HCA 的工作区形态，由
`tools/eval/seed_workspace.py` 负责，内容与这里播种的**逐字段对应**。

**只写数据，不编数据**：下面这三只票的持仓数量、成本价、买入日期是这次评测
自己设定的**测试账本**（评测就需要一个已知的持仓），不是从哪抄来的行情；
行情与财务全部由两边各自的工具实时去取。种子内容集中在 SEED 里，报告会原样引用。

输出：`<secrets>/eval-account.json`（600），给 tools/eval/ 的跑分脚本读。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

EMAIL = "hca-eval@example.invalid"
DISPLAY = "HCA 评测账号"

# 评测用的测试账本。三只票覆盖三种情况：大白马 / 周期股 / 成长股。
SEED = [
    {"code": "600519", "name": "贵州茅台", "market": "A", "exchange": "SH",
     "shares": 100, "cost_price": 1580.0, "buy_date": "2026-03-16",
     "thesis": "买入理由：① 直营占比提升带动吨价上行；② 系列酒放量补充增速；"
               "③ 预收款（合同负债）维持高位。证伪条件：合同负债连续两季同比下滑，"
               "或批价跌破出厂价。"},
    {"code": "601088", "name": "中国神华", "market": "A", "exchange": "SH",
     "shares": 2000, "cost_price": 38.5, "buy_date": "2026-01-20",
     "thesis": "买入理由：① 长协煤比例高、业绩波动小；② 分红率稳定在 70% 以上；"
               "③ 煤电路港航一体化对冲煤价。证伪条件：分红率下调，"
               "或长协价格机制发生不利调整。"},
    {"code": "300750", "name": "宁德时代", "market": "A", "exchange": "SZ",
     "shares": 300, "cost_price": 205.0, "buy_date": "2026-05-08",
     "thesis": "买入理由：① 储能业务增速快于动力电池；② 海外产能爬坡带来毛利改善；"
               "③ 新技术路线（麒麟/神行）维持份额。证伪条件：单季度毛利率环比下滑"
               "超过 2 个百分点，或国内动力电池份额跌破 40%。"},
]


def req(method: str, url: str, body=None, token: str = "", timeout: float = 60.0):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"_raw": raw[:400]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--secrets", required=True)
    args = ap.parse_args()

    api = args.api.rstrip("/")
    secrets = Path(args.secrets)
    secrets.mkdir(parents=True, exist_ok=True)

    pw_file = secrets / "eval-account-password"
    if not pw_file.is_file() or not pw_file.read_text().strip():
        pw_file.write_text(os.urandom(16).hex(), encoding="utf-8")
        pw_file.chmod(0o600)
    password = pw_file.read_text(encoding="utf-8").strip()

    st, body = req("POST", f"{api}/api/auth/register",
                   {"email": EMAIL, "password": password, "display_name": DISPLAY})
    if st == 409:
        st, body = req("POST", f"{api}/api/auth/login",
                       {"email": EMAIL, "password": password})
    if st != 200:
        print(f"[seed] ✗ 注册/登录失败 HTTP {st}: {body}", file=sys.stderr)
        return 1

    token = body.get("access_token") or body.get("token") or ""
    user = body.get("user") or {}
    user_id = str(user.get("id") or "")
    if not token or not user_id:
        print(f"[seed] ✗ 没拿到 token / user_id：{body}", file=sys.stderr)
        return 1
    print(f"[seed] 账号就绪 user_id={user_id}（口令在 {pw_file}，不打印）")

    for s in SEED:
        st, b = req("POST", f"{api}/api/watchlist",
                    {"code": s["code"], "name": s["name"], "market": s["market"],
                     "exchange": s["exchange"], "asset_type": "stock"}, token,
                    timeout=180)
        print(f"[seed]   加自选 {s['code']} {s['name']} → HTTP {st}")
        if st not in (200, 201):
            print(f"[seed] ✗ 加自选失败：{b}", file=sys.stderr)
            return 1
        st, b = req("PUT", f"{api}/api/watchlist/{s['code']}/thesis",
                    {"thesis_text": s["thesis"], "shares": s["shares"],
                     "cost_price": s["cost_price"], "buy_date": s["buy_date"]}, token)
        print(f"[seed]   写论点+持仓 {s['code']} → HTTP {st}")
        if st != 200:
            print(f"[seed] ✗ 写论点失败：{b}", file=sys.stderr)
            return 1

    st, wl = req("GET", f"{api}/api/watchlist", None, token)
    print(f"[seed] 核对：GET /api/watchlist → HTTP {st}，{len(wl) if isinstance(wl, list) else '?'} 条")

    out = secrets / "eval-account.json"
    out.write_text(json.dumps({
        "email": EMAIL, "user_id": user_id, "token": token,
        "api": api, "seed": SEED,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    out.chmod(0o600)
    print(f"[seed] 已写 {out}（600）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
