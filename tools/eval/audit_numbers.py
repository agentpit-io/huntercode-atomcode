#!/usr/bin/env python3
"""A1 的取证工具：把正文里的每个数字和本次工具返回原文对一遍。

A1 的评分表写的是「正文里出现的每个数字都能在**本次的**工具返回里找到，
出现一个编造的数字即本项 0 分」。30 次运行、每次几十个数字，靠眼睛看既慢
又不可复现。这个脚本把对照做成一张表：

    python3 tools/eval/audit_numbers.py docs/eval/raw/q1-fundamental-atomcode-r1

输出每个数字一行：数字 / 正文里的上下文 / 在哪个工具的返回里找到（找不到写 ✗）。

**它不给分，也不替人做判断。** 找不到 ≠ 编造 —— 可能是算出来的（如「1580 → 1252
跌了 20.7%」）、可能来自题面、可能是年份或列表序号。脚本只负责把「需要人看一眼
的那几个」挑出来，人给的分和理由仍然写进 scores.json。

两边的原始记录格式不同，都支持：
  · HCA 侧   `<id>.sse`      —— `tool_result` 事件的 `output`
  · 基线侧   `<id>.raw.json` —— `messages[].parts[]` 里 type=tool 的 `state.output`
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 正文里要审的「数字」：带小数点或千分位的数、百分比、整数。
# 纯序号（1. 2. 3.）与年月日在 CONTEXT_SKIP 里排除。
NUM = re.compile(r"-?\d[\d,]*\.?\d*")
# 这些一看就不是数据，跳过：年份、月份日、列表序号、章节号
SKIP_EXACT = {str(y) for y in range(1990, 2101)} | {str(i) for i in range(1, 13)}
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def norm(tok: str) -> str:
    """去千分位、去尾部的小数零，便于在工具返回里找同一个数。"""
    t = tok.replace(",", "")
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return t or "0"


def tool_outputs_sse(path: Path) -> list:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            e = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if e.get("type") == "tool_result":
            out.append((e.get("name") or "?", str(e.get("output") or "")))
    return out


def tool_outputs_raw(path: Path) -> list:
    d = json.loads(path.read_text(encoding="utf-8"))
    out = []
    msgs = d.get("messages") or []
    if d.get("post_response"):
        msgs = msgs + [d["post_response"]]
    for m in msgs:
        for part in (m.get("parts") or []):
            if part.get("type") != "tool":
                continue
            st = part.get("state") or {}
            body = st.get("output")
            if body is None:
                body = json.dumps(st.get("result") or st, ensure_ascii=False)
            out.append((part.get("tool") or "?", str(body)))
    return out


def audit(stem: Path):
    rec = json.loads(stem.with_suffix(".json").read_text(encoding="utf-8"))
    text = rec.get("text") or ""
    if not text:
        # 基线侧的正文在 raw.json 里；.json 是摘要
        raw = stem.with_suffix("").with_name(stem.name + ".raw.json")
        if raw.is_file():
            d = json.loads(raw.read_text(encoding="utf-8"))
            parts = (d.get("post_response") or {}).get("parts") or []
            text = "".join(p.get("text") or "" for p in parts if p.get("type") == "text")

    sse = stem.with_suffix(".sse")
    raw = stem.with_name(stem.name + ".raw.json")
    if sse.is_file():
        tools = tool_outputs_sse(sse)
        src = sse.name
    elif raw.is_file():
        tools = tool_outputs_raw(raw)
        src = raw.name
    else:
        print(f"✗ 找不到 {sse.name} 也找不到 {raw.name}", file=sys.stderr)
        return 2

    haystack = [(n, norm_all(o)) for n, o in tools]
    pools = [(n, floats_of(o)) for n, o in tools]
    # ISO 日期单独审：整串去工具返回里找，找到就是一行。不这么做的话
    # `2026-06-30` 会被 NUM 拆成 `2026` `-06` `-30`，后两个必然找不到，
    # 一张表里全是这种噪声，真正要人看的那两三个反而埋了。
    seen, rows = set(), []
    for d in dict.fromkeys(ISO_DATE.findall(text)):
        hit = next((n for n, hay in haystack if d in hay), None)
        if hit is None:
            # 紧凑写法也算命中：`watchlist_stock_news` 的 `date` 字段实测**全是空串**
            # （一次返回 30 条、30 个 `"date":""`），日期只存在于东方财富的 URL
            # 里（`.../a/202609213879940651.html`）。模型从 URL 推出 2026-09-21
            # 是合理推断，不该报成「编造」——但要标出来让人看一眼推得对不对。
            compact = d.replace("-", "")
            hit2 = next((n for n, hay in haystack if compact in hay), None)
            if hit2:
                hit = f"{hit2}（只匹配到紧凑写法 {compact}，多半是从 URL 推的）"
        i = text.find(d)
        rows.append((d, hit, text[max(0, i - 24):i + len(d) + 12].replace("\n", " ")))
    masked = ISO_DATE.sub(lambda m: "〈日期〉", text)
    for m in NUM.finditer(masked):
        tok = m.group(0).strip(".,")
        if not tok or tok in SKIP_EXACT:
            continue
        v = norm(tok)
        if v in seen:
            continue
        seen.add(v)
        hit = next((n for n, hay in haystack if v in hay), None)
        if hit is None:
            # 四舍五入命中也算命中（筛选/财务类工具回的是全精度浮点）
            hit3 = next((n for n, pool in pools if rounds_to(tok, pool)), None)
            if hit3:
                hit = f"{hit3}（四舍五入命中，工具返回是全精度值）"
        if hit is None and v.startswith("-"):
            # 工具返回里常把符号和数分开放（"同比 下降 1.95%"），
            # 绝对值命中也算命中，但标出来让人看一眼
            hit2 = next((n for n, hay in haystack if v[1:] in hay), None)
            if hit2:
                hit = f"{hit2}（只匹配到绝对值 {v[1:]}）"
        ctx = masked[max(0, m.start() - 28):m.end() + 12].replace("\n", " ")
        rows.append((tok, hit, ctx))

    miss = [r for r in rows if r[1] is None]
    print(f"# {stem.name}")
    print(f"正文 {len(text)} 字符 · 工具返回 {len(tools)} 份（来源 {src}）· "
          f"审了 {len(rows)} 个不同的数 · **{len(miss)} 个没在工具返回里找到**")
    print()
    print("| 数字 | 命中的工具 | 正文上下文 |")
    print("|---|---|---|")
    for tok, hit, ctx in rows:
        safe = ctx.replace("|", "\\|")
        print(f"| `{tok}` | {hit or '**✗ 没找到**'} | {safe} |")
    return 0


def norm_all(s: str) -> str:
    """把工具返回里的数也做同样的归一化，避免 1,252.57 / 1252.570 对不上。"""
    return NUM.sub(lambda m: norm(m.group(0)), s)


def floats_of(s: str) -> list:
    """工具返回里所有能解析成数的 token，供四舍五入匹配用。"""
    out = []
    for m in NUM.finditer(s):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            continue
    return out


def rounds_to(tok: str, pool: list) -> bool:
    """工具返回里有没有哪个数，按 tok 的小数位数四舍五入之后正好等于 tok。

    为什么要有：筛选类工具回的是 `33.2451371632829`，模型照规范写成
    `33.25`。逐字符找必然找不到，于是一张表里九行全是这种噪声，
    真正该看的那一两个反而被埋掉。
    """
    t = tok.replace(",", "")
    try:
        want = float(t)
    except ValueError:
        return False
    nd = len(t.split(".")[1]) if "." in t else 0
    return any(round(v, nd) == want for v in pool)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stem", type=Path,
                    help="不带扩展名的路径，如 docs/eval/raw/q1-fundamental-atomcode-r1")
    a = ap.parse_args(argv)
    return audit(a.stem)


if __name__ == "__main__":
    raise SystemExit(main())
