"""I2 · `HCA_MCP_DISABLE`（MCP 子集开关）的单测。

两条底线：
  · **不设开关时一个字都不改** —— 原样铺过去，注释与 ${VAR} 都留着由 AtomCode 展开。
  · 剥注释不能把 URL 里的 `//` 当成注释剥掉（`https://…` 是最容易踩的那个）。
"""
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("hca_init", REPO / "deploy" / "daemon" / "hca-init.py")
hca_init = importlib.util.module_from_spec(spec)
sys.modules["hca_init"] = hca_init
spec.loader.exec_module(hca_init)

SAMPLE = '''{
  // 注释：这一行整行都该被剥掉
  "mcpServers": {
    "akshare":   {"command": "/opt/hca/venv/bin/akshare-mcp", "timeout_ms": 120000},
    "watchlist": {"command": "/opt/hca/venv-hunter/bin/python",
                  "env": {"HERMES_API_URL": "${HERMES_API_URL:-http://api:8000}"}},
    "kronos":    {"command": "/opt/hca/venv/bin/kronos-mcp"}  // 行尾注释
  }
}'''


class TestFilterMcp(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("HCA_MCP_DISABLE")
        os.environ.pop("HCA_MCP_DISABLE", None)

    def tearDown(self):
        os.environ.pop("HCA_MCP_DISABLE", None)
        if self._old is not None:
            os.environ["HCA_MCP_DISABLE"] = self._old

    def test_不设开关时原样返回(self):
        text, removed = hca_init.filter_mcp(SAMPLE)
        self.assertEqual(text, SAMPLE)
        self.assertEqual(removed, [])

    def test_去掉指定的server(self):
        os.environ["HCA_MCP_DISABLE"] = "akshare,kronos"
        text, removed = hca_init.filter_mcp(SAMPLE)
        self.assertEqual(sorted(removed), ["akshare", "kronos"])
        obj = json.loads(text)
        self.assertEqual(list(obj["mcpServers"]), ["watchlist"])

    def test_保留没被点名的server的完整配置(self):
        os.environ["HCA_MCP_DISABLE"] = "akshare"
        obj = json.loads(hca_init.filter_mcp(SAMPLE)[0])
        # ${VAR} 必须原样留着 —— 展开是 AtomCode 的事，预渲染会把密钥写进工作区
        self.assertEqual(obj["mcpServers"]["watchlist"]["env"]["HERMES_API_URL"],
                         "${HERMES_API_URL:-http://api:8000}")

    def test_点名不存在的server不炸(self):
        os.environ["HCA_MCP_DISABLE"] = "不存在的"
        text, removed = hca_init.filter_mcp(SAMPLE)
        self.assertEqual(removed, [])
        self.assertEqual(list(json.loads(text)["mcpServers"]), ["akshare", "watchlist", "kronos"])

    def test_剥注释不碰字符串里的双斜杠(self):
        src = '{"mcpServers": {"a": {"url": "https://x.example.com/v1"}}}  // 尾注释'
        os.environ["HCA_MCP_DISABLE"] = "b"
        obj = json.loads(hca_init.filter_mcp(src)[0])
        self.assertEqual(obj["mcpServers"]["a"]["url"], "https://x.example.com/v1")

    def test_解析不了就原样返回而不是把文件写坏(self):
        os.environ["HCA_MCP_DISABLE"] = "akshare"
        broken = '{"mcpServers": {'
        text, removed = hca_init.filter_mcp(broken)
        self.assertEqual(text, broken)
        self.assertEqual(removed, [])

    def test_真实的mcp_json能被剥注释并解析(self):
        real = (REPO / "distro" / "workspace-template" / ".mcp.json").read_text(encoding="utf-8")
        obj = json.loads(hca_init._strip_jsonc(real))
        self.assertEqual(len(obj["mcpServers"]), 9)
        os.environ["HCA_MCP_DISABLE"] = "akshare,kronos,truesource,screener,hunter_cap"
        text, removed = hca_init.filter_mcp(real)
        self.assertEqual(len(removed), 5)
        self.assertEqual(sorted(json.loads(text)["mcpServers"]),
                         ["hunter_user", "portfolio", "uzi", "watchlist"])


if __name__ == "__main__":
    unittest.main()
