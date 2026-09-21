# -*- coding: utf-8 -*-
"""扫描源永远下推的过滤(screen_source.base_filter)—— 不联网。

    docker compose run --rm --no-deps -T -v .../app:/app/app -v .../tests:/app/tests api python tests/test_screen_source_filter.py

2026-09-18:港股用 is_primary 会漏掉 A+H 的 H 股与第二上市(工行 / 比亚迪 / 阿里 / 京东 / 汇丰 ……),
港股改成 currency = HKD(剔人民币柜台)。美股 / A 股口径不变。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.quant import screen_source as ss   # noqa: E402


def _left(flt):
    return {f["left"]: f["right"] for f in flt}


def test_hk_drops_is_primary_and_keeps_hkd_only():
    f = _left(ss.base_filter("hk"))
    assert "is_primary" not in f
    assert f["currency"] == "HKD"
    assert f["type"] == "stock" and f["typespecs"] == ["common"]   # 优先股 / ETF 照样挡


def test_us_and_a_unchanged():
    for mk in ("us", "a"):
        assert ss.base_filter(mk) == ss.BASE_FILTER
        assert _left(ss.base_filter(mk))["is_primary"] is True


def test_returns_copy():
    ss.base_filter("hk").append({"left": "x"})
    ss.base_filter("us").append({"left": "x"})
    assert all(f["left"] != "x" for f in ss.base_filter("hk") + ss.BASE_FILTER)


if __name__ == "__main__":
    n = 0
    for k, v in list(globals().items()):
        if k.startswith("test_") and callable(v):
            v()
            n += 1
    print(f"ok {n}")
