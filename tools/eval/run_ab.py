#!/usr/bin/env python3
"""A/B 评测调度 —— 在**测试机宿主**上跑，5 题 × 3 次 × 2 边 = 30 次。

    python3 tools/eval/run_ab.py --out docs/eval/raw --repeat 3

## 顺序：交错，且每一轮换先手

    轮1: q1[A,B] q2[A,B] … q5[A,B]
    轮2: q1[B,A] q2[B,A] … q5[B,A]
    轮3: q1[A,B] …

M0 那次 llm-shim 的 A/B 之所以白跑，一个原因就是**顺序没交错**（待办池 P0-6）：
上游状态、缓存、配额在一天里是漂的，一边连跑 8 次再换另一边，测到的差异里
分不清多少是引擎的、多少是时段的。

## 配额闸

每次跑之前查一次网关 `GET /quota`。剩余额度低于 `--min-quota`（默认 30 万）就
**停下并如实记录跑了多少次**，不硬塞一个跑到一半被截断的结果进报告。

## 输出

`<out>/<question>-<side>-r<n>.json`（结构化，含全文）+ 侧特有的原始流
（A 侧 `.sse`、B 侧 `.raw.json`），以及 `<out>/index.json` 汇总。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))
from questions import QUESTIONS  # noqa: E402

DAEMON = os.environ.get("HCA_DAEMON_CONTAINER", "hca-daemon")
IN_CONTAINER_OUT = "/workspace/.eval"


def log(*a):
    print(f"[ab {time.strftime('%H:%M:%S')}]", *a, flush=True)


def quota(key: str):
    url = os.environ.get("HCA_QUOTA_URL") or "https://hunter.agentpit.io/api/saas/llm/quota"
    try:
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        log(f"⚠ 查配额失败：{type(e).__name__}: {e}")
        return None


def refresh_probe():
    """把仓库里当前版本的探针 cp 进容器，覆盖镜像里那份。

    镜像里有一份（`deploy/Dockerfile.daemon` COPY 进 `/opt/hca/tools/`），保证
    「不带仓库也能跑」；但评测期间改探针不该逼着重建镜像 —— 重建一次 daemon 要
    两分多钟，而且会和另一条链路抢那把重负载锁。以仓库里的为准。
    """
    src = HERE / "eval_atomcode.py"
    p = subprocess.run(["docker", "cp", str(src),
                        f"{DAEMON}:/opt/hca/tools/eval_atomcode.py"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        log(f"⚠ 刷新探针失败（用镜像里那份继续）：{p.stderr.strip()}")
    else:
        log(f"探针已刷新：{src} → {DAEMON}:/opt/hca/tools/eval_atomcode.py")


def container_ok(name: str) -> bool:
    p = subprocess.run(
        ["docker", "inspect", "-f",
         "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}", name],
        capture_output=True, text=True)
    return p.returncode == 0 and p.stdout.strip() in ("healthy", "running")


def preflight() -> bool:
    """两套栈都得健康才开跑。

    测试机上另一条链路会清 docker 资源（总控「测试机与 HunterLauncher 链路共用」），
    容器被清掉之后再跑评测，拿到的是一串连接失败 —— 还白烧 token。
    不健康就先按各自的可重复脚本拉起来一次，再确认。
    """
    # ⚠️ daemon 必须**带评测覆盖文件**拉起。直接跑 deploy/up.sh 会用不带覆盖的
    # compose 重建容器，于是 HERMES_API_URL / HUNTER_USER_ID 丢掉、也不再接在
    # hca-eval-net 上 —— 6 个 hunter 系 MCP 会悄无声息地变回"调不通"，
    # 而 /mcp/status 照样 9/9 connected（待办池 P0-8 / P0-10 的老问题）。
    need = {
        "hca-daemon": [
            "docker", "compose", "-p", "hca",
            "-f", str(REPO / "deploy" / "docker-compose.yml"),
            "-f", str(REPO / "deploy" / "eval" / "docker-compose.hca-api.yml"),
            "--env-file", str(REPO / "deploy" / ".env"), "up", "-d", "--wait",
        ],
        "hca-baseline-opencode-1": [
            "bash", str(REPO / "deploy" / "eval" / "up-baseline.sh")],
    }
    for name, heal in need.items():
        if container_ok(name):
            log(f"预检 {name}: ok")
            continue
        log(f"预检 {name}: 不健康，尝试拉起 → {' '.join(heal)}")
        subprocess.run(heal, cwd=str(REPO), timeout=1800)
        if not container_ok(name):
            log(f"✗ {name} 仍不健康，放弃开跑（不烧 token 去撞一堵墙）")
            return False
        log(f"预检 {name}: 已恢复")
    return True


def run_atomcode(case_id: str, message: str, out: Path, timeout: float, permission: str):
    """在 daemon 容器里跑，再把产物 cp 出来。"""
    cmd = ["docker", "exec", DAEMON, "python3", "/opt/hca/tools/eval_atomcode.py",
           "--id", case_id, "--message", message, "--out", IN_CONTAINER_OUT,
           "--timeout", str(timeout), "--permission", permission]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 180)
    for ext in (".json", ".sse"):
        src = f"{DAEMON}:{IN_CONTAINER_OUT}/{case_id}{ext}"
        subprocess.run(["docker", "cp", src, str(out / f"{case_id}{ext}")],
                       capture_output=True, text=True)
    (out / f"{case_id}.exec.log").write_text(
        f"$ {' '.join(cmd)}\n--- rc={p.returncode} ---\n{p.stdout}\n--- stderr ---\n{p.stderr}",
        encoding="utf-8")
    return p.returncode


def run_opencode(case_id: str, message: str, out: Path, timeout: float, account: Path):
    cmd = [sys.executable, str(HERE / "eval_opencode.py"), "--id", case_id,
           "--message", message, "--account", str(account), "--out", str(out),
           "--timeout", str(timeout)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 180)
    (out / f"{case_id}.exec.log").write_text(
        f"$ {' '.join(cmd)}\n--- rc={p.returncode} ---\n{p.stdout}\n--- stderr ---\n{p.stderr}",
        encoding="utf-8")
    return p.returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=REPO / "docs" / "eval" / "raw")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--gap", type=float, default=20.0,
                    help="两次运行之间的间隔秒数。网关限 20 次/分钟，留点余量")
    ap.add_argument("--min-quota", type=int, default=300_000)
    ap.add_argument("--permission", default="deny",
                    help="HCA 侧自动应答 permission_request 的决定")
    ap.add_argument("--account", type=Path,
                    default=Path(os.environ.get("HCA_SECRETS_DIR",
                                                str(Path.home() / "hca" / "secrets")))
                    / "eval-account.json")
    ap.add_argument("--only", default="", help="只跑某道题（题目 id 的子串）")
    ap.add_argument("--sides", default="atomcode,opencode")
    args = ap.parse_args(argv)

    key_file = Path(os.environ.get("HCA_SECRETS_DIR",
                                   str(Path.home() / "hca" / "secrets"))) / "llm-test-key"
    if not key_file.is_file():
        log(f"✗ 缺 {key_file}"); return 2
    key = key_file.read_text(encoding="utf-8").strip()
    os.environ.setdefault("HCA_LLM_API_KEY", key)

    if not args.account.is_file():
        log(f"✗ 缺评测账号文件 {args.account}（先跑 deploy/eval/up-baseline.sh）")
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    if not preflight():
        return 4
    refresh_probe()
    sides = [s.strip() for s in args.sides.split(",") if s.strip()]
    qs = [q for q in QUESTIONS if args.only in q["id"]]

    index_path = args.out / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {"runs": []}
    done = {r["case_id"] for r in index["runs"] if r.get("rc") == 0}

    started = time.time()
    for r in range(1, args.repeat + 1):
        # 每一轮换先手，消掉"谁先跑"的系统性偏差
        order = sides if r % 2 == 1 else list(reversed(sides))
        for q in qs:
            for side in order:
                case_id = f"{q['id']}-{side}-r{r}"
                if case_id in done:
                    log(f"跳过已完成 {case_id}"); continue

                qd = quota(key)
                remaining = (qd or {}).get("remaining")
                if isinstance(remaining, int) and remaining < args.min_quota:
                    log(f"✗ 配额只剩 {remaining}（阈值 {args.min_quota}），停在 {case_id} 之前。"
                        f"重置时间 {(qd or {}).get('reset_at')}")
                    index["stopped_for_quota_at"] = case_id
                    index["quota_at_stop"] = qd
                    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
                    return 3

                log(f"▶ {case_id}（剩余配额 {remaining}）")
                t0 = time.time()
                if side == "atomcode":
                    rc = run_atomcode(case_id, q["text"], args.out, args.timeout,
                                      args.permission)
                else:
                    rc = run_opencode(case_id, q["text"], args.out, args.timeout,
                                      args.account)
                dt = time.time() - t0
                log(f"  rc={rc} 用时 {dt:.1f}s")
                index["runs"] = [x for x in index["runs"] if x["case_id"] != case_id]
                index["runs"].append({"case_id": case_id, "question": q["id"],
                                      "side": side, "repeat": r, "rc": rc,
                                      "elapsed_s": round(dt, 1),
                                      "quota_remaining_before": remaining})
                index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
                time.sleep(args.gap)

    index["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    index["total_elapsed_s"] = round(time.time() - started, 1)
    index["quota_at_end"] = quota(key)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"全部完成，共 {len(index['runs'])} 次，总耗时 {index['total_elapsed_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
