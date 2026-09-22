"""MCP 大小闸单测（待办池 P0-11）。

钉死三件事：
  1. 没超预算的**一个字节都不许动**（绝大多数调用走这条，动了就是回归）
  2. 超了之后返回的**必须还是合法 JSON**，且说清楚裁掉了多少
     —— 内核那种「头 4 KB + 尾 4 KB」的砍法留下的是断裂的 JSON，
        模型看不出少了什么，就会拿记忆去补（M2 §4.5 实测过）
  3. 单条就超预算时也不能返回半截结构，要包一层并写明「别推断」
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "opencode-mcp"))
from hca_size_guard import MAX_BYTES, fit, nbytes  # noqa: E402


def test_没超预算的原样返回():
    s = json.dumps({"a": 1, "data": [1, 2, 3]}, ensure_ascii=False)
    assert fit(s) == s


def test_默认预算低于内核阈值():
    """内核是 16384。预算必须留余量，卡在 16384 上等于没做。"""
    assert MAX_BYTES < 16 * 1024


def test_超预算时裁列表且仍是合法JSON():
    items = [{"code": f"60{i:04d}", "name": "某某股份", "pe": 12.34, "roe": 9.87} for i in range(2000)]
    s = json.dumps({"func": "market_screen", "matched": 2000, "data": items}, ensure_ascii=False)
    assert nbytes(s) > MAX_BYTES
    out = fit(s, tool="market_screen")
    assert nbytes(out) <= MAX_BYTES
    d = json.loads(out)                       # 合法 JSON —— 这是重点
    g = d["_hca_size_guard"]
    assert g["field"] == "data"
    assert g["total_items"] == 2000
    assert g["kept_items"] == len(d["data"])
    assert g["dropped_items"] == 2000 - g["kept_items"]
    assert g["kept_items"] >= 1
    assert "不是不存在" in g["warning"]
    assert d["matched"] == 2000               # 非列表字段不动，总数还看得见


def test_裁到的条数是能塞下的最大值():
    items = [{"i": i, "pad": "x" * 100} for i in range(500)]
    s = json.dumps({"data": items}, ensure_ascii=False)
    out = fit(s, tool="t")
    kept = json.loads(out)["_hca_size_guard"]["kept_items"]
    # 再多一条就该超了
    more = dict(json.loads(out))
    more["data"] = items[:kept + 1]
    assert nbytes(json.dumps(more, ensure_ascii=False)) > MAX_BYTES


def test_顶层是数组也能裁():
    s = json.dumps([{"i": i, "pad": "y" * 200} for i in range(400)], ensure_ascii=False)
    out = fit(s, tool="t")
    d = json.loads(out)
    assert nbytes(out) <= MAX_BYTES
    assert d["_hca_size_guard"]["total_items"] == 400


def test_非JSON的超大文本也包成合法JSON():
    s = "行情数据" * 20000
    out = fit(s, tool="raw")
    assert nbytes(out) <= MAX_BYTES
    d = json.loads(out)
    assert "raw_head" in d
    assert "不要从它推断" in d["_hca_size_guard"]["warning"]


def test_单条就超预算时不返回半截结构():
    s = json.dumps({"data": [{"blob": "z" * 40000}]}, ensure_ascii=False)
    out = fit(s, tool="t")
    assert nbytes(out) <= MAX_BYTES
    d = json.loads(out)                       # 关键：还是合法 JSON
    assert d["_hca_size_guard"]["reason"].startswith("单条记录")


def test_预算可以按调用覆盖():
    s = json.dumps({"data": list(range(1000))}, ensure_ascii=False)
    out = fit(s, tool="t", max_bytes=800)
    assert nbytes(out) <= 800
