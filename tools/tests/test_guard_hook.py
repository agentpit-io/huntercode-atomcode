"""guard.py / context.py 两个 hook 的单元测试。

按 hook 真实的契约跑：**起子进程、喂 stdin 的 JSON、读 stdout 最后一行**。
不 import 它们的内部函数 —— 那样测不到"上游到底会看到什么"。

    python3 -m pytest tools/tests/test_guard_hook.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "distro" / "workspace-template" / ".hooks" / "guard.py"
CONTEXT = REPO / "distro" / "workspace-template" / ".hooks" / "context.py"


def run_guard(tool: str, tool_input: dict, workspace: Path, env_extra: dict | None = None):
    """返回 (stdout 最后一行解析出的 dict 或 None, returncode)。"""
    env = dict(os.environ)
    env["HCA_WORKSPACE"] = str(workspace)
    env.update(env_extra or {})
    payload = json.dumps({
        "session_id": "t", "hook_event_name": "PreToolUse",
        "tool_name": tool, "tool_input": tool_input, "cwd": str(workspace),
    }, ensure_ascii=False)
    p = subprocess.run([sys.executable, str(GUARD)], input=payload, env=env,
                       capture_output=True, text=True, timeout=30)
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    if not lines:
        return None, p.returncode
    return json.loads(lines[-1]), p.returncode


def hs(res):
    return (res or {}).get("hookSpecificOutput", {})


def is_deny(res):
    return hs(res).get("permissionDecision") == "deny"


# ── 写类工具 ────────────────────────────────────────────────────────────────

def test_write_outside_allowed_dirs_denied(tmp_path):
    for tool in ("write_file", "edit_file", "search_replace"):
        res, rc = run_guard(tool, {"file_path": "holdings/positions.md"}, tmp_path)
        assert rc == 0
        assert is_deny(res), f"{tool} 写 holdings/ 必须被拒：{res}"
        assert "holdings" in hs(res)["permissionDecisionReason"]


def test_write_into_reports_and_theses_allowed(tmp_path):
    for path in ("reports/600519.md", "theses/600519-thesis.md",
                 "reports/sub/deep/a.md"):
        res, _ = run_guard("write_file", {"file_path": path}, tmp_path)
        assert not is_deny(res), f"{path} 应放行，却被拒：{res}"


def test_write_absolute_path_inside_reports_allowed(tmp_path):
    res, _ = run_guard("write_file", {"file_path": str(tmp_path / "reports" / "x.md")},
                       tmp_path)
    assert not is_deny(res)


def test_write_escape_via_dotdot_denied(tmp_path):
    res, _ = run_guard("write_file", {"file_path": "reports/../holdings/a.md"}, tmp_path)
    assert is_deny(res)


def test_write_outside_workspace_denied(tmp_path):
    res, _ = run_guard("write_file", {"file_path": "/etc/passwd"}, tmp_path)
    assert is_deny(res)


def test_write_without_path_denied(tmp_path):
    res, _ = run_guard("write_file", {"content": "x"}, tmp_path)
    assert is_deny(res)


def test_parallel_edit_files_checks_every_target(tmp_path):
    args = {"files": [{"path": "reports/a.md", "instruction": "x"},
                      {"path": "scripts/b.py", "instruction": "y"}]}
    res, _ = run_guard("parallel_edit_files", args, tmp_path)
    assert is_deny(res), f"只要有一个目标越界就要拒：{res}"


# ── bash ────────────────────────────────────────────────────────────────────

def test_bash_readonly_compute_allowed(tmp_path):
    for cmd in ('python3 -c "print(1+1)"',
                "ls -la reports",
                "wc -l reports/a.md",
                'python3 -c "import statistics;print(statistics.mean([1,2,3]))"'):
        res, _ = run_guard("bash", {"command": cmd}, tmp_path)
        assert not is_deny(res), f"只读命令 {cmd!r} 不该被拒：{res}"


def test_bash_redirect_denied(tmp_path):
    for cmd in ("echo hi > reports/a.md", "echo hi >> holdings/a.md",
                "cat x 2> err.log"):
        res, _ = run_guard("bash", {"command": cmd}, tmp_path)
        assert is_deny(res), f"{cmd!r} 有写重定向，必须拒"


def test_bash_fd_dup_not_treated_as_redirect(tmp_path):
    res, _ = run_guard("bash", {"command": "ls reports 2>&1 | head"}, tmp_path)
    assert not is_deny(res), f"2>&1 是复制 fd，不是写文件：{res}"


def test_bash_dangerous_commands_denied(tmp_path):
    for cmd in ("rm -rf reports", "sudo ls", "git status", "pip install foo",
                "curl https://example.com", "wget http://x", "chmod 777 .",
                "tee reports/a.md", "sed -i s/a/b/ reports/a.md"):
        res, _ = run_guard("bash", {"command": cmd}, tmp_path)
        assert is_deny(res), f"{cmd!r} 必须拒：{res}"


def test_bash_inline_write_denied(tmp_path):
    cmd = 'python3 -c "open(\'reports/a.md\',\'w\').write(\'x\')"'
    res, _ = run_guard("bash", {"command": cmd}, tmp_path)
    assert is_deny(res)


def test_bash_inline_http_denied(tmp_path):
    cmd = 'python3 -c "import requests;print(requests.get(\'http://x\').text)"'
    res, _ = run_guard("bash", {"command": cmd}, tmp_path)
    assert is_deny(res)
    assert "MCP" in hs(res)["permissionDecisionReason"]


def test_bash_outside_workspace_path_denied(tmp_path):
    res, _ = run_guard("bash", {"command": "cat /etc/passwd"}, tmp_path)
    assert is_deny(res)


# ── 绕过 MCP 层直接跑 server 源码（待办池 P1-18 / M3 实测过一次真实发生）────

def test_bash_distro_private_dir_denied(tmp_path):
    """首词就是 /opt/hca 下的解释器 —— 老的路径检查漏掉它（只查首词之后的 token）。"""
    cmd = '/opt/hca/venv-hunter/bin/python -c "print(1)"'
    res, _ = run_guard("bash", {"command": cmd}, tmp_path)
    assert is_deny(res)
    assert "/opt/hca" in hs(res)["permissionDecisionReason"]


def test_bash_distro_private_path_inside_inline_script_denied(tmp_path):
    """路径藏在引号里，不是独立 token —— 所以要对整条命令原文匹配。"""
    cmd = ('python3 -c "import sys; sys.path.insert(0, \'/opt/hca/mcp\'); '
           'import watchlist_mcp; print(watchlist_mcp)"')
    res, _ = run_guard("bash", {"command": cmd}, tmp_path)
    assert is_deny(res)
    assert "MCP" in hs(res)["permissionDecisionReason"]


def test_bash_normal_interpreter_still_allowed(tmp_path):
    """别把正常的只读计算一起拦了 —— 系统解释器照常放行。"""
    res, _ = run_guard("bash", {"command": 'python3 -c "import statistics;print(statistics.mean([1,2,3]))"'}, tmp_path)
    assert not is_deny(res)


def test_bash_dangerous_in_later_segment_denied(tmp_path):
    res, _ = run_guard("bash", {"command": "ls reports && rm -rf reports"}, tmp_path)
    assert is_deny(res)


# ── 只读工具的越界 ──────────────────────────────────────────────────────────

def test_read_outside_workspace_denied(tmp_path):
    res, _ = run_guard("read_file", {"file_path": "/etc/shadow"}, tmp_path)
    assert is_deny(res)


def test_read_inside_workspace_allowed(tmp_path):
    res, _ = run_guard("read_file", {"file_path": "holdings/positions.md"}, tmp_path)
    assert not is_deny(res)


# ── MCP：身份注入（P0-5）────────────────────────────────────────────────────

def test_hunter_mcp_gets_user_id_injected(tmp_path):
    res, _ = run_guard("mcp__uzi__stock_deep_analysis", {"code": "600519"}, tmp_path,
                       {"HUNTER_USER_ID": "u-42"})
    assert hs(res).get("updatedInput") == {"code": "600519", "_hermes_user_id": "u-42"}
    assert "permissionDecision" not in hs(res), "只改参数、不表态，否则会多弹一次权限"


def test_non_hunter_mcp_untouched(tmp_path):
    res, _ = run_guard("mcp__akshare__akshare_call", {"func": "x"}, tmp_path,
                       {"HUNTER_USER_ID": "u-42"})
    assert res is None, "akshare 不认 _hermes_user_id，不该改它的参数"


def test_no_user_id_no_rewrite(tmp_path):
    res, _ = run_guard("mcp__uzi__stock_deep_analysis", {"code": "600519"}, tmp_path,
                       {"HUNTER_USER_ID": ""})
    assert res is None


def test_existing_user_id_not_overwritten(tmp_path):
    res, _ = run_guard("mcp__uzi__stock_deep_analysis",
                       {"code": "600519", "_hermes_user_id": "already"}, tmp_path,
                       {"HUNTER_USER_ID": "u-42"})
    assert res is None


# ── 健壮性 ──────────────────────────────────────────────────────────────────

def test_garbage_stdin_proceeds(tmp_path):
    p = subprocess.run([sys.executable, str(GUARD)], input="not json",
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0
    assert p.stdout.strip() == "", "解析不了就放行，不输出决策"


def test_guard_writes_audit_line(tmp_path):
    run_guard("write_file", {"file_path": "holdings/a.md"}, tmp_path)
    log = tmp_path / ".atomcode" / "guard.jsonl"
    assert log.is_file()
    rec = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["tool"] == "write_file" and rec["decision"] == "deny"


# ── context.py ──────────────────────────────────────────────────────────────

def run_context(workspace: Path):
    env = dict(os.environ)
    env["HCA_WORKSPACE"] = str(workspace)
    p = subprocess.run([sys.executable, str(CONTEXT)],
                       input=json.dumps({"hook_event_name": "UserPromptSubmit",
                                         "prompt": "hi", "cwd": str(workspace)}),
                       env=env, capture_output=True, text=True, timeout=60)
    return p.stdout, p.returncode


def test_context_emits_time_and_wrapper(tmp_path):
    out, rc = run_context(tmp_path)
    assert rc == 0
    assert out.startswith("<hca-context>")
    assert out.rstrip().endswith("</hca-context>")
    assert "当前时间：" in out


def test_context_lists_existing_assets_only(tmp_path):
    (tmp_path / "holdings").mkdir()
    (tmp_path / "holdings" / "positions.md").write_text("x", encoding="utf-8")
    out, _ = run_context(tmp_path)
    assert "`holdings/`" in out and "positions.md" in out
    assert "`factors/`" not in out, "不存在的目录不该出现"


def test_context_without_calendar_says_nothing_about_market(tmp_path, monkeypatch):
    """没有 akshare 时**整段市场状态都不输出** —— 不许拿推算充数。"""
    env = dict(os.environ)
    env["HCA_WORKSPACE"] = str(tmp_path)
    # 用一个空目录当 PYTHONPATH 挡不住已装的 akshare；改为检查"要么有来源标注，要么没这一行"
    out, _ = run_context(tmp_path)
    if "交易日" in out:
        assert "tool_trade_date_hist_sina" in out, "说了交易日就必须标来源"


# ── 切词的边界情况（第一版用正则切词，栽在引号里的分号上）──────────────────

def test_quoted_operators_are_not_operators(tmp_path):
    for cmd in ('python3 -c "import statistics;print(statistics.mean([1,2]))"',
                'echo "a > b"',
                'python3 -c "print(3>2)"',
                "grep -c 'a|b' reports/a.md"):
        res, _ = run_guard("bash", {"command": cmd}, tmp_path)
        assert not is_deny(res), f"{cmd!r} 里的操作符在引号内，不该触发拦截：{res}"


def test_command_substitution_head_is_checked(tmp_path):
    for cmd in ("echo $(rm -rf reports)", "echo `rm -rf reports`"):
        res, _ = run_guard("bash", {"command": cmd}, tmp_path)
        assert is_deny(res), f"{cmd!r} 里的 rm 必须被抓到：{res}"


def test_pipeline_of_readonly_allowed(tmp_path):
    res, _ = run_guard("bash", {"command": "cat reports/a.md | head -20 | wc -l"}, tmp_path)
    assert not is_deny(res)


def test_env_prefix_does_not_hide_command(tmp_path):
    res, _ = run_guard("bash", {"command": "LC_ALL=C rm -rf reports"}, tmp_path)
    assert is_deny(res)
