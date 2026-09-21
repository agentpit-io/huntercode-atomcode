# -*- coding: utf-8 -*-
"""筛选器 · 官方示例判定的跨层用例(前端真实 buildScript 输出 → 后端 official_preset_of)。

## 为什么要有它

原样运行官方示例不扣扫描次数(2026-09-14)。判定在后端 `screen_source.official_preset_of`,
比的是「前端把条件行回写成的脚本」和「示例原文」的规范化结果。test_screen_preset_free.py 里的回写是
**Python 正则模拟**的,前端 buildScript 一改(括号、plot 顺序、条件宿主、前面的 plot),两边悄悄对不上:
用户原样点示例却被扣次数,或者改过的脚本被当成示例免费跑 —— 都不报错。
这个用例让**真实的前端代码**产出脚本,再交给真实的后端判定。

## 三步(不连库;第 1、3 步在 api 容器里,第 2 步在装了 node 的宿主机上)

    # 1 · 后端解析每个示例
    docker compose exec -T api sh -c 'cd /app && PYTHONPATH=/app python tests/test_screen_xlayer_official.py dump /tmp/xl_parsed.json'
    docker compose cp api:/tmp/xl_parsed.json /tmp/xl_parsed.json
    # 2 · 前端真实代码回写
    node apps/web/public/strategies/xlayer_build.js /tmp/xl_parsed.json /tmp/xl_built.json
    # 3 · 后端判定
    docker compose cp /tmp/xl_built.json api:/tmp/xl_built.json
    docker compose exec -T api sh -c 'cd /app && PYTHONPATH=/app python tests/test_screen_xlayer_official.py verify /tmp/xl_built.json'
"""
from __future__ import annotations

import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.services.quant import screen_source as so  # noqa: E402

KEEP = ("combine", "plot_name", "plot_expr", "plot_refs", "plot_order", "term_host", "extra_plots")


def dump(out: str) -> int:
    items = []
    for p in so.PRESETS:
        d = so.parse_script(p["script"], p.get("market") or "us")
        items.append({"key": p["key"], "market": p.get("market") or "us",
                      "parsed": {**{k: d.get(k) for k in KEEP},
                                 "conditions": [{k: c.get(k) for k in ("name", "expr", "kind", "is_bool", "title", "paren", "tokens")}
                                                for c in d["conditions"]]}})
    with open(out, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    print(f"dump · {len(items)} 个示例 → {out}")
    return 0


def verify(path: str) -> int:
    with open(path, encoding="utf-8") as f:
        built = json.load(f)
    fails = []
    ok = 0
    for b in built:
        got = so.official_preset_of(b["script"], b["market"])
        if got and got["key"] == b["key"]:
            ok += 1
            print(f"OK   示例 {b['key']}:前端原样回写 → 判为官方示例")
        else:
            fails.append(b["key"])
            print(f"FAIL 示例 {b['key']}:前端原样回写后没被判为官方示例 · 判定 {got} · 脚本 {b['script'][:200]!r}")
        # 改一处(停用第一条)就不能再免费
        if b.get("script_off"):
            got2 = so.official_preset_of(b["script_off"], b["market"])
            if got2 is None:
                ok += 1
                print(f"OK   示例 {b['key']}:停用一条之后不再是官方示例")
            else:
                fails.append(b["key"] + "·off")
                print(f"FAIL 示例 {b['key']}:停用一条之后仍被判成官方示例 {got2}")
    if not built:
        fails.append("没有任何示例")
    print(f"\n{'ALL OK' if not fails else 'SOME FAILED'} · 通过 {ok} · 失败 {len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("dump", "verify"):
        print(__doc__)
        sys.exit(2)
    sys.exit(dump(sys.argv[2]) if sys.argv[1] == "dump" else verify(sys.argv[2]))
