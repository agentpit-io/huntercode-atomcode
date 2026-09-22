"""I2 · hook 常驻服务（hookd.py）+ bash 客户端（hook_client.sh）。

这一层只为省时间，**不许改变任何判定**。所以用例的主线是一条：
同一份 payload，走常驻服务和走原来的「起一个 python」，**输出必须逐字节相同** ——
尤其是 guard 的 deny，那是本发行版唯一的硬约束。

另外盯住三件事：
  · 空输出（guard 放行）不能被客户端当成「服务坏了」而退回去重跑；
  · 服务不在时客户端必须照常跑出正确结果（fail-safe，不是 fail-open）；
  · 服务内部出错时**不能**回一个「放行」。
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOKS = REPO / "distro" / "workspace-template" / ".hooks"
HOOKD = HOOKS / "hookd.py"
CLIENT = HOOKS / "hook_client.sh"

PAYLOADS = {
    "放行的只读工具": {
        "hook_event_name": "PreToolUse", "session_id": "t1", "cwd": "{ws}",
        "tool_name": "read_file", "tool_input": {"file_path": "theses/600519.md"}},
    "拒绝的越界写": {
        "hook_event_name": "PreToolUse", "session_id": "t1", "cwd": "{ws}",
        "tool_name": "write_file", "tool_input": {"file_path": "/etc/passwd", "content": "x"}},
    "拒绝的取数命令": {
        "hook_event_name": "PreToolUse", "session_id": "t1", "cwd": "{ws}",
        "tool_name": "bash", "tool_input": {"command": "python3 -c \"import akshare\""}},
    "拒绝的工作区外读": {
        "hook_event_name": "PreToolUse", "session_id": "t1", "cwd": "{ws}",
        "tool_name": "read_file", "tool_input": {"file_path": "../../etc/shadow"}},
}


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class HookdCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.env = {**os.environ, "HCA_HOOKD_PORT": str(cls.port),
                   "HCA_WORKSPACE": str(REPO / "distro" / "workspace-template"),
                   "HERMES_API_URL": "", "HUNTER_INTERNAL_KEY": ""}
        cls.proc = subprocess.Popen([sys.executable, str(HOOKD)], env=cls.env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            cls.proc.kill()
            raise AssertionError("hookd 没起来")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    def direct(self, script: str, payload: dict) -> str:
        p = subprocess.run([sys.executable, str(HOOKS / script)],
                           input=json.dumps(payload, ensure_ascii=False).encode(),
                           capture_output=True, env=self.env)
        return p.stdout.decode()

    def via_client(self, name: str, script: str, payload: dict, env_extra=None) -> str:
        p = subprocess.run(["bash", str(CLIENT), name, script],
                           input=json.dumps(payload, ensure_ascii=False).encode(),
                           capture_output=True, env={**self.env, **(env_extra or {})})
        return p.stdout.decode()

    # ── 主线：两条路径的判定必须一致 ────────────────────────────────────
    def test_guard_两条路径判定一致(self):
        ws = str(REPO / "distro" / "workspace-template")
        for label, tpl in PAYLOADS.items():
            with self.subTest(label):
                payload = {**tpl, "cwd": ws}
                a = json.loads(self.direct("guard.py", payload) or "null")
                b = json.loads(self.via_client("guard", "guard.py", payload) or "null")
                self.assertEqual(a, b, f"{label}：常驻服务与直跑的判定不一致")

    def test_deny的确是deny(self):
        """别测了半天两边一致，其实两边都放行。"""
        ws = str(REPO / "distro" / "workspace-template")
        for label in ("拒绝的越界写", "拒绝的取数命令", "拒绝的工作区外读"):
            with self.subTest(label):
                out = self.via_client("guard", "guard.py", {**PAYLOADS[label], "cwd": ws})
                d = json.loads(out)
                self.assertEqual(
                    d["hookSpecificOutput"]["permissionDecision"], "deny", out[:200])

    def test_放行时输出为空且不退回(self):
        ws = str(REPO / "distro" / "workspace-template")
        out = self.via_client("guard", "guard.py", {**PAYLOADS["放行的只读工具"], "cwd": ws})
        self.assertEqual(out, "", "放行本来就没有输出 —— 有输出说明退回路径被误触发了")

    def test_lang_两条路径一致(self):
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": "t", "prompt": "600519"}
        self.assertEqual(self.direct("lang.py", payload),
                         self.via_client("lang", "lang.py", payload))

    # ── 退回路径 ────────────────────────────────────────────────────────
    def test_服务不在时客户端照常跑出正确结果(self):
        ws = str(REPO / "distro" / "workspace-template")
        payload = {**PAYLOADS["拒绝的越界写"], "cwd": ws}
        out = self.via_client("guard", "guard.py", payload,
                              env_extra={"HCA_HOOKD_PORT": str(free_port())})
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny",
                         "服务不在就漏了 —— guard 必须 fail-safe")

    def test_开关关掉时直接走原路(self):
        ws = str(REPO / "distro" / "workspace-template")
        payload = {**PAYLOADS["拒绝的取数命令"], "cwd": ws}
        out = self.via_client("guard", "guard.py", payload, env_extra={"HCA_HOOKD": "0"})
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")

    # ── 服务端异常 ──────────────────────────────────────────────────────
    def test_未知hook名不回任何内容(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall("不存在的hook\n{}".encode())
        self.assertEqual(s.makefile("rb").read(), b"")
        s.close()

    def test_payload不是JSON时与直跑一致(self):
        """guard 自己对解析不了的输入就是「放行」（见 guard.py main 的注释）。
        这里要的不是「服务端报错」，而是**两条路径的行为一样**。"""
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall("guard\n这不是 JSON".encode())
        s.shutdown(socket.SHUT_WR)
        body = s.makefile("rb").read()
        s.close()
        direct = subprocess.run([sys.executable, str(HOOKS / "guard.py")],
                                input="这不是 JSON".encode(),
                                capture_output=True, env=self.env).stdout
        self.assertEqual(body, b"OK\n" + direct)
        self.assertEqual(direct, b"", "guard 对解析不了的输入本来就是不输出")

    def test_hook模块抛异常时不回状态行(self):
        """真正的「服务端坏了」：模块抛异常。这时**绝不能**回 OK ——
        回了就等于把一次未判定变成了放行。客户端看到没有状态行会退回去重跑。"""
        import importlib.util
        spec = importlib.util.spec_from_file_location("hookd_under_test", HOOKD)
        hookd = importlib.util.module_from_spec(spec)
        sys.modules["hookd_under_test"] = hookd
        spec.loader.exec_module(hookd)

        class Boom:
            @staticmethod
            def main():
                raise RuntimeError("炸了")

        hookd._mods["guard"] = Boom
        with self.assertRaises(RuntimeError):
            hookd.run_hook("guard", "{}")

    def test_hook调用sys_exit非零时算失败(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("hookd_exit_test", HOOKD)
        hookd = importlib.util.module_from_spec(spec)
        sys.modules["hookd_exit_test"] = hookd
        spec.loader.exec_module(hookd)

        class Exiter:
            @staticmethod
            def main():
                raise SystemExit(3)

        class CleanExiter:
            @staticmethod
            def main():
                print("正常输出")
                raise SystemExit(0)

        hookd._mods["guard"] = Exiter
        with self.assertRaises(RuntimeError):
            hookd.run_hook("guard", "{}")
        hookd._mods["guard"] = CleanExiter
        self.assertEqual(hookd.run_hook("guard", "{}"), "正常输出\n")

    def test_大payload不会拖垮服务(self):
        ws = str(REPO / "distro" / "workspace-template")
        payload = {"hook_event_name": "PostToolUse", "session_id": "t", "cwd": ws,
                   "tool_name": "mcp__watchlist__stock_news", "tool_input": {"code": "600519"},
                   "tool_response": {"output": "x" * 200_000}}
        self.assertEqual(self.direct("audit.py", payload),
                         self.via_client("audit", "audit.py", payload))

    def test_并发请求互不串扰(self):
        ws = str(REPO / "distro" / "workspace-template")
        results, errs = [], []

        def work(i):
            try:
                label = "拒绝的越界写" if i % 2 else "放行的只读工具"
                results.append((label, self.via_client("guard", "guard.py",
                                                       {**PAYLOADS[label], "cwd": ws})))
            except Exception as e:                      # noqa: BLE001
                errs.append(e)

        ts = [threading.Thread(target=work, args=(i,)) for i in range(16)]
        [t.start() for t in ts]
        [t.join(60) for t in ts]
        self.assertFalse(errs, errs)
        self.assertEqual(len(results), 16, "有请求没回来")
        for label, out in results:
            if label == "放行的只读工具":
                self.assertEqual(out, "", "放行的请求拿到了别人的输出")
            else:
                self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"],
                                 "deny")


if __name__ == "__main__":
    unittest.main()


class TestInterpreterParity(unittest.TestCase):
    """两条路径必须是**同一个解释器** —— 否则 context.py 的 `import akshare`
    在常驻服务这条路上会悄无声息地失败（akshare 只装在 /opt/hca/venv 里），
    表现是注入的上下文里少了「今天是不是交易日」那一行，而没有任何报错。"""

    def test_entrypoint_用HCA_PYTHON起hookd(self):
        src = (REPO / "deploy" / "daemon" / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn('"${HCA_PYTHON:-python3}" "${WORKSPACE}/.hooks/hookd.py"', src)

    def test_客户端退回路径也用HCA_PYTHON(self):
        src = CLIENT.read_text(encoding="utf-8")
        self.assertIn('PY="${HCA_PYTHON:-python3}"', src)

    def test_hookd只听回环(self):
        """绑 0.0.0.0 就等于把 guard 的判定入口暴露到容器网络。"""
        src = HOOKD.read_text(encoding="utf-8")
        self.assertIn('HOST = "127.0.0.1"', src)
        # 掐掉 docstring 与所有注释（含行尾注释）之后不该再出现 0.0.0.0 ——
        # 文件里那两处都是「绝不能绑 0.0.0.0」的告诫，不是真的绑上去了
        code = src.split('"""', 2)[-1]
        code = "\n".join(re.sub(r"#.*$", "", l) for l in code.splitlines())
        self.assertNotIn("0.0.0.0", code)
