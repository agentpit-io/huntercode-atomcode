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


def test_模型自填的身份会被env里的真身份覆盖(tmp_path):
    """I2 加严，理由见 test_guard_session_identity.py 里同名那条：
    工具参数是模型生成的，「已经有就不动」等于把身份交给模型说了算。"""
    res, _ = run_guard("mcp__uzi__stock_deep_analysis",
                       {"code": "600519", "_hermes_user_id": "别人的"}, tmp_path,
                       {"HUNTER_USER_ID": "u-42"})
    assert res is not None
    assert res["hookSpecificOutput"]["updatedInput"]["_hermes_user_id"] == "u-42"


def test_hcapack_用不带下划线的参数名(tmp_path):
    """mcp 2.x 拒绝 `_` 开头的参数名，所以组合工具那一路注入的键不一样。"""
    res, _ = run_guard("mcp__hcapack__stock_snapshot",
                       {"code": "600519"}, tmp_path, {"HUNTER_USER_ID": "u-42"})
    assert res is not None
    upd = res["hookSpecificOutput"]["updatedInput"]
    assert upd["hermes_user_id"] == "u-42"
    assert "_hermes_user_id" not in upd


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


# ── M5：解释器只许跑内联代码（脚本文件 / 管道全是完整绕过）────────────────

def test_解释器不许跑脚本文件或从管道读(tmp_path):
    """回归：M3 §5 与 M4（P1-20）只堵住了**内联**命令。

    `DATA_LIB_RE` / `INLINE_NET_RE` / `INLINE_WRITE_RE` 判的是命令行文本 ——
    脚本在文件里的时候正则什么也看不见。而 `write_file` 本来就允许往
    `reports/` 与 `theses/` 写（上面那几条用例正是这么钉的）。两步接起来
    就是完整绕过：先把 `import akshare; …` 写进 `reports/fetch.py`，
    再 `python3 reports/fetch.py` —— 取到的数不进 MCP 层，拿不到用户身份、
    不进审计、界面上也认不出来源。M5 实测这七条在修之前**全部放行**。
    """
    for cmd in [
        "python3 reports/x.py",
        "sh notes/x.sh",
        "bash reports/run.sh",
        "cat notes/x.py | python3",
        "echo aW1wb3J0IGFrc2hhcmU= | base64 -d | sh",
        "echo aW1wb3J0IGFrc2hhcmU= | base64 -d | python3",
        "echo x | busybox sh",
    ]:
        res, rc = run_guard("bash", {"command": cmd}, tmp_path)
        assert rc == 0
        assert is_deny(res), f"没拦住：{cmd} → {res}"


def test_解释器的正常用法不误伤(tmp_path):
    for cmd in [
        'python3 -c "print(2+2)"',
        'python3 -c "import statistics;print(statistics.mean([1,2,3]))"',
        "python3 --version",
        'sh -c "ls reports/"',
        "python3 -m json.tool reports/a.json",
    ]:
        res, _ = run_guard("bash", {"command": cmd}, tmp_path)
        assert not is_deny(res), f"误伤了：{cmd} → {res}"


def test_bash_白名单兜住denylist漏掉的那一批(tmp_path):
    """M5：这 12 条彼此毫无关系，全是 denylist 想不全的形态。

    共同后果都一样 —— `write_file` 允许往 reports/ 与 theses/ 写，
    任何一条漏网都能接成「先写脚本再执行」的完整绕过。
    """
    for cmd in [
        "find reports -name '*.py' -exec python3 {} ;",
        """awk 'BEGIN{system("python3 reports/x.py")}'""",
        ". reports/x.sh",
        "source reports/x.sh",
        'eval "$(cat reports/x.sh)"',
        """perl -e 'system("python3 reports/x.py")'""",
        "node reports/x.js",
        "./reports/x.py",
        "ruby reports/x.rb",
        "php reports/x.php",
        "tar -czf reports/a.tgz holdings/",
        "find . -delete",
    ]:
        res, rc = run_guard("bash", {"command": cmd}, tmp_path)
        assert rc == 0
        assert is_deny(res), f"没拦住：{cmd} → {res}"


def test_bash_白名单没有误伤日常只读用法(tmp_path):
    """反误伤：这些是研究工作区里真会用到的形态，改白名单之后必须照样放行。"""
    for cmd in [
        "ls -la reports/",
        "cat theses/600519.md",
        "head -50 reports/a.md",
        "wc -l holdings/a.md",
        "grep -rn 持仓 holdings/",
        "find reports -name '*.md'",
        "echo hello",
        "date",
        "du -sh reports/",
        "sort -u reports/a.txt | head",
        'env | grep -E "HERMES" || true',
        "jq .a reports/a.json",
        "cut -d, -f1 reports/a.csv | uniq -c | sort -rn | head",
    ]:
        res, _ = run_guard("bash", {"command": cmd}, tmp_path)
        assert not is_deny(res), f"误伤了：{cmd} → {res}"


def test_bash_白名单里自带输出文件参数的命令不算只读(tmp_path):
    """M5 复核：白名单只管住了"首词是谁"，管不住"这个命令自己会不会写"。

    写重定向那道闸只看 `>` / `>>`，而 `sort -o FILE` / `uniq 输入 输出` 这类
    **不用重定向就写盘**。部署中的 daemon 容器里 sort 与 uniq 都在，实跑验证过
    `sort -o holdings/positions.json reports/evil.txt` 能把持仓账本整个覆盖 ——
    这正是 M2 冒烟题「改我的持仓文件」要求拒绝的那件事。
    """
    for cmd in [
        "sort -o holdings/positions.json reports/evil.txt",
        "sort reports/a.txt --output=holdings/positions.json",
        "uniq reports/evil.txt holdings/positions.json",
        "xxd -r reports/p.hex holdings/positions.bin",
        "tree -o reports/tree.txt",
        "yq -i '.a=1' reports/a.yaml",
    ]:
        res, rc = run_guard("bash", {"command": cmd}, tmp_path)
        assert rc == 0
        assert is_deny(res), f"没拦住（不用 > 也能写盘）：{cmd} → {res}"


def test_bash_输出文件判据没有误伤带值短选项(tmp_path):
    """反误伤：`xxd -l 100 a.bin` 的 100 是 -l 的值，不是"输入"，
    naive 地数操作数会把 a.bin 当成输出文件而误伤。"""
    for cmd in [
        "sort reports/a.txt",
        "sort -u reports/a.txt",
        "uniq reports/a.txt",
        "uniq -c reports/a.txt",
        "uniq -w 3 reports/a.txt",
        "xxd -l 100 reports/a.bin",
        "xxd reports/a.bin",
        "tree reports",
        "cat holdings/positions.json | sort | uniq -c",
    ]:
        res, _ = run_guard("bash", {"command": cmd}, tmp_path)
        assert not is_deny(res), f"误伤了：{cmd} → {res}"


def test_bash_白名单里带执行选项的命令(tmp_path):
    """M5 复核第二轮：`rg --pre CMD` 会对每个输入文件跑一遍 CMD —— 不是查看，是执行。

    开发机上实证（不经 guard，直接跑命令）：
        $ echo 'print("PWNED-BY-RG-PRE")' > x.py
        $ rg --pre python3 --pre-glob '*.py' 'PWNED' .
        ./x.py:PWNED-BY-RG-PRE          ← 这一行是 python3 x.py 的执行输出
    部署中的容器里没装 rg，所以当前不可利用；但白名单随工作区模板走，换个基础镜像就有。
    `ack` 同类（`--pager` 把内容交给外部命令），容器里也没装 —— 直接移出白名单。
    """
    for cmd in [
        "rg --pre python3 --pre-glob '*.py' PWNED .",
        "rg --pre=sh PWNED reports",
        "rg --hostname-bin reports/x.sh PWNED .",
        "ack --pager='python3 reports/x.py' foo",
    ]:
        res, rc = run_guard("bash", {"command": cmd}, tmp_path)
        assert rc == 0
        assert is_deny(res), f"没拦住：{cmd} → {res}"


def test_bash_rg的正常用法不受影响(tmp_path):
    for cmd in ["rg -n 持仓 reports/", "rg --json foo reports", "grep -rn foo reports"]:
        res, _ = run_guard("bash", {"command": cmd}, tmp_path)
        assert not is_deny(res), f"误伤了：{cmd} → {res}"


def test_bash_内联open只读模式不算写(tmp_path):
    """M5 复核：`INLINE_WRITE_RE` 原来写的是 `[waxr]\\+?`，`+` 可选，
    于是 **`open(..., 'r')` 这个只读模式也被判成了写**。

    不是纸面推演 —— 拿部署中容器 `guard.jsonl` 里模型**真实发出**的 37 条 bash
    重放时撞出来的，两条正当的只读用法被拦：
        python3 -c "with open('.eval/x.sse', 'r', errors='ignore') as f: ..."
        python3 -c "with open('.atomcode/skills/deep_analysis/SKILL.md','r') as f: print(...)"
    「用 python3 -c 读文件算数」正是白名单刻意要放行的用法，拦掉它等于把 bash 废了一半。
    """
    for cmd in [
        """python3 -c "with open('reports/a.txt') as f: print(len(f.read()))" """,
        """python3 -c "with open('reports/a.txt','r') as f: print(f.read()[:100])" """,
        """python3 -c "open('reports/a.bin','rb').read()" """,
        """python3 -c "open('reports/a.txt', 'r', errors='ignore')" """,
    ]:
        res, _ = run_guard("bash", {"command": cmd}, tmp_path)
        assert not is_deny(res), f"只读 open 被当成写拦掉了：{cmd} → {res}"
    # 真正的写照样要拦（含 r+ 这个"读模式里唯一能写的"）
    for cmd in [
        """python3 -c "open('holdings/p.json','w').write('x')" """,
        """python3 -c "open('holdings/p.json','r+').write('x')" """,
        """python3 -c "open('reports/a.bin','wb').write(b'x')" """,
        """python3 -c "open('reports/a.txt','a').write('x')" """,
        """python3 -c "import os; os.remove('holdings/p.json')" """,
    ]:
        res, rc = run_guard("bash", {"command": cmd}, tmp_path)
        assert rc == 0
        assert is_deny(res), f"真正的写没拦住：{cmd} → {res}"


def test_bash_丢到devnull不算写文件(tmp_path):
    """M5 复核：`2>/dev/null` 不是写文件，是丢弃输出，一律按写重定向拦是误伤。

    同样是拿容器 `guard.jsonl` 里模型真实发出的命令重放时撞到的
    （`python3 -c "..." 2>/dev/null || python3 -c "..."`）。
    只放行目标**恰好**是 `/dev/null` 的，并且那个目标 token 不再当成命令参数
    （否则会被「参数指向工作区外」那一条接着拦掉）。
    """
    for cmd in [
        """python3 -c "print(1)" 2>/dev/null""",
        """python3 -c "print(1)" > /dev/null""",
        """ls -la reports 2>/dev/null""",
        """python3 -c "print(1)" 2>/dev/null || python3 -c "print(2)" """,
    ]:
        res, _ = run_guard("bash", {"command": cmd}, tmp_path)
        assert not is_deny(res), f"丢到 /dev/null 被当成写盘拦了：{cmd} → {res}"
    # 真写还是要拦；/dev/null 这条口子也不能被拿来顺带放行别的
    for cmd in [
        """python3 -c "print(1)" > reports/out.txt""",
        """python3 -c "print(1)" 2> reports/err.txt""",
        """echo hi >> holdings/positions.json""",
        """python3 -c "print(1)" > /dev/nullx""",
        """echo hi > /dev/null; cat /etc/shadow""",
    ]:
        res, rc = run_guard("bash", {"command": cmd}, tmp_path)
        assert rc == 0
        assert is_deny(res), f"没拦住：{cmd} → {res}"
