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


def test_内容全是转义字符时不会被JSON转义撑爆预算():
    """回归：第 3 档原先按**原始**字节截（`budget - 600`），再整个塞进 `json.dumps`。

    JSON 转义会膨胀 —— `"` → `\\"`、`\\` → `\\\\`，一段全是这两个字符的内容进 JSON 后体积翻倍。
    实测那一版对 36 023 字节的输入吐出 **29 245 字节**，不但超预算，
    **还超过内核 16 384 的阈值** —— 于是又被内核砍成断裂 JSON，这一档等于没做。
    现在改成对最终输出二分。原先那个用例填的是 `"z" * 40000`（不含需要转义的字符），
    正好躲过了这个坑，所以补这一条。
    """
    s = json.dumps({"one_huge_record": '"\\' * 9000}, ensure_ascii=False)
    assert nbytes(s) > 30000
    out = fit(s, tool="probe")
    assert nbytes(out) <= MAX_BYTES                 # 预算
    assert nbytes(out) <= 16 * 1024                 # 更要紧：内核阈值
    json.loads(out)                                 # 仍是合法 JSON


def test_几种转义密度下都不超预算():
    for name, payload in [
        ("全转义", json.dumps({"r": '"\\' * 9000}, ensure_ascii=False)),
        ("长中文", json.dumps({"r": "测" * 12000}, ensure_ascii=False)),
        ("纯文本", "x" * 40000),
        ("中文非ASCII", json.dumps({"r": "甲乙丙丁" * 5000}, ensure_ascii=False)),
    ]:
        out = fit(payload, tool="t")
        assert nbytes(out) <= MAX_BYTES, f"{name} 超预算：{nbytes(out)}"
        json.loads(out)


def test_随机输入下的两条不变量():
    """性质测试：400 组随机输入（含引号 / 反斜杠 / 换行 / emoji / 单引号）。

    两条不变量：
      1. **超预算的输入，裁完一定不超预算**（这是整道闸存在的理由）；
      2. **裁过的输出一定是合法 JSON**（与内核那种砍成半截的做法的本质区别）。

    没超预算的输入原样返回 —— 包括非 JSON 的纯文本，那是对的：MCP 的
    text content 本来就不必是 JSON，不该为了「统一成 JSON」去包一层。
    （第一版断言写成「输出恒为合法 JSON」，结果 21 组没超预算的纯文本报红 ——
    是断言错了不是代码错了，记在这里免得下次再写一遍。）

    固定种子，可复现。
    """
    import random
    random.seed(7)
    alphabet = ['a', '中', '"', '\\', '\n', '\t', 'é', '😀', "'", '/']

    def blob(n: int) -> str:
        return ''.join(random.choice(alphabet) for _ in range(n))

    cut = untouched = 0
    for i in range(400):
        kind = i % 4
        if kind == 0:
            payload = json.dumps({"items": [{"x": blob(random.randint(1, 300))}
                                            for _ in range(random.randint(1, 200))]},
                                 ensure_ascii=False)
        elif kind == 1:
            payload = json.dumps([{"a": blob(random.randint(1, 500))}
                                  for _ in range(random.randint(1, 100))], ensure_ascii=False)
        elif kind == 2:
            payload = json.dumps({"big": blob(random.randint(1, 30000)), "items": [1, 2]},
                                 ensure_ascii=False)
        else:
            payload = blob(random.randint(1, 40000))

        out = fit(payload, tool="prop")
        if nbytes(payload) <= MAX_BYTES:
            assert out == payload, f"第 {i} 组没超预算却被动过"
            untouched += 1
            continue
        cut += 1
        assert nbytes(out) <= MAX_BYTES, f"第 {i} 组裁完仍超预算：{nbytes(out)}"
        assert nbytes(out) <= 16 * 1024, f"第 {i} 组裁完仍超内核阈值"
        json.loads(out)                                   # 裁过的必须是合法 JSON

    assert cut > 200 and untouched > 50, f"样本分布不对：裁了 {cut} 组、原样 {untouched} 组"
