"""I2 · guard 身份反查的两处提速 —— 每一次工具调用都要跑，所以值得单独盯。

两条都是在容器里实测出来的：

1. `import urllib.request` 在这台机器上要 **530 ms**（`python -X importtime`，
   累计 532 ms，主要开销在 http.client → email.parser 那一串）。原先的顺序是
   先 import 再查缓存，缓存命中率再高也照样每次付这 530 ms。
2. 没有会话归属记录的会话（评测探针自己 `POST /sessions` 建的、运维手工发起的），
   每次工具调用都要重新打一遍 api 拿 404，然后照样落到 `HUNTER_USER_ID` 兜底。

两条都必须**不改变判定结果** —— 用例里连身份注入的语义一起盯住。
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

GUARD_PATH = Path(__file__).resolve().parents[2] / "distro" / "workspace-template" / ".hooks" / "guard.py"
_spec = importlib.util.spec_from_file_location("hca_guard", GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
sys.modules["hca_guard"] = guard
_spec.loader.exec_module(guard)


class TestIdentityCache(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def _touch(self, sid: str, delta: float):
        path = guard._cache_path(self.ws)
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        data[sid]["at"] += delta
        Path(path).write_text(json.dumps(data), encoding="utf-8")

    def test_缓存命中时不import_urllib(self):
        guard._cache_write(self.ws, "sid-1", "uid-abc")
        removed = {k: sys.modules.pop(k) for k in
                   ("urllib.request", "urllib.error", "urllib.parse")
                   if k in sys.modules}
        try:
            with mock.patch.object(guard, "HERMES_API_URL", "http://api:8000"), \
                 mock.patch.object(guard, "HUNTER_INTERNAL_KEY", "k"):
                self.assertEqual(guard.lookup_user("sid-1", self.ws), "uid-abc")
            self.assertNotIn("urllib.request", sys.modules,
                             "缓存命中还 import 了 urllib.request —— 那 530 ms 又白付了")
        finally:
            sys.modules.update(removed)

    def test_404写负缓存_不再重复打接口(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 404, "not found", {}, None)

        with mock.patch.object(guard, "HERMES_API_URL", "http://api:8000"), \
             mock.patch.object(guard, "HUNTER_INTERNAL_KEY", "k"), \
             mock.patch("urllib.request.urlopen", fake_urlopen):
            for _ in range(3):
                self.assertEqual(guard.lookup_user("sid-404", self.ws), "")
        self.assertEqual(len(calls), 1, "404 之后还在反复打接口 —— 负缓存没生效")

    def test_网络错误不写负缓存(self):
        """临时故障不能被记成「这个会话没有身份」—— 下一次还得问。"""
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            raise OSError("connection refused")

        with mock.patch.object(guard, "HERMES_API_URL", "http://api:8000"), \
             mock.patch.object(guard, "HUNTER_INTERNAL_KEY", "k"), \
             mock.patch("urllib.request.urlopen", fake_urlopen):
            guard.lookup_user("sid-neterr", self.ws)
            guard.lookup_user("sid-neterr", self.ws)
        self.assertEqual(len(calls), 2)

    def test_负缓存过期后重新问(self):
        guard._cache_write(self.ws, "sid-x", "")
        self.assertEqual(guard._cache_read(self.ws, "sid-x"), "")
        self._touch("sid-x", -(guard.LOOKUP_NEG_TTL_S + 5))
        self.assertIsNone(guard._cache_read(self.ws, "sid-x"))

    def test_正缓存的TTL比负缓存长(self):
        guard._cache_write(self.ws, "sid-y", "uid-y")
        self._touch("sid-y", -(guard.LOOKUP_NEG_TTL_S + 5))
        self.assertEqual(guard._cache_read(self.ws, "sid-y"), "uid-y",
                         "正缓存被负缓存的短 TTL 误伤了")

    def test_没有记录返回None而不是空串(self):
        """None（该去问）和 ''（刚问过、没有）必须分得开。"""
        self.assertIsNone(guard._cache_read(self.ws, "从来没见过的会话"))

    def test_查到身份仍然正常写正缓存(self):
        def fake_urlopen(req, timeout=None):
            class R:
                def read(self_inner):
                    return json.dumps({"user_id": "u-real"}).encode()

                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *a):
                    return False
            return R()

        with mock.patch.object(guard, "HERMES_API_URL", "http://api:8000"), \
             mock.patch.object(guard, "HUNTER_INTERNAL_KEY", "k"), \
             mock.patch("urllib.request.urlopen", fake_urlopen):
            self.assertEqual(guard.lookup_user("sid-ok", self.ws), "u-real")
        self.assertEqual(guard._cache_read(self.ws, "sid-ok"), "u-real")


if __name__ == "__main__":
    unittest.main()
