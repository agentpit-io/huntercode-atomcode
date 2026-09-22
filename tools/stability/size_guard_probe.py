#!/usr/bin/env python3
"""P0-11 的前后对照探针：**在 daemon 容器里**量一次 MCP 返回的字节数。

    docker exec hca-daemon /opt/hca/venv/bin/python /opt/hca/tools/size_guard_probe.py
    # 或开发机上直接对着仓库里的源码量（要 pandas）：
    #   python3 tools/stability/size_guard_probe.py --src tools/akshare-mcp/akshare_mcp/server.py

零 token：用一张**合成的宽表**（50 行 × 84 列，仿 `stock_financial_analysis_indicator`
那种财务指标接口），不联网、不过模型。量的是「这份返回会不会被内核砍」。

判据：AtomCode 内核 `output_artifact.rs:79` 的 `THRESHOLD_BYTES = 16*1024`。
返回超过它就会被砍成头尾各 4 KB，中间不可见且 JSON 断裂（待办池 P0-11）。
"""
from __future__ import annotations

import argparse
import json
import sys

KERNEL_THRESHOLD = 16 * 1024


def load(src: str | None):
    if src:
        ns = {"json": json}
        text = open(src, encoding="utf-8").read()
        i = text.index("def _to_json(")
        ns["MAX_ROWS"] = 50
        ns["MAX_BYTES"] = 15000
        exec(text[i:text.index("def main()")], ns)
        return ns["_to_json"], ns["MAX_ROWS"], ns.get("MAX_BYTES")
    from akshare_mcp import server as m           # 容器里装好的那份
    return m._to_json, m.MAX_ROWS, getattr(m, "MAX_BYTES", None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=None, help="直接读某个 server.py（不走 import）")
    ap.add_argument("--rows", type=int, default=50)
    ap.add_argument("--cols", type=int, default=84)
    a = ap.parse_args()

    try:
        import pandas as pd
    except ImportError:
        print("需要 pandas（容器里用 /opt/hca/venv/bin/python 跑）")
        return 2

    to_json, max_rows, max_bytes = load(a.src)
    df = pd.DataFrame({f"指标_{c}_同比增长率(%)": [f"{c}.{r}23456789" for r in range(a.rows)]
                       for c in range(a.cols)})

    out = to_json("stock_financial_analysis_indicator", df)
    n = len(out.encode("utf-8"))
    d = json.loads(out)
    over = n > KERNEL_THRESHOLD

    print(f"AKSHARE_MAX_ROWS={max_rows}  AKSHARE_MAX_BYTES={max_bytes}")
    print(f"输入：{a.rows} 行 × {a.cols} 列（仿财务指标接口的宽表）")
    print(f"返回字节数：{n:,}")
    print(f"内核阈值：{KERNEL_THRESHOLD:,} → {'❌ 会被砍成头尾各 4 KB' if over else '✅ 在阈值以内，不会被砍'}")
    print(f"返回里的 rows/total：{d.get('rows')}/{d.get('total')}  truncated={d.get('truncated')}")
    if d.get("note"):
        print(f"note：{d['note']}")

    # 再量一次带列投影的
    want = [f"指标_{c}_同比增长率(%)" for c in range(6)]
    try:
        out2 = to_json("stock_financial_analysis_indicator", df, want)
    except TypeError:
        print("\n（这份 server.py 的 _to_json 还没有 columns 参数 —— 是修复前的版本）")
        return 1 if over else 0
    n2 = len(out2.encode("utf-8"))
    d2 = json.loads(out2)
    print(f"\n带列投影（只要 6 列）：{n2:,} 字节，"
          f"{'❌ 仍超阈值' if n2 > KERNEL_THRESHOLD else '✅ 在阈值以内'}，"
          f"rows={d2.get('rows')}/{d2.get('total')}，丢弃列数={d2.get('columns_dropped_by_projection')}")
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
