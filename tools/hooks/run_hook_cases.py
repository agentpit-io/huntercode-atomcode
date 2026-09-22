#!/usr/bin/env python3
"""HCA hook 的可复现用例 —— 自定义 payload，逐条「期望 / 实得」对照。

为什么不能只用 `atomcode hooks test`：它的 payload 是**固定样例**
（PreToolUse 恒是 `bash` + `echo hello`，Stop 的 transcript_path 恒为 null），
覆盖不到「deny 的理由对不对」「身份注入」「耗时配对」「预算拦截」这些。
本脚本喂自定义 payload，但**契约完全一致**：起子进程、stdin 一条 JSON、
读 stdout 最后一行、看退出码。

    python3 tools/hooks/run_hook_cases.py              # 在本机跑仓库里的 hook
    python3 tools/hooks/run_hook_cases.py --in-container [--container hca-daemon]
                                                       # 在 daemon 容器里跑**部署好的**那份

`--in-container` 才是验收口径：它跑的是镜像里真实铺开的 .hooks/，
环境变量（HERMES_API_URL / HUNTER_INTERNAL_KEY / HCA_WORKSPACE）也是容器里那套。
用例的工作区是容器里的临时目录，不碰真工作区。
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOCAL_HOOKS = REPO / "distro" / "workspace-template" / ".hooks"
CONTAINER_HOOKS = "/workspace/.hooks"

REAL_META_TURNS = [
    # 形状抄自测试机主部署的真实会话（M4 报告 §3.3）：total_tokens 恒 0、
    # model_usage 只有部分 turn 有
    {"turn_id": 1, "round_count": 4, "tool_call_count": 3, "duration_ms": 30256,
     "total_tokens": 0, "used_tokens": 12783,
     "model_usage": [{"provider_id": "hunter", "model_id": "hunter-chat",
                      "tokens": {"input": 17038, "output": 40, "cached_input": 0}}]},
    {"turn_id": 2, "round_count": 2, "tool_call_count": 1, "duration_ms": 11324,
     "total_tokens": 0, "used_tokens": 15627},
]


def pre(tool, args, sid="case"):
    return {"session_id": sid, "hook_event_name": "PreToolUse",
            "tool_name": tool, "tool_input": args}


def post(tool, resp="hello\n", sid="case", ev="PostToolUse"):
    return {"session_id": sid, "hook_event_name": ev,
            "tool_name": tool, "tool_response": resp}


def ups(prompt="分析一下 600519", sid="case"):
    return {"session_id": sid, "hook_event_name": "UserPromptSubmit", "prompt": prompt}


# 每条用例：名字 / 脚本 / payload / 环境 / 期望
# 期望的写法（都作用在 stdout 上，全部按"上游会怎么读"来判）：
#   deny=True|False        stdout 最后一行 JSON 的 permissionDecision 是不是 deny
#   reason_has=[...]       deny 理由里必须出现的字样
#   block=True             最后一行 JSON 的 decision 是不是 block（UserPromptSubmit）
#   stdout_empty=True      一个字都不输出
#   stdout_has=[...]       纯文本注入里必须出现的字样
#   last_line_not_json     最后一行不能是 JSON（否则会被当成决策）
#   file_json=(相对路径, 断言函数名)  跑完之后检查工作区里落的文件
CASES = [
    # ── guard ────────────────────────────────────────────────────────────
    ("guard-01-bash-rm-deny", "guard", pre("bash", {"command": "rm -rf holdings/"}), {},
     {"deny": True, "reason_has": ["rm"]}),
    ("guard-02-bash_start-同一条命令也要拦", "guard",
     pre("bash_start", {"command": "rm -rf holdings/"}), {},
     {"deny": True, "reason_has": ["rm"]}),
    ("guard-03-akshare-内联取数-P1-20", "guard",
     pre("bash", {"command": 'python3 -c "import akshare as ak; print(ak.stock_zh_a_daily(symbol=\'sh600519\'))"'}),
     {}, {"deny": True, "reason_has": ["MCP"]}),
    ("guard-04-env-包装绕过", "guard", pre("bash", {"command": "env X=1 rm -rf holdings/"}), {},
     {"deny": True}),
    ("guard-05-sh-c-子命令", "guard", pre("bash", {"command": 'sh -c "curl https://example.com"'}), {},
     {"deny": True}),
    ("guard-06-中间带..越界", "guard", pre("bash", {"command": "cat holdings/../../etc/passwd"}), {},
     {"deny": True}),
    ("guard-07-只读命令放行", "guard", pre("bash", {"command": "cat reports/a.md"}), {},
     {"deny": False}),
    ("guard-08-纯计算放行", "guard",
     pre("bash", {"command": 'python3 -c "import statistics; print(statistics.mean([1,2,3]))"'}), {},
     {"deny": False}),
    ("guard-09-写-holdings-拒", "guard", pre("write_file", {"file_path": "holdings/x.md"}), {},
     {"deny": True, "reason_has": ["holdings"]}),
    ("guard-10-写-reports-放行", "guard", pre("write_file", {"file_path": "reports/x.md"}), {},
     {"deny": False}),
    ("guard-11-绕过MCP层跑server源码-P1-18", "guard",
     pre("bash", {"command": '/opt/hca/venv-hunter/bin/python -c "import sys; sys.path.insert(0, \'/opt/hca/mcp\')"'}),
     {}, {"deny": True, "reason_has": ["/opt/hca"]}),
    ("guard-12-给hunter系MCP注入身份-P0-5", "guard",
     pre("mcp__watchlist__stock_quickview", {"symbol": "600519"}),
     {"HUNTER_USER_ID": "u-case", "HERMES_API_URL": "", "HUNTER_INTERNAL_KEY": ""},
     {"deny": False, "updated_input_has": {"_hermes_user_id": "u-case"}}),
    ("guard-13-查不到身份就不注入", "guard",
     pre("mcp__watchlist__stock_quickview", {"symbol": "600519"}),
     {"HUNTER_USER_ID": "", "HERMES_API_URL": "", "HUNTER_INTERNAL_KEY": ""},
     {"deny": False, "stdout_empty": True}),

    # ── audit（12 依赖 11 落下的起始记录，顺序相关，名字里标了）──────────
    ("audit-14-前置-guard先放行一次bash", "guard", pre("bash", {"command": "ls reports/"}, sid="pair"), {},
     {"deny": False}),
    ("audit-15-配上耗时与参数", "audit", post("bash", sid="pair"), {},
     {"stdout_empty": True, "audit_last": {"tool": "bash", "ok": True,
                                           "args_src": "guard-start", "has_duration": True}}),
    ("audit-16-失败事件-ok为false", "audit",
     post("bash", resp="command failed\n", sid="x", ev="PostToolUseFailure"), {},
     {"stdout_empty": True, "audit_last": {"ok": False}}),
    ("audit-17-配不上就写—", "audit", post("glob", sid="没有起始记录"), {},
     {"stdout_empty": True, "audit_last": {"args_src": "—", "has_duration": False}}),
    ("audit-18-日志写不进也不拖垮对话", "audit", post("bash"), {"HCA_AUDIT_LOG": "/proc/nope/a.jsonl"},
     {"stdout_empty": True, "exit": 0}),

    # ── lang ─────────────────────────────────────────────────────────────
    ("lang-19-注入语言硬约束", "lang", ups(), {},
     {"stdout_has": ["【语言硬约束】", "简体中文"], "last_line_not_json": True}),
    ("lang-20-开关关掉就不注入", "lang", ups(), {"HCA_LANG_HOOK": "0"}, {"stdout_empty": True}),

    # ── context ──────────────────────────────────────────────────────────
    ("context-21-注入真实时间", "context", ups(), {},
     {"stdout_has": ["<hca-context>", "上海时间"], "last_line_not_json": True}),

    # ── budget ───────────────────────────────────────────────────────────
    ("budget-22-默认关-什么都不做", "budget",
     {"session_id": "b", "hook_event_name": "Stop", "transcript_path": None,
      "stop_reason": "Stopped"}, {}, {"stdout_empty": True, "no_file": ".atomcode/budget.json"}),
    ("budget-23-开了之后按会话meta记账", "budget",
     {"session_id": "b1", "hook_event_name": "Stop",
      "transcript_path": "@@META@@", "stop_reason": "Stopped"},
     {"HCA_BUDGET_ENABLED": "1", "HCA_QUOTA_URL": "http://127.0.0.1:1/nope",
      "HCA_LLM_API_KEY": "", "HCA_LLM_API_KEY_FILE": ""},
     {"stdout_empty": True, "budget_state": {"tool_calls": 4, "tokens": None}}),
    ("budget-24-超工具次数就拦", "budget", ups(sid="b1"),
     {"HCA_BUDGET_ENABLED": "1", "HCA_BUDGET_DAILY_TOOL_CALLS": "4",
      "HCA_QUOTA_URL": "http://127.0.0.1:1/nope", "HCA_LLM_API_KEY": "",
      "HCA_LLM_API_KEY_FILE": ""},
     {"block": True, "reason_has": ["4 / 4"]}),
    ("budget-25-token未知时不拦", "budget", ups(sid="b1"),
     {"HCA_BUDGET_ENABLED": "1", "HCA_BUDGET_DAILY_TOKENS": "1",
      "HCA_QUOTA_URL": "http://127.0.0.1:1/nope", "HCA_LLM_API_KEY": "",
      "HCA_LLM_API_KEY_FILE": ""},
     {"stdout_empty": True}),
]


class Runner:
    def __init__(self, in_container: bool, container: str):
        self.in_container = in_container
        self.container = container
        self.ws = None

    def setup(self):
        if self.in_container:
            self.ws = subprocess.run(
                ["docker", "exec", self.container, "mktemp", "-d", "-p", "/tmp", "hookcase.XXXXXX"],
                capture_output=True, text=True, check=True).stdout.strip()
            self.hooks = CONTAINER_HOOKS
            # 真实会话 meta 的形状，放进临时工作区
            self.sh(["mkdir", "-p", self.ws + "/sessions"])
            self.write(self.ws + "/sessions/sid.meta",
                       json.dumps({"turn_stats": REAL_META_TURNS}, ensure_ascii=False))
        else:
            import tempfile
            self.ws = tempfile.mkdtemp(prefix="hookcase-")
            self.hooks = str(LOCAL_HOOKS)
            os.makedirs(self.ws + "/sessions", exist_ok=True)
            Path(self.ws + "/sessions/sid.meta").write_text(
                json.dumps({"turn_stats": REAL_META_TURNS}, ensure_ascii=False), encoding="utf-8")

    def sh(self, argv):
        if self.in_container:
            return subprocess.run(["docker", "exec", self.container, *argv],
                                  capture_output=True, text=True)
        return subprocess.run(argv, capture_output=True, text=True)

    def write(self, path, content):
        if self.in_container:
            p = subprocess.Popen(["docker", "exec", "-i", self.container,
                                  "sh", "-c", "cat > " + shlex.quote(path)],
                                 stdin=subprocess.PIPE, text=True)
            p.communicate(content)
        else:
            Path(path).write_text(content, encoding="utf-8")

    def read(self, path):
        r = self.sh(["cat", path])
        return r.stdout if r.returncode == 0 else None

    def run_hook(self, hook, payload, env):
        script = "{}/{}.py".format(self.hooks, hook)
        envp = dict(env)
        envp.setdefault("HCA_WORKSPACE", self.ws)
        if self.in_container:
            argv = ["docker", "exec", "-i"]
            for k, v in envp.items():
                argv += ["-e", "{}={}".format(k, v)]
            argv += [self.container, "python3", script]
        else:
            argv = [sys.executable, script]
        body = json.dumps(payload, ensure_ascii=False)
        if self.in_container:
            p = subprocess.run(argv, input=body, capture_output=True, text=True, timeout=90)
        else:
            e = dict(os.environ); e.update(envp)
            p = subprocess.run(argv, input=body, env=e, capture_output=True, text=True, timeout=90)
        return p

    def cleanup(self):
        if not self.ws:
            return
        if self.in_container:
            self.sh(["rm", "-rf", self.ws])
        else:
            import shutil
            shutil.rmtree(self.ws, ignore_errors=True)


def last_json(stdout):
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


def check(rn: Runner, name, hook, payload, env, want):
    payload = json.loads(json.dumps(payload).replace("@@META@@", rn.ws + "/sessions/sid.jsonl"))
    payload.setdefault("cwd", rn.ws)
    if hook == "audit" and name.startswith("audit-15"):
        time.sleep(0.05)   # 让耗时是个正数，证明它真的在算差值
    p = rn.run_hook(hook, payload, env)
    res = last_json(p.stdout)
    hs = (res or {}).get("hookSpecificOutput", {}) or {}
    problems = []

    if "exit" in want and p.returncode != want["exit"]:
        problems.append("退出码 {} ≠ {}".format(p.returncode, want["exit"]))
    if p.returncode != 0:
        problems.append("退出码 {}（hook 一律该 exit 0）stderr={}".format(p.returncode, p.stderr[:200]))
    if want.get("deny") is True and hs.get("permissionDecision") != "deny":
        problems.append("期望 deny，实得 {}".format(hs.get("permissionDecision") or "（无决策）"))
    if want.get("deny") is False and hs.get("permissionDecision") == "deny":
        problems.append("期望放行，实得 deny：" + str(hs.get("permissionDecisionReason"))[:120])
    if want.get("block") and (res or {}).get("decision") != "block":
        problems.append("期望 block，实得 {}".format((res or {}).get("decision") or "（无决策）"))
    for kw in want.get("reason_has", []):
        blob = str(hs.get("permissionDecisionReason") or (res or {}).get("reason") or "")
        if kw not in blob:
            problems.append("理由里没有「{}」：{}".format(kw, blob[:120]))
    if want.get("stdout_empty") and p.stdout.strip():
        problems.append("期望不输出，实得：" + p.stdout.strip()[:120])
    for kw in want.get("stdout_has", []):
        if kw not in p.stdout:
            problems.append("输出里没有「{}」".format(kw))
    if want.get("last_line_not_json") and last_json(p.stdout) is not None:
        problems.append("最后一行是 JSON —— 会被上游当成决策解析")
    if "updated_input_has" in want:
        ui = hs.get("updatedInput") or {}
        for k, v in want["updated_input_has"].items():
            if ui.get(k) != v:
                problems.append("updatedInput.{} = {} ≠ {}".format(k, ui.get(k), v))
    if "audit_last" in want:
        raw = rn.read(rn.ws + "/.atomcode/audit.jsonl") or ""
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        rec = json.loads(lines[-1]) if lines else {}
        for k, v in want["audit_last"].items():
            if k == "has_duration":
                got = isinstance(rec.get("duration_ms"), int)
                if got != v:
                    problems.append("duration_ms={}（期望{}有值）".format(rec.get("duration_ms"), "" if v else "没"))
            elif rec.get(k) != v:
                problems.append("audit.{} = {!r} ≠ {!r}".format(k, rec.get(k), v))
    if "budget_state" in want:
        raw = rn.read(rn.ws + "/.atomcode/budget.json")
        st = json.loads(raw) if raw else {}
        for k, v in want["budget_state"].items():
            if st.get(k) != v:
                problems.append("budget.{} = {!r} ≠ {!r}".format(k, st.get(k), v))
    if "no_file" in want and rn.read(rn.ws + "/" + want["no_file"]) is not None:
        problems.append("不该有这个文件：" + want["no_file"])

    return problems, p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-container", action="store_true")
    ap.add_argument("--container", default=os.environ.get("HCA_DAEMON_CONTAINER", "hca-daemon"))
    ap.add_argument("--json-out", default="")
    a = ap.parse_args()

    rn = Runner(a.in_container, a.container)
    rn.setup()
    where = "容器 {}".format(a.container) if a.in_container else "本机仓库"
    print("hook 用例 · 跑在 {} · 临时工作区 {}\n".format(where, rn.ws))
    print("{:<44} {:<6} {}".format("用例", "结果", "说明"))
    print("─" * 100)
    failed = 0
    records = []
    try:
        for name, hook, payload, env, want in CASES:
            problems, p = check(rn, name, hook, payload, env, want)
            state = "过" if not problems else "失败"
            if problems:
                failed += 1
            print("{:<44} {:<6} {}".format(name, state, "；".join(problems) if problems else ""))
            records.append({"case": name, "hook": hook, "ok": not problems,
                            "problems": problems, "exit": p.returncode,
                            "stdout_head": p.stdout[:400]})
    finally:
        rn.cleanup()
    print("─" * 100)
    print("{} 条，过 {}，失败 {}".format(len(CASES), len(CASES) - failed, failed))
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(
            {"where": where, "total": len(CASES), "failed": failed, "cases": records},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print("明细写到 " + a.json_out)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
