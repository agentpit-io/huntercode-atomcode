"""deploy/.env 里带空格的值必须能被 `set -a; . .env` 读进来，且不被截断。

香港那台升级到 I2 分支时炸的就是这一条：`deploy/env.example` 里
`HCA_LLM_MODEL_LABEL=hunter-chat=Gemini 3.8 Flash,...` 没有引号，
而 `up.sh` 读 .env 用的是 `set -a; . "$ENV_FILE"; set +a`（让 bash 解释这个文件）——
于是变成「K=Gemini，然后执行命令 3.8」，报 `3.8: command not found`，
`up.sh` 因为 `set -e` 直接失败，而且那个变量只拿到 `Gemini`（**静默截断**，
真正吓人的是这一半：网页上模型名会显示成半截）。

这里钉两件事：
  1. `env.example` 里所有带空格的值都带引号（照抄它的人不会踩坑）；
  2. `install.sh` 的 `set_env_kv` 写进去的值，带空格时自己加引号。
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO / "deploy" / "env.example"
INSTALL = REPO / "deploy" / "install.sh"


def sourceable(text: str) -> tuple[bool, str]:
    """把一段 .env 交给 bash 按 `set -a; . file` 的方式读一遍，返回（成功?，stderr）。"""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / ".env"
        f.write_text(text, encoding="utf-8")
        p = subprocess.run(["bash", "-c", f'set -a; . "{f}"; set +a'],
                           capture_output=True, text=True)
        return p.returncode == 0 and not p.stderr.strip(), p.stderr.strip()


class EnvQuotingCase(unittest.TestCase):
    def test_env_example_整份能被_bash_source(self):
        body = ENV_EXAMPLE.read_text(encoding="utf-8")
        ok, err = sourceable(body)
        self.assertTrue(ok, f"env.example 不能被 `. ` 读：{err}")

    def test_env_example_里带空格的值都带引号(self):
        bad = []
        for i, ln in enumerate(ENV_EXAMPLE.read_text(encoding="utf-8").splitlines(), 1):
            m = re.match(r"^([A-Z0-9_]+)=(.*)$", ln)
            if not m:
                continue
            val = m.group(2)
            if " " in val and not (val.startswith(("'", '"')) and val.endswith(("'", '"'))):
                bad.append(f"{i}: {m.group(1)}")
        self.assertEqual([], bad, f"这些行的值里有空格却没加引号：{bad}")

    def test_那个真实值读进来不被截断(self):
        # 回归用例就是香港那次炸掉的原文
        text = "HCA_LLM_MODEL_LABEL='hunter-chat=Gemini 3.8 Flash,hunter-deep=Gemini 3.1 Pro'\n"
        p = subprocess.run(
            ["bash", "-c", f'set -a; . /dev/stdin; set +a; printf "%s" "$HCA_LLM_MODEL_LABEL"'],
            input=text, capture_output=True, text=True)
        self.assertEqual("hunter-chat=Gemini 3.8 Flash,hunter-deep=Gemini 3.1 Pro", p.stdout)

    def test_没有引号的同一行会炸_证明这条测试抓得住(self):
        ok, err = sourceable("K=Gemini 3.8 Flash\n")
        self.assertFalse(ok, "没引号的值竟然读过去了 —— 那这条测试就没在测东西")
        self.assertIn("3.8", err)

    def test_set_env_kv_给带空格的值加引号(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / ".env"
            f.write_text("A=1\n", encoding="utf-8")
            # 只取 install.sh 里那个函数来跑，不跑整个安装脚本
            src = INSTALL.read_text(encoding="utf-8")
            start = src.index("set_env_kv() {")
            end = src.index("\n}\n", start) + 3
            func = src[start:end]
            script = f'{func}\nset_env_kv "{f}" LBL "hunter-chat=Gemini 3.8 Flash"\n'
            p = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(0, p.returncode, p.stderr)
            body = f.read_text(encoding="utf-8")
            ok, err = sourceable(body)
            self.assertTrue(ok, f"set_env_kv 写出来的 .env 读不了：{err}\n{body}")
            got = subprocess.run(
                ["bash", "-c", f'set -a; . "{f}"; set +a; printf "%s" "$LBL"'],
                capture_output=True, text=True).stdout
            self.assertEqual("hunter-chat=Gemini 3.8 Flash", got)


if __name__ == "__main__":
    sys.exit(unittest.main())
