"""A1 取证工具：正文里的数字能不能在本次工具返回里找到。

这个脚本不给分，只负责把「需要人看一眼的那几个」挑出来，所以测的是
**噪声有没有被挑掉、真该报的有没有漏报**。
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "eval" / "audit_numbers.py"
sys.path.insert(0, str(REPO / "tools" / "eval"))
from audit_numbers import norm, norm_all  # noqa: E402


def test_归一化去千分位与尾零():
    assert norm("1,252.57") == "1252.57"
    assert norm("1252.570") == "1252.57"
    assert norm("30.0") == "30"
    assert norm("-1.95") == "-1.95"


def test_工具返回也做同样的归一化():
    assert "1252.57" in norm_all('{"price": 1,252.570}')


def _write(tmp_path, text, tool_outputs):
    stem = tmp_path / "q1-x-atomcode-r1"
    stem.with_suffix(".json").write_text(
        json.dumps({"id": stem.name, "side": "atomcode", "text": text},
                   ensure_ascii=False), encoding="utf-8")
    lines = []
    for name, out in tool_outputs:
        lines.append("data: " + json.dumps(
            {"type": "tool_result", "name": name, "output": out}, ensure_ascii=False))
    stem.with_suffix(".sse").write_text("\n".join(lines) + "\n", encoding="utf-8")
    p = subprocess.run([sys.executable, str(TOOL), str(stem)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout


def test_工具返回里有的数不报(tmp_path):
    out = _write(tmp_path, "毛利率 89.56%，ROE 16.75%。",
                 [("mcp__uzi__x", '{"gross_margin": 89.56, "roe": 16.75}')])
    assert "0 个没在工具返回里找到" in out


def test_工具返回里没有的数要报(tmp_path):
    out = _write(tmp_path, "毛利率 89.56%，我给它打 68 分。",
                 [("mcp__uzi__x", '{"gross_margin": 89.56}')])
    assert "1 个没在工具返回里找到" in out
    assert "`68`" in out


def test_iso_日期整串审而不是拆成三段(tmp_path):
    """`2026-06-30` 不该被拆成 2026 / -06 / -30 报两个假缺失。"""
    out = _write(tmp_path, "数据截至 2026-06-30。",
                 [("mcp__uzi__x", '{"asof": "2026-06-30"}')])
    assert "0 个没在工具返回里找到" in out
    assert "`-06`" not in out and "`-30`" not in out


def test_只匹配到绝对值要标出来(tmp_path):
    """工具说「下滑 1.95%」、正文写「-1.95%」—— 算命中，但要让人看一眼。"""
    out = _write(tmp_path, "归母净利润同比 -1.95%。",
                 [("mcp__uzi__x", "净利润出现 1.95% 的下滑")])
    assert "只匹配到绝对值 1.95" in out
    assert "0 个没在工具返回里找到" in out
