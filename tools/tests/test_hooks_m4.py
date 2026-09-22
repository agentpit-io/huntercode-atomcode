"""M4 新增/补齐的 hook 单测：guard 的四个新口子、audit、lang、budget。

和 test_guard_hook.py 同一个路子：**起子进程、喂 stdin 的 JSON、读 stdout 最后一行**，
测的是"上游到底会看到什么"，不 import 内部函数。

    python3 -m pytest tools/tests/test_hooks_m4.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOKS = REPO / "distro" / "workspace-template" / ".hooks"
GUARD, AUDIT, LANG, BUDGET = (HOOKS / "guard.py", HOOKS / "audit.py",
                              HOOKS / "lang.py", HOOKS / "budget.py")


def run_hook(script: Path, payload: dict, workspace: Path, env_extra=None, argv=()):
    env = dict(os.environ)
    env["HCA_WORKSPACE"] = str(workspace)
    env.update(env_extra or {})
    p = subprocess.run([sys.executable, str(script), *argv],
                       input=json.dumps(payload, ensure_ascii=False),
                       env=env, capture_output=True, text=True, timeout=60)
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    last = None
    if lines:
        try:
            last = json.loads(lines[-1])
        except json.JSONDecodeError:
            last = None
    return p, last


def guard(tool: str, tool_input: dict, workspace: Path, env_extra=None, sid="t"):
    p, last = run_hook(GUARD, {
        "session_id": sid, "hook_event_name": "PreToolUse",
        "tool_name": tool, "tool_input": tool_input, "cwd": str(workspace),
    }, workspace, env_extra)
    return p, last


def is_deny(res):
    return ((res or {}).get("hookSpecificOutput", {}) or {}).get("permissionDecision") == "deny"


def reason(res):
    return ((res or {}).get("hookSpecificOutput", {}) or {}).get("permissionDecisionReason", "")


# ── guard 口子 1：bash_start 必须和 bash 同判 ───────────────────────────────

def test_bash_start_is_judged_like_bash(tmp_path):
    """上游 tools/mod.rs:210 无条件注册 bash_start，参数名同样是 command。

    M3 的 guard 只判 tool == "bash"，所以这是一条完整绕过。
    """
    bad = "rm -rf holdings/"
    _, as_bash = guard("bash", {"command": bad}, tmp_path)
    _, as_start = guard("bash_start", {"command": bad}, tmp_path)
    assert is_deny(as_bash), "bash 下 rm 必须被拒"
    assert is_deny(as_start), f"bash_start 下同一条命令也必须被拒，否则丢到后台就绕过了：{as_start}"


def test_bash_start_readonly_still_allowed(tmp_path):
    _, res = guard("bash_start", {"command": "cat reports/a.md"}, tmp_path)
    assert not is_deny(res), f"只读命令在 bash_start 下也该放行：{res}"


# ── guard 口子 2：P1-20 直接调取数库 ───────────────────────────────────────

def test_inline_data_library_denied(tmp_path):
    cases = [
        'python3 -c "import akshare as ak; print(ak.stock_zh_a_daily(symbol=\'sh600519\'))"',
        'python3 -c "from akshare import stock_zh_a_spot; print(1)"',
        'python3 -c "import tushare as ts; ts.pro_api()"',
        'python3 -c "import yfinance as yf; yf.download(\'AAPL\')"',
        'python3 -c "import baostock as bs; bs.login()"',
    ]
    for cmd in cases:
        _, res = guard("bash", {"command": cmd}, tmp_path)
        assert is_deny(res), f"直接调取数库必须被拒：{cmd} → {res}"
        assert "MCP" in reason(res), f"理由里要说清去调 MCP：{reason(res)}"


def test_pure_calculation_still_allowed(tmp_path):
    """反误伤：纯计算、纯字符串处理不能被取数库那条正则扫到。"""
    ok = [
        'python3 -c "import statistics; print(statistics.mean([1,2,3]))"',
        'python3 -c "print(sum(int(x) for x in \'1 2 3\'.split()))"',
        'python3 -c "import json; print(json.dumps({\'pe\': 12.3}))"',
        "cat reports/600519.md",
        "ls -la theses/",
        'python3 -c "print(1/2)"',
    ]
    for cmd in ok:
        _, res = guard("bash", {"command": cmd}, tmp_path)
        assert not is_deny(res), f"只读/纯计算被误伤了：{cmd} → {reason(res)}"


# ── guard 口子 3：包装命令 / sh -c / python -m ──────────────────────────────

def test_wrapper_commands_unwrapped(tmp_path):
    for cmd in ["env X=1 rm -rf holdings/",
                "timeout 5 rm -rf holdings/",
                "nohup rm -rf holdings/",
                "nice -n 5 curl https://example.com",
                "xargs rm"]:
        _, res = guard("bash", {"command": cmd}, tmp_path)
        assert is_deny(res), f"包装命令后面的真首词要判到：{cmd} → {res}"


def test_sh_c_subcommand_recursed(tmp_path):
    for cmd in ['sh -c "rm -rf holdings/"',
                'bash -c "curl https://example.com -o x"',
                "sh -c 'python3 -c \"import akshare\"'"]:
        _, res = guard("bash", {"command": cmd}, tmp_path)
        assert is_deny(res), f"`-c` 的子命令要递归判：{cmd} → {res}"


def test_sh_c_readonly_subcommand_allowed(tmp_path):
    _, res = guard("bash", {"command": 'sh -c "cat reports/a.md"'}, tmp_path)
    assert not is_deny(res), f"子命令是只读的就该放行：{reason(res)}"


def test_python_m_module_denied(tmp_path):
    _, res = guard("bash", {"command": "python3 -m pip install akshare"}, tmp_path)
    assert is_deny(res) and "pip" in reason(res), f"python -m pip 要拦：{res}"


# ── guard 口子 4：中间带 .. 的路径 ─────────────────────────────────────────

def test_midpath_dotdot_escape_denied(tmp_path):
    for cmd in ["cat holdings/../../etc/passwd",
                "head -5 reports/../../../root/.ssh/id_rsa"]:
        _, res = guard("bash", {"command": cmd}, tmp_path)
        assert is_deny(res), f"折出来在工作区外就该拒：{cmd} → {res}"


def test_relative_inside_path_allowed(tmp_path):
    _, res = guard("bash", {"command": "cat reports/sub/../a.md"}, tmp_path)
    assert not is_deny(res), f"折完还在工作区内要放行：{reason(res)}"


# ── guard：给 audit 落的起始记录 ───────────────────────────────────────────

def test_guard_writes_start_record_for_allowed_call(tmp_path):
    guard("read_file", {"file_path": "reports/a.md"}, tmp_path, sid="s1")
    d = tmp_path / ".atomcode" / "tool-start"
    files = list(d.glob("*.json")) if d.is_dir() else []
    assert len(files) == 1, f"放行的调用要留一条起始记录：{files}"
    rec = json.loads(files[0].read_text(encoding="utf-8"))
    assert rec["tool"] == "read_file" and rec["session_id"] == "s1"
    assert "file_path" in rec["args_keys"] and isinstance(rec["at"], float)


def test_guard_writes_no_start_record_when_denied(tmp_path):
    """被拒的调用不会有 PostToolUse —— 留下记录就会被下一次同名调用配走，算出假耗时。"""
    _, res = guard("write_file", {"file_path": "holdings/x.md"}, tmp_path, sid="s1")
    assert is_deny(res)
    d = tmp_path / ".atomcode" / "tool-start"
    assert not d.is_dir() or not list(d.glob("*.json"))


# ── audit ─────────────────────────────────────────────────────────────────

def audit(event: str, workspace: Path, tool="bash", resp="hello\n", sid="s1", env_extra=None):
    payload = {"session_id": sid, "hook_event_name": event,
               "tool_name": tool, "tool_response": resp, "cwd": str(workspace)}
    p, _ = run_hook(AUDIT, payload, workspace, env_extra)
    return p


def read_audit(workspace: Path):
    f = workspace / ".atomcode" / "audit.jsonl"
    return [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_audit_pairs_with_guard_for_args_and_duration(tmp_path):
    """耗时与参数摘要只能靠 guard 的起始记录 —— PostToolUse payload 里两样都没有。"""
    guard("bash", {"command": "cat reports/a.md"}, tmp_path, sid="s1")
    time.sleep(0.05)
    p = audit("PostToolUse", tmp_path, tool="bash", sid="s1")
    assert p.returncode == 0
    rec = read_audit(tmp_path)[-1]
    assert rec["tool"] == "bash" and rec["ok"] is True
    assert rec["args_src"] == "guard-start"
    assert "command" in rec["args_keys"]
    assert rec["duration_ms"] >= 50, rec
    assert rec["response_len"] == len("hello\n")


def test_audit_writes_dash_when_no_start_record(tmp_path):
    """拿不到就写 —，不猜（总控红线 1）。"""
    audit("PostToolUse", tmp_path, tool="glob", sid="s9")
    rec = read_audit(tmp_path)[-1]
    assert rec["duration_ms"] is None and rec["duration_src"] == "—"
    assert rec["args_head"] is None and rec["args_src"] == "—"


def test_audit_failure_event_marks_not_ok(tmp_path):
    audit("PostToolUseFailure", tmp_path, tool="bash", resp="command failed\n")
    rec = read_audit(tmp_path)[-1]
    assert rec["ok"] is False and rec["event"] == "PostToolUseFailure"


def test_audit_start_record_consumed_once(tmp_path):
    """一条起始记录只能配一次，否则第二次调用会蹭到第一次的开始时刻。"""
    guard("bash", {"command": "ls"}, tmp_path, sid="s1")
    audit("PostToolUse", tmp_path, tool="bash", sid="s1")
    audit("PostToolUse", tmp_path, tool="bash", sid="s1")
    recs = read_audit(tmp_path)
    assert recs[0]["duration_src"].startswith("guard-start")
    assert recs[1]["duration_ms"] is None


def test_audit_user_id_from_session_cache(tmp_path):
    d = tmp_path / ".atomcode"
    d.mkdir(parents=True, exist_ok=True)
    (d / "session-user.json").write_text(
        json.dumps({"s1": {"uid": "u-42", "at": time.time()}}), encoding="utf-8")
    audit("PostToolUse", tmp_path, sid="s1")
    rec = read_audit(tmp_path)[-1]
    assert rec["user_id"] == "u-42" and rec["user_src"] == "session-cache"


def test_audit_user_id_falls_back_to_env(tmp_path):
    audit("PostToolUse", tmp_path, sid="s-none", env_extra={"HUNTER_USER_ID": "u-env"})
    rec = read_audit(tmp_path)[-1]
    assert rec["user_id"] == "u-env" and rec["user_src"] == "env"


def test_audit_never_blocks_on_unwritable_log(tmp_path):
    """日志写不进去（只读挂载 / 磁盘满）必须照样 exit 0、不输出决策。"""
    p = audit("PostToolUse", tmp_path, env_extra={"HCA_AUDIT_LOG": "/proc/nope/audit.jsonl"})
    assert p.returncode == 0 and p.stdout.strip() == ""


def test_audit_rotates_at_threshold(tmp_path):
    log = tmp_path / "a.jsonl"
    log.write_text("x" * 2048, encoding="utf-8")
    audit("PostToolUse", tmp_path, env_extra={"HCA_AUDIT_LOG": str(log),
                                              "HCA_AUDIT_MAX_MB": "0.001"})
    assert (tmp_path / "a.jsonl.1").exists(), "超过阈值要轮转"
    assert len(read_jsonl(log)) == 1


def read_jsonl(path: Path):
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ── lang ──────────────────────────────────────────────────────────────────

def test_lang_injects_rule(tmp_path):
    p, _ = run_hook(LANG, {"session_id": "s", "hook_event_name": "UserPromptSubmit",
                           "prompt": "分析一下 600519", "cwd": str(tmp_path)}, tmp_path)
    assert p.returncode == 0
    assert "【语言硬约束】" in p.stdout
    assert p.stdout.strip().splitlines()[-1] == "</hca-lang>", \
        "最后一行不能是 JSON，否则会被上游当成决策解析"


def test_lang_can_be_switched_off(tmp_path):
    p, _ = run_hook(LANG, {"hook_event_name": "UserPromptSubmit", "prompt": "x",
                           "cwd": str(tmp_path)}, tmp_path, {"HCA_LANG_HOOK": "0"})
    assert p.returncode == 0 and p.stdout.strip() == ""


def test_rule_text_matches_upstream():
    """规则原文只有一处定义：apps/api/agents/text_sanitizer.py 的 ZH_ONLY_RULE。

    hook 跑在 daemon 容器里 import 不到 api 的包，只能逐字副本；这条断言就是
    "改一处漏一处"的闸（交接稿铁律 3）。
    """
    src = (REPO / "apps" / "api" / "agents" / "text_sanitizer.py").read_text(encoding="utf-8")
    ns: dict = {}
    start = src.index("ZH_ONLY_RULE = (")
    end = src.index(")", src.index('"\n)', start)) + 1
    exec(src[start:end], ns)  # noqa: S102  只 exec 这一个字符串字面量拼接
    upstream = ns["ZH_ONLY_RULE"]
    hook_src = LANG.read_text(encoding="utf-8")
    ns2: dict = {}
    s2 = hook_src.index("ZH_ONLY_RULE = (")
    e2 = hook_src.index(")", hook_src.index('"\n)', s2)) + 1
    exec(hook_src[s2:e2], ns2)  # noqa: S102
    assert ns2["ZH_ONLY_RULE"] == upstream, "hook 里的语言规则与 api 那份不再逐字一致"


# ── budget ────────────────────────────────────────────────────────────────

def budget(event: str, workspace: Path, env_extra=None, **extra):
    payload = {"session_id": extra.pop("sid", "s1"), "hook_event_name": event,
               "cwd": str(workspace)}
    payload.update(extra)
    return run_hook(BUDGET, payload, workspace, env_extra)


def test_budget_off_by_default_does_nothing(tmp_path):
    """默认关：不记账、不拦、不建文件（计划 v0.2 TP-05「默认关，与现状一致」）。"""
    p, _ = budget("Stop", tmp_path, transcript_path=None, stop_reason="Stopped")
    assert p.returncode == 0 and p.stdout.strip() == ""
    assert not (tmp_path / ".atomcode" / "budget.json").exists()
    p2, _ = budget("UserPromptSubmit", tmp_path, prompt="x")
    assert p2.stdout.strip() == ""


def write_meta(tmp_path: Path, turn_stats: list) -> str:
    d = tmp_path / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    (d / "sid.meta").write_text(json.dumps({"turn_stats": turn_stats}), encoding="utf-8")
    return str(d / "sid.jsonl")   # payload 里给的是 .jsonl，hook 自己换成 .meta


ON = {"HCA_BUDGET_ENABLED": "1", "HCA_QUOTA_URL": "http://127.0.0.1:1/nope",
      "HCA_LLM_API_KEY": "", "HCA_LLM_API_KEY_FILE": ""}


def test_budget_counts_tool_calls_from_session_meta(tmp_path):
    """真实字段形状取自测试机主部署的一份 .meta（M4 报告 §3.3）。"""
    tp = write_meta(tmp_path, [
        {"turn_id": 1, "round_count": 4, "tool_call_count": 3, "duration_ms": 30256,
         "total_tokens": 0, "used_tokens": 12783,
         "model_usage": [{"provider_id": "hunter", "model_id": "hunter-chat",
                          "tokens": {"input": 17038, "output": 40, "cached_input": 0}}]},
        {"turn_id": 2, "round_count": 2, "tool_call_count": 1, "duration_ms": 11324,
         "total_tokens": 0, "used_tokens": 15627},
    ])
    p, _ = budget("Stop", tmp_path, env_extra=ON, transcript_path=tp, stop_reason="Stopped")
    assert p.returncode == 0
    st = json.loads((tmp_path / ".atomcode" / "budget.json").read_text(encoding="utf-8"))
    assert st["tool_calls"] == 4, st
    # turn 2 没有 model_usage → 覆盖不全 → 不拿 meta 当 token 源
    assert st["tokens"] is None and st["tokens_src"] == "—", st
    line = read_jsonl(tmp_path / ".atomcode" / "budget.jsonl")[-1]
    assert line["meta_turns"] == 2 and line["meta_turns_with_usage"] == 1


def test_budget_uses_meta_tokens_when_coverage_full(tmp_path):
    tp = write_meta(tmp_path, [
        {"turn_id": 1, "tool_call_count": 2,
         "model_usage": [{"tokens": {"input": 100, "output": 20, "cached_input": 5}}]},
    ])
    budget("Stop", tmp_path, env_extra=ON, transcript_path=tp, stop_reason="Stopped")
    st = json.loads((tmp_path / ".atomcode" / "budget.json").read_text(encoding="utf-8"))
    assert st["tokens"] == 125 and st["tokens_src"] == "session-meta", st


def test_budget_blocks_prompt_over_tool_call_limit(tmp_path):
    tp = write_meta(tmp_path, [{"turn_id": 1, "tool_call_count": 5}])
    budget("Stop", tmp_path, env_extra=ON, transcript_path=tp, stop_reason="Stopped")
    env = dict(ON, HCA_BUDGET_DAILY_TOOL_CALLS="5")
    p, last = budget("UserPromptSubmit", tmp_path, env_extra=env, prompt="再查一只")
    assert last and last.get("decision") == "block", p.stdout
    assert "5 / 5" in last["reason"]


def test_budget_does_not_block_under_limit(tmp_path):
    tp = write_meta(tmp_path, [{"turn_id": 1, "tool_call_count": 2}])
    budget("Stop", tmp_path, env_extra=ON, transcript_path=tp, stop_reason="Stopped")
    p, _ = budget("UserPromptSubmit", tmp_path,
                  env_extra=dict(ON, HCA_BUDGET_DAILY_TOOL_CALLS="5"), prompt="x")
    assert p.stdout.strip() == ""


def test_budget_never_blocks_on_unknown_tokens(tmp_path):
    """token 拿不到时**不拦** —— 不能把"不知道"当成"超了"。"""
    tp = write_meta(tmp_path, [{"turn_id": 1, "tool_call_count": 1}])
    budget("Stop", tmp_path, env_extra=ON, transcript_path=tp, stop_reason="Stopped")
    p, _ = budget("UserPromptSubmit", tmp_path,
                  env_extra=dict(ON, HCA_BUDGET_DAILY_TOKENS="1"), prompt="x")
    assert p.stdout.strip() == "", "token 未知却拦了，等于把 — 当成超限"


def test_budget_survives_missing_meta(tmp_path):
    p, _ = budget("Stop", tmp_path, env_extra=ON,
                  transcript_path=str(tmp_path / "nope.jsonl"), stop_reason="Stopped")
    assert p.returncode == 0
    line = read_jsonl(tmp_path / ".atomcode" / "budget.jsonl")[-1]
    assert line["meta"].startswith("—")


def test_budget_session_limit(tmp_path):
    tp = write_meta(tmp_path, [{"turn_id": 1, "tool_call_count": 3}])
    budget("Stop", tmp_path, env_extra=ON, transcript_path=tp, stop_reason="Stopped", sid="sA")
    p, last = budget("UserPromptSubmit", tmp_path,
                     env_extra=dict(ON, HCA_BUDGET_SESSION_TOOL_CALLS="3"),
                     prompt="x", sid="sA")
    assert last and last["decision"] == "block", p.stdout
    p2, last2 = budget("UserPromptSubmit", tmp_path,
                       env_extra=dict(ON, HCA_BUDGET_SESSION_TOOL_CALLS="3"),
                       prompt="x", sid="sB")
    assert last2 is None, "换一个会话不该被上一个会话的用量拦住"


# ── audit 的异步上报（真起一个本地 HTTP 收集端，不 mock 网络）───────────────

def test_audit_webhook_delivers_out_of_band(tmp_path):
    """上报是**真的异步**：子进程脱离进程组去 POST，父 hook 不等它。

    这里起一个真的 http.server 收，断言：① hook 立刻返回；② 记录最终到达。
    """
    import http.server
    import threading

    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            got.append((self.headers.get("Authorization"), self.rfile.read(n)))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):  # 别把测试输出刷满
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:{}/audit".format(srv.server_address[1])
    try:
        t0 = time.time()
        p = audit("PostToolUse", tmp_path, env_extra={
            "HCA_AUDIT_WEBHOOK": url, "HCA_AUDIT_WEBHOOK_TOKEN": "tk-1"})
        elapsed = time.time() - t0
        assert p.returncode == 0
        for _ in range(100):
            if got:
                break
            time.sleep(0.1)
        assert got, "异步上报没有到达收集端"
        auth, body = got[0]
        assert auth == "Bearer tk-1"
        rec = json.loads(body.decode("utf-8"))
        assert rec["tool"] == "bash" and rec["event"] == "PostToolUse"
        # 本地那一行必须照样落盘（上报是附加动作，不是替代）
        assert read_audit(tmp_path)[-1]["tool"] == "bash"
        assert elapsed < 5, f"hook 自己花了 {elapsed:.1f}s，说明它在等网络"
    finally:
        srv.shutdown()


def test_audit_survives_dead_webhook(tmp_path):
    """收集端挂了：hook 照样 exit 0、本地照样落盘、不输出决策。"""
    p = audit("PostToolUse", tmp_path,
              env_extra={"HCA_AUDIT_WEBHOOK": "http://127.0.0.1:1/nope"})
    assert p.returncode == 0 and p.stdout.strip() == ""
    assert read_audit(tmp_path)[-1]["tool"] == "bash"
