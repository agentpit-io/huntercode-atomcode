"""钉住 `web_turn.py` 判「这一轮成不成功」的判据。

**为什么这条判据值得单独测**：它不是测试代码，它是 `docs/stability-report.md`
运维建议里推荐的**每日探活**的判据。判据坏了，表现是「模型通道整个断了、探活还报绿」，
而这恰恰是探活唯一要防的事。

用例里的两条来自 M5 的真实记录（Ollama 通道，`docs/开发文档/M5-成果与测试报告.md` §3.12）。
"""
import importlib.util
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]


def _judge():
    spec = importlib.util.spec_from_file_location("hca_web_turn", TOOLS / "e2e" / "web_turn.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.judge_turn


judge_turn = _judge()


def test_真实记录_provider_error算失败():
    """第一次跑 Ollama 通道时的真实记录：HTTP 200、终态收到、正文 0 字。

    旧判据（`ok = st == 200`）打出的是「1 题，成功 1，失败 0」。
    """
    ok, why = judge_turn(200, "provider_error", 0, 0)
    assert ok is False
    assert "provider_error" in why


def test_真实记录_接通之后算成功():
    """改绑 ollama 监听地址之后同一条题：203.9 s、172 字、stop_reason=stopped。"""
    ok, why = judge_turn(200, "stopped", 172, 0)
    assert ok is True and why is None


def test_只调工具没写正文不算失败():
    """多轮任务里这一步很常见，判据不能把它判成失败。"""
    ok, why = judge_turn(200, "stopped", 0, 1)
    assert ok is True and why is None


def test_既没正文也没工具算失败():
    ok, why = judge_turn(200, "stopped", 0, 0)
    assert ok is False and "既没有正文也没有工具调用" in why


@pytest.mark.parametrize("sr", ["error", "failed", "provider_error", "upstream_error"])
def test_各种错误终态都算失败(sr):
    ok, why = judge_turn(200, sr, 999, 9)
    assert ok is False, sr


def test_HTTP不是200一律失败():
    ok, why = judge_turn(500, "stopped", 999, 9)
    assert ok is False and "HTTP 500" in why
