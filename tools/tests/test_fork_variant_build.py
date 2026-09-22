"""跑 fork 变体时，`up.sh` 的构建**不许**把 fork 那一层覆盖掉。

香港那台真出过这件事，而且完全静默：

  · `deploy/.env` 里 `HCA_DAEMON_VARIANT=-fork`；
  · daemon 服务的 `image:` 是 `hca-daemon:${HCA_IMAGE_TAG}${HCA_DAEMON_VARIANT}`，
    而 compose 里 `image:` **同时是构建产物的 tag**；
  · 于是 `docker compose build daemon` 把**官方二进制那份 Dockerfile 的产物**
    打成了 `hca-daemon:dev-fork` —— tag 还叫 fork，里面已经是官方二进制。
    `/health` 会老老实实回官方那个 binary_hash，但没人会去看。

这里钉三件事（都只读脚本文本，不起容器）：
  1. 构建那两行必须把 `HCA_DAEMON_VARIANT` 清空；
  2. 构建之后必须有「fork 层比基础镜像旧」的提醒；
  3. compose 里 daemon 的 `image:` 仍然带变体（启动时要用它）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UP = (REPO / "deploy" / "up.sh").read_text(encoding="utf-8")
COMPOSE = (REPO / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
SWITCH = (REPO / "deploy" / "switch-to-fork.sh").read_text(encoding="utf-8")


class ForkVariantBuildCase(unittest.TestCase):
    def test_构建命令把变体清空(self):
        builds = [ln for ln in UP.splitlines()
                  if "docker compose" in ln and " build" in ln]
        self.assertTrue(builds, "up.sh 里找不到 docker compose build 那几行")
        for ln in builds:
            self.assertIn("HCA_DAEMON_VARIANT=", ln,
                          f"这一行构建没清空变体，会把 fork 层覆盖掉：{ln.strip()}")
            # 必须是「清空」而不是「传进去」
            self.assertRegex(ln, r"HCA_DAEMON_VARIANT=\s",
                             f"变体要清空（`HCA_DAEMON_VARIANT= `），不是赋值：{ln.strip()}")

    def test_构建之后会提醒_fork_层旧了(self):
        self.assertIn("HCA_DAEMON_VARIANT:-}", UP)
        self.assertRegex(UP, r"fork 那一层.*比基础镜像.*旧|比基础镜像（.*）旧")
        self.assertIn("switch-to-fork.sh", UP, "提醒里要写清怎么修")

    def test_compose_里启动用的镜像仍然带变体(self):
        self.assertRegex(
            COMPOSE, r"image:\s*hca-daemon:\$\{HCA_IMAGE_TAG:-dev\}\$\{HCA_DAEMON_VARIANT:-\}")

    def test_switch_to_fork_会验证生效(self):
        # 只写 .env 不验证的话，「配了没生效」这件事没人会发现（官方二进制读不到这两项）
        self.assertIn("config.toml", SWITCH)
        self.assertIn("system_prompt_file", SWITCH)
        self.assertRegex(SWITCH, r"exit 3|✗ config.toml")

    def test_变体默认为空_也就是默认官方二进制(self):
        env = (REPO / "deploy" / "env.example").read_text(encoding="utf-8")
        m = re.search(r"^HCA_DAEMON_VARIANT=(.*)$", env, re.M)
        self.assertIsNotNone(m, "env.example 里应当有这个键（留空 = 官方二进制）")
        self.assertEqual("", m.group(1).strip())


if __name__ == "__main__":
    unittest.main()
