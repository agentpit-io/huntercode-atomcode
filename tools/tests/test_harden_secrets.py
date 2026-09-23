"""I3 · `deploy/up.sh` 的 `harden_secrets` —— 早退判据必须连**文件**一起看。

这条用例是**真部署撞出来的**，不是想出来的：

I3 在测试机上重建 daemon 之后，第一轮真实对话被网关回 401。查下去是
`~/hca/deploy-secrets/llm-key` 的**组**是宿主用户的组（不是容器的 10001），
daemon（uid 10001）读不到，`[hca-init] … key=（空）`。
而 `harden_secrets` 早退的判据**只看目录**：目录之前已经 chgrp 过一次，
于是「目录已经对了 → 直接 return」，**后来放进去的那把 key 永远改不过来**。

这个 bug 最难发现的地方在于：`up.sh` 的自检**是过的** ——
自检那一跳（`GET /models` 经 web→daemon）不需要模型 key，
所以六个容器全 healthy、自检全绿，只有真发一句话才会炸。

用例用真实的临时目录跑真实的那个 shell 函数（把 `harden_secrets` 从
`up.sh` 里摘出来 source，不起容器、不用 sudo —— 走的是它最后那条
「退成 0755/0644」的兜底分支，判的是**它有没有决定动手**）。
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UP = REPO / "deploy" / "up.sh"


def extract_fn(name: str) -> str:
    """把一个 shell 函数从 up.sh 里摘出来（从 `name() {` 到列首的 `}`）。"""
    text = UP.read_text(encoding="utf-8")
    m = re.search(rf"^{re.escape(name)}\(\) \{{\n(.*?)^\}}\n", text, re.S | re.M)
    assert m, f"{name} 没在 {UP} 里找到"
    return f"{name}() {{\n{m.group(1)}}}\n"


HARNESS = """
set -u
log() { printf '%s\\n' "$*"; }
# 不让它真去 sudo / 起容器：把两条都堵死，逼它走最后的兜底分支。
# 这条用例判的是**它有没有决定动手**，不是它用哪条路动手。
sudo() { return 1; }
docker() { return 1; }
{fn}
harden_secrets "$1"
"""


def run(dirpath: str, gid: int | None = None) -> str:
    """`gid` 给值时把函数里写死的 10001 换成它。

    为什么要允许换：验「都对了就早退」那条路必须让目录与文件的组**真的**等于
    判据里的那个 gid，而普通用户 chgrp 不到 10001。换成用例自己的 gid 之后，
    走的是**同一段代码、同一个判据**，只是那个常数不同。
    """
    fn = extract_fn("harden_secrets")
    if gid is not None:
        assert "local gid=10001" in fn
        fn = fn.replace("local gid=10001", f"local gid={gid}")
    r = subprocess.run(["bash", "-c", HARNESS.replace("{fn}", fn), "bash", dirpath],
                       capture_output=True, text=True, timeout=60)
    return r.stdout + r.stderr


class HardenSecretsCase(unittest.TestCase):
    def _dir(self, dir_mode: int, file_mode: int) -> str:
        d = tempfile.mkdtemp(prefix="hca-secrets-")
        f = Path(d, "llm-key")
        f.write_text("hunt_tools_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n", encoding="utf-8")
        os.chmod(f, file_mode)
        os.chmod(d, dir_mode)
        return d

    def test_目录对了但文件组不对时_不许早退(self):
        """核心用例：目录 gid 已经是 10001、模式 0750，但文件不是组可读。

        真部署里的形状是「文件的**组**不对」，而用例跑在普通用户下改不了组
        （chgrp 到 10001 要 root）。所以这里用**等价的可观测面**：
        `find ! -perm -040` —— 组读位没开，容器同样读不到。
        两者在判据里是同一个 `-o`，命中任何一个都必须继续往下走。
        """
        d = self._dir(0o750, 0o600)          # 文件 0600：组读不到
        os.chown(d, os.getuid(), os.getgid())
        out = run(d)
        self.assertNotIn("权限已对齐", out,
                         f"目录对了、文件读不到，却早退了。输出：\n{out}")
        self.assertIn("容器读不到", out, f"没报出是哪个文件。输出：\n{out}")
        self.assertEqual(0o644, os.stat(Path(d, "llm-key")).st_mode & 0o777,
                         "走到兜底分支却没有把文件改成可读")

    def test_目录和文件都对了才早退(self):
        """反向用例：都对了就不该再动手 —— 否则每次启动都白起一个容器。

        把判据里的 gid 换成用例自己的组（见 `run` 的 docstring），
        这样「目录组对 + 文件组对 + 组读位开」三条同时成立。
        """
        d = self._dir(0o750, 0o640)
        out = run(d, gid=os.getgid())
        self.assertIn("权限已对齐", out, f"三条都对了却还动手。输出：\n{out}")
        self.assertNotIn("容器读不到", out, f"没有坏文件却报了坏文件。输出：\n{out}")

    def test_只有文件组不对时也不许早退(self):
        """把 gid 换成用例自己的组，只让**其中一个文件**的组不对。

        这才是真部署里那个形状（目录早就 chgrp 过，后来放进去的 key 带着
        当前用户的组）。用 `os.setgid` 改不了别人的组，所以用「另一个组」——
        取用例进程的补充组里任意一个与主组不同的；没有就退化成用组读位那一半。
        """
        d = self._dir(0o750, 0o640)
        others = [g for g in os.getgroups() if g != os.getgid()]
        if others:
            os.chown(Path(d, "llm-key"), os.getuid(), others[0])
        else:
            os.chmod(Path(d, "llm-key"), 0o600)      # 退化：组读位关掉
        out = run(d, gid=os.getgid())
        self.assertNotIn("权限已对齐", out, f"有一个文件容器读不到，却早退了。输出：\n{out}")
        self.assertIn("llm-key", out, f"没有报出是哪个文件。输出：\n{out}")

    def test_早退那句日志把文件也算进去了(self):
        """日志措辞本身也钉一下：说的是「目录与其中的文件」，不是只有目录。

        这不是文字洁癖 —— 运维就是照着这句话判断「要不要再查一遍 key 能不能读」的。
        """
        self.assertIn("密钥目录与其中的文件权限已对齐", UP.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
