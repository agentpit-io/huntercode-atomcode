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


def test_日期的紧凑写法也算命中但要标注(tmp_path):
    """`watchlist_stock_news` 的 date 字段实测全是空串，日期只在 URL 里。"""
    out = _write(tmp_path, "公告日期 2026-09-19。",
                 [("watchlist_stock_news",
                   '{"date":"","url":"http://stock.eastmoney.com/a/202609193879940651.html"}')])
    assert "只匹配到紧凑写法 20260919" in out
    assert "0 个没在工具返回里找到" in out


# ── 四舍五入匹配（M2 正式批次加的）──────────────────────────────────────
# 为什么加：筛选类工具回的是 `33.2451371632829`，模型按规范写 `33.25`。
# 只做逐字符匹配的话 q3 两边各有 9 个这种「未命中」，全是噪声，
# 真正该人看的那一两个（如 q2 里凭记忆补出来的 268.95）反而被埋掉。
from audit_numbers import floats_of, rounds_to  # noqa: E402


def test_floats_of_取出工具返回里的数():
    pool = floats_of('{"roe": 33.2451371632829, "pe": 19.25573258395184}')
    assert 33.2451371632829 in pool
    assert 19.25573258395184 in pool


def test_全精度值按两位小数四舍五入能命中():
    pool = floats_of('{"return_on_equity": 33.2451371632829}')
    assert rounds_to("33.25", pool)


def test_进位的那一侧也能命中():
    # 19.25573… 的两位四舍五入是 19.26，不是 19.25
    pool = floats_of('{"pe": 19.25573258395184}')
    assert rounds_to("19.26", pool)
    assert not rounds_to("19.25", pool)


def test_精度不同不算命中():
    # 三位小数就对不上了，不能因为"差不多"就放过
    pool = floats_of('{"x": 33.2451371632829}')
    assert not rounds_to("33.246", pool)


def test_整数也按零位小数比():
    assert rounds_to("313", floats_of('{"matched": 313}'))
    assert not rounds_to("314", floats_of('{"matched": 313}'))


def test_带千分位的正文写法():
    assert rounds_to("5,237", floats_of('{"universe_total": 5237}'))


def test_工具返回里没有的数不会被四舍五入蒙混过去():
    # q2 那个凭记忆补出来的 268.95：工具返回里只有年度数据，没有任何数四舍五入等于它
    pool = floats_of('{"2025": 528.49, "2024": 558.05}')
    assert not rounds_to("268.95", pool)


def test_非数字_token_不抛异常():
    assert not rounds_to("-", [1.0])
    assert not rounds_to("", [1.0])


# ── 单位换算匹配（正式批次上误判过一次 A1 才加的）──────────────────────
from audit_numbers import scales_to  # noqa: E402


def test_元换算成亿元能命中():
    # 真实来源：`扣除非经常性损益后的净利润(元)`: 26895000000 → 正文写 268.95 亿元
    assert scales_to("268.95", floats_of('{"扣非净利润(元)": 26895000000}')) == "亿"


def test_元换算成万元能命中():
    assert scales_to("1.5", floats_of('{"x": 15000}')) == "万"


def test_换算后对不上就不算命中():
    assert scales_to("268.95", floats_of('{"x": 12345}')) is None


def test_换算也要按正文的小数位数四舍五入():
    # 26895123456 / 1e8 = 268.95123… → 两位是 268.95，三位是 268.951
    pool = floats_of('{"x": 26895123456}')
    assert scales_to("268.95", pool) == "亿"
    assert scales_to("268.96", pool) is None


def test_零不参与换算():
    # 不然任何 0 都能"换算命中"，白白放过一个该看的数
    assert scales_to("0", floats_of('{"x": 0}')) is None


def test_非数字不抛异常():
    assert scales_to("—", [1.0]) is None
