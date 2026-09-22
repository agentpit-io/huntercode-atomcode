"""A/B 评测的「拒绝开跑」闸 —— 什么情况下不该把这一次运行算进 30 次里。

背景：q1 试跑（`docs/eval/pilot-q1/`）里 5 个 MCP 没连上，探针照样把题发了出去，
拿回一份只有 4 个数据源的答案。这种记录混进正式批次，比的就不是两个 agent 了。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
from eval_atomcode import refuse_reason  # noqa: E402


def test_一切正常时不拦():
    assert refuse_reason("sid-1", "") is None


def test_mcp_没全连上要拦():
    err = "第 5 轮 reload 后仍未连上：['akshare', 'watchlist']"
    why = refuse_reason("sid-1", err)
    assert why and "MCP" in why


def test_切不到会话要拦():
    why = refuse_reason(None, "switch_session 被拒：{'ok': False}")
    assert why and "干净会话" in why


def test_unbound_是良性不拦():
    err = "switch_session 恒为 Unbound（daemon 还没绑过会话，视为已是干净会话）：{}"
    assert refuse_reason(None, err) is None


def test_关掉开关后一律不拦():
    err = "第 5 轮 reload 后仍未连上：['akshare']"
    assert refuse_reason(None, err, require_mcp=False) is None


def test_直接数connected也能拦():
    """第一道闸（看 switch_err）曾经被控制流绕过去 —— 所以有第二道。"""
    why = refuse_reason("sid-1", "", mcp_ok=4, mcp_all=9)
    assert why and "4/9" in why


def test_全连上时第二道不拦():
    assert refuse_reason("sid-1", "", mcp_ok=9, mcp_all=9) is None


def test_取不到mcp状态时第二道不拦():
    """`/mcp/status` 请求本身失败时 mcp_ok/mcp_all 是 None —— 不猜、不拦。"""
    assert refuse_reason("sid-1", "", mcp_ok=None, mcp_all=None) is None
