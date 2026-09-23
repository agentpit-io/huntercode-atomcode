#!/usr/bin/env python3
"""I3 · U-20 分档探针出表：把「工具 schema 体积」与「提示正文」拆开。

输入是 `deploy/eval/i3-schema-probe.sh` 落在一个目录里的两类文件：

  · `u20-<臂>-c<轮>-r<次>.json`      —— 评测脚本在 daemon 外面看到的这一次运行
  · `trace-<臂>-c<轮>.jsonl`         —— llm-shim 追踪，一行一次上游请求；
                                        **自变量（n_tools / tools_bytes /
                                        system_chars）与因变量（ttfb_ms）同在一行**

每臂每轮前 `--warm` 次是热身，两类文件都按序号丢掉。

出三张表：
  1. 逐臂：请求构成 + 墙钟 / 模型段 / 上游首字，各取中位数；
  2. 只动 schema 的四臂（t-full / t-rel / t-min / t-zero）的响应关系 +
     一条最小二乘直线（每千字节值多少毫秒）；
  3. 只动正文的那一格（t-rel vs p-min）。

**不做的事**：不拿这三张表去推 q3/q5 能快多少 —— 那要在真题上测，
这里量到的是「同一道零工具的题上，请求里少 X 字节 schema 值多少毫秒」。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics as st

ARMS = ["t-full", "t-rel", "t-min", "t-zero", "p-min"]
ARM_DESC = {
    "t-full": "投研人设 · 上游行为（两层都不摘工具）",
    "t-rel": "投研人设 · 发行版默认（fork deny + shim 白名单）",
    "t-min": "投研人设 · 只留本场景要的几个工具",
    "t-zero": "投研人设 · 工具全关",
    "p-min": "**一行人设** · 发行版默认工具（只动正文的对照）",
}
RUN_RE = re.compile(r"u20-(?P<arm>[a-z-]+)-c(?P<cyc>\d+)-r(?P<run>\d+)\.json$")


def _med(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(st.median(xs), 1) if xs else None


def _fmt(v, unit=""):
    return "—" if v is None else f"{v:g}{unit}"


def collect(d: str, warm: int):
    """→ {臂: {"runs": [每次一条], "reqs": [每次上游请求一条]}}"""
    out = {a: {"runs": [], "reqs": []} for a in ARMS}
    for path in sorted(glob.glob(os.path.join(d, "u20-*.json"))):
        if path.endswith(".raw.json"):
            continue
        m = RUN_RE.search(os.path.basename(path))
        if not m or m["arm"] not in out:
            continue
        if int(m["run"]) <= warm:          # 热身不计入
            continue
        try:
            rec = json.load(open(path, encoding="utf-8"))
        except Exception:                  # noqa: BLE001
            continue
        tot = (rec.get("waterfall") or {}).get("totals") or {}
        out[m["arm"]]["runs"].append({
            "cyc": int(m["cyc"]), "run": int(m["run"]),
            "wall_ms": rec.get("wall_ms"),
            "model_ms": tot.get("model_ms"),
            "text_len": rec.get("text_len"),
            "prompt_tokens": rec.get("prompt_tokens"),
            "n_calls": len(rec.get("calls") or []),
        })
    for path in sorted(glob.glob(os.path.join(d, "trace-*.jsonl"))):
        base = os.path.basename(path)
        m = re.match(r"trace-(?P<arm>[a-z-]+)-c(?P<cyc>\d+)\.jsonl$", base)
        if not m or m["arm"] not in out:
            continue
        rows = []
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:              # noqa: BLE001
                pass
        # 一次运行正好一次上游请求（会话自动起名在 opt 阶段是关掉的），
        # 所以第 k 行对第 k 次运行；前 warm 行同样丢掉。
        for i, r in enumerate(rows, start=1):
            if i <= warm:
                continue
            r["_cyc"] = int(m["cyc"])
            out[m["arm"]]["reqs"].append(r)
    return out


def lstsq(xs, ys):
    """两点以上的最小二乘。返回 (斜率, 截距, R²)；点太少或 x 全同返回 None。"""
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    sst = sum((y - my) ** 2 for y in ys)
    sse = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 1 - sse / sst if sst else None
    return b, a, r2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--warm", type=int, default=2)
    args = ap.parse_args()
    data = collect(args.dir, args.warm)

    print("# U-20 分档实测：工具 schema 体积 → 模型段耗时\n")
    print(f"> 原始记录 `{args.dir}`；每臂每轮前 {args.warm} 次热身已丢弃。"
          "上游首字 `ttfb_ms` 来自 llm-shim 追踪，与 `n_tools` / `tools_bytes` **同一行**。\n")

    print("## 1. 逐臂\n")
    print("| 臂 | 说明 | n | 工具数 | schema 字节 | 系统提示字符 | 请求字节 | "
          "上游首字中位 | 模型段中位 | 墙钟中位 | 正文字数中位 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    summary = {}
    for arm in ARMS:
        runs, reqs = data[arm]["runs"], data[arm]["reqs"]
        if not runs and not reqs:
            print(f"| `{arm}` | {ARM_DESC[arm]} | 0 | — | — | — | — | — | — | — | — |")
            continue
        n_tools = _med([r.get("n_tools") for r in reqs])
        tb = _med([r.get("tools_bytes") for r in reqs])
        sc = _med([r.get("system_chars") for r in reqs])
        rb = _med([r.get("req_bytes") for r in reqs])
        ttfb = _med([r.get("ttfb_ms") for r in reqs])
        model = _med([r["model_ms"] for r in runs])
        wall = _med([r["wall_ms"] for r in runs])
        tl = _med([r["text_len"] for r in runs])
        summary[arm] = {"n": len(runs), "n_tools": n_tools, "tools_bytes": tb,
                        "system_chars": sc, "req_bytes": rb, "ttfb": ttfb,
                        "model": model, "wall": wall}
        print(f"| `{arm}` | {ARM_DESC[arm]} | {len(runs)} | {_fmt(n_tools)} | "
              f"{_fmt(tb)} | {_fmt(sc)} | {_fmt(rb)} | {_fmt(ttfb,' ms')} | "
              f"{_fmt(model,' ms')} | {_fmt(wall,' ms')} | {_fmt(tl)} |")

    print("\n### 逐次上游首字（ms）—— 别只看中位数\n")
    for arm in ARMS:
        vals = [r.get("ttfb_ms") for r in data[arm]["reqs"] if r.get("ttfb_ms") is not None]
        if vals:
            print(f"* `{arm}`：{[round(v) for v in vals]}")

    print("\n### 自检：这道题必须是「零工具调用、一次上游请求」\n")
    bad = []
    for arm in ARMS:
        runs, reqs = data[arm]["runs"], data[arm]["reqs"]
        calls = sum(r["n_calls"] for r in runs)
        if calls:
            bad.append(f"`{arm}` 有 {calls} 次工具调用 —— 这几次不该计入")
        if runs and reqs and len(runs) != len(reqs):
            bad.append(f"`{arm}` 运行 {len(runs)} 次但上游请求 {len(reqs)} 次 —— 对不上，"
                       "追踪与运行的配对不成立")
    print("\n".join(f"* ⚠️ {x}" for x in bad) if bad
          else "* 五臂全部 0 次工具调用，且运行次数与上游请求次数一一对上 ✓")

    print("\n## 2. 只动 schema 的四臂\n")
    pts = [(summary[a]["tools_bytes"], summary[a]["ttfb"], a)
           for a in ("t-full", "t-rel", "t-min", "t-zero")
           if a in summary and summary[a]["tools_bytes"] is not None
           and summary[a]["ttfb"] is not None]
    if len(pts) >= 2:
        base = min(pts, key=lambda p: p[0])
        print("| 臂 | schema 字节 | 上游首字中位 | 比 `t-zero` 多 | 每千字节 |")
        print("|---|---|---|---|---|")
        for tb, tt, a in sorted(pts):
            d_b, d_t = tb - base[0], tt - base[1]
            per = f"{d_t / (d_b / 1000):.1f} ms" if d_b else "—"
            print(f"| `{a}` | {tb:g} | {tt:g} ms | {d_t:+.1f} ms | {per} |")
        fit = lstsq([p[0] for p in pts], [p[1] for p in pts])
        if fit:
            b, a0, r2 = fit
            print(f"\n**最小二乘**（{len(pts)} 个点）：上游首字 ≈ "
                  f"{a0:.0f} ms + **{b * 1000:.1f} ms/千字节** × schema 字节"
                  + (f"，R² = {r2:.2f}" if r2 is not None else ""))
            print("\n> ⚠️ 四个点、每点 n 不大，**斜率的方向可信、量值别当精确值用**。"
                  "R² 高也只说明四个中位数落在一条线附近，不等于逐次数据的离散度小。")
    else:
        print("（点不够，出不了这张表）")

    print("\n## 3. 只动正文的那一格（`t-rel` vs `p-min`，工具清单相同）\n")
    if "t-rel" in summary and "p-min" in summary:
        a, b = summary["t-rel"], summary["p-min"]
        same = a["n_tools"] == b["n_tools"] and a["tools_bytes"] == b["tools_bytes"]
        print(f"| | 系统提示字符 | schema 字节 | 上游首字中位 | 模型段中位 |")
        print("|---|---|---|---|---|")
        print(f"| `t-rel`（投研人设） | {_fmt(a['system_chars'])} | {_fmt(a['tools_bytes'])} | "
              f"{_fmt(a['ttfb'],' ms')} | {_fmt(a['model'],' ms')} |")
        print(f"| `p-min`（一行人设） | {_fmt(b['system_chars'])} | {_fmt(b['tools_bytes'])} | "
              f"{_fmt(b['ttfb'],' ms')} | {_fmt(b['model'],' ms')} |")
        if a["ttfb"] is not None and b["ttfb"] is not None:
            print(f"\n**差**：{a['ttfb'] - b['ttfb']:+.1f} ms"
                  f"（系统提示少了 {(a['system_chars'] or 0) - (b['system_chars'] or 0):g} 字符）")
        print(f"\n工具清单是否真的相同：**{'是' if same else '否 —— 这一格不能用'}**")
    else:
        print("（缺臂，出不了这张表）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
