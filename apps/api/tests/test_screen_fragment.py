# -*- coding: utf-8 -*-
"""「复制这一条」复制出来的片段粘回生成框(screen_dsl.complete_fragment)—— 不连库、不联网。

    cd apps/api && PYTHONPATH=. python tests/test_screen_fragment.py

2026-09-17 用户:条件行上的复制按钮复制出 `def c_price_2 = close > 10;`,粘回生成框报「脚本里没有 plot 语句」——
自家复制出来的东西自家不认。copySnippet 产出的片段都没有 plot,还会引用原脚本的中间定义 / 参数。
浏览器里拿 7 个官方示例 + 3 个保存的策略共 127 行逐行「复制 → 追加 / 替换」实跑过;这里把各类形状固定下来。
用例**只加不删**。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_API = os.path.dirname(_HERE)


def _load():
    # 容器 / 有全套依赖时直接用真包(input / rec 会走时间序列引擎 screen_series,伪造的包导不到它)
    try:
        sys.path.insert(0, _API)
        from app.services.quant import screen_dsl as real
        return real
    except Exception:                                # noqa: BLE001
        sys.path.remove(_API)
    for n in ("app", "app.services", "app.services.quant"):
        if n not in sys.modules:
            m = types.ModuleType(n)
            m.__path__ = []
            sys.modules[n] = m
    full = "app.services.quant.screen_dsl"
    if full in sys.modules and hasattr(sys.modules[full], "complete_fragment"):
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, os.path.join(_API, "app", "services", "quant", "screen_dsl.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


sd = _load()
FIELDS = {"close", "open", "high", "low", "volume", "High.3M", "Low.3M", "High.1M", "Low.1M", "rs_rating"}
SMA = [20, 50, 150, 200]


def comp(src):
    return sd.compile_script(src, lambda n: n in FIELDS, SMA, SMA, [14])


def frag(text, ctx=None):
    return sd.complete_fragment(text, ctx, comp)


def raises(text, ctx=None, must=""):
    try:
        frag(text, ctx)
    except sd.ScreenError as e:
        return must in str(e)
    return False


CTX = """input minRng = 0.15;
def rng3m = (High.3M - Low.3M) / High.3M;
def rng1m = (High.1M - Low.1M) / High.1M;
def c_price = close > 10;
def c_depth = rng3m >= minRng;
def c_shrink = rng1m <= rng3m * 0.5;
plot scan = c_price and c_depth and c_shrink;"""

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


# ── A · 不是片段:照原流程走 ─────────────────────────────────────
@case("有 plot 的完整脚本不是片段")
def _():
    return frag(CTX) is None


@case("大白话不是片段(交给本地关键词)")
def _():
    return frag("市盈率低于15") is None


@case("没有 context 的纯表达式不是片段(close > 10 交给本地关键词)")
def _():
    return frag("close > 10") is None


@case("有 context 但纯表达式没用到原脚本的名字 → 不是片段")
def _():
    return frag("close > 10", CTX) is None


# ── B · 替换模式(没有 context)──────────────────────────────────
@case("⭐用户原样:def c_price_2 = close > 10; → 补 plot scan = c_price_2")
def _():
    r = frag("def c_price_2 = close > 10;")
    return r and r["src"].rstrip().endswith("plot scan = c_price_2;") and r["plot_name"] == "scan" and not r["context"] \
        and any("已补上" in n for n in r["notes"])


@case("最后一句漏分号也认")
def _():
    r = frag("def c = close > 10")
    return r and "plot scan = c;" in r["src"]


@case("自带依赖的片段(复制按钮现在会带上中间定义)只把没被引用的布尔 def 当条件")
def _():
    r = frag("input minRng = 0.15;\ndef rng3m = (High.3M - Low.3M) / High.3M;\ndef c_depth = rng3m >= minRng;")
    return r and r["src"].rstrip().endswith("plot scan = c_depth;")


@case("被引用的布尔 def 是中间量,不进 plot")
def _():
    r = frag("def up = close > open;\ndef c = up and volume > 100;")
    return r and r["src"].rstrip().endswith("plot scan = c;")


@case("参数 + 裸表达式(term 行的复制)→ 表达式进 plot")
def _():
    r = frag("input minRng = 0.15;\n(High.3M - Low.3M) / High.3M >= minRng")
    return r and r["src"].rstrip().endswith("plot scan = (High.3M - Low.3M) / High.3M >= minRng;")


@case("裸表达式后面的注释不吃掉补上的分号")
def _():
    r = frag("input n = 5;\nclose > n # 注释;里有分号")
    return r and r["src"].rstrip().endswith("plot scan = close > n;")


@case("条件 + 带 or 的表达式:表达式加括号,不改优先级")
def _():
    r = frag("def c = close > 10;\ninput n = 5;\nclose > n or volume > 100")
    return r and "plot scan = c and (close > n or volume > 100);" in r["src"]


@case("只有参数 → 报「只有参数或数值定义」")
def _():
    return raises("input minRng = 0.15;", must="只有参数或数值定义")


@case("只有数值定义 → 同上")
def _():
    return raises("def rng3m = (High.3M - Low.3M) / High.3M;", must="只有参数或数值定义")


@case("缺依赖(老版本复制出来的片段)→ 编译报不认识,不吞")
def _():
    return raises("def c_depth = rng3m >= 0.15;", must="rng3m")


# ── C · 追加模式(当前脚本当 context)──────────────────────────────
@case("⭐引用原脚本中间定义的条件能追加,只返回片段自己的名字")
def _():
    r = frag("def c_new = rng1m <= 0.05;", CTX)
    return r and r["context"] and r["frag_names"] == {"c_new"} and r["plot_name"] == "pasted" \
        and r["src"].startswith(CTX) and r["src"].rstrip().endswith("plot pasted = c_new;")


@case("条件本身和原脚本重名 → 改名 _2 追加(用户要的就是再加一条)")
def _():
    r = frag("def c_price = close > 10;", CTX)
    return r and r["frag_names"] == {"c_price_2"} and any("c_price_2" in n for n in r["notes"])


@case("⭐一字不差的依赖沿用原来的,不多出 rng3m_2 / minRng_2")
def _():
    snip = "input minRng = 0.15;\ndef rng3m = (High.3M - Low.3M) / High.3M;\ndef c_depth = rng3m >= minRng;"
    r = frag(snip, CTX)
    return r and r["frag_names"] == {"c_depth_2"} and "rng3m_2" not in r["src"] and "minRng_2" not in r["src"] \
        and r["src"].rstrip().endswith("plot pasted = c_depth_2;")


@case("同名但定义不同的依赖 → 改名,并把片段里的引用一起改")
def _():
    snip = "def rng3m = (High.3M - Low.3M) / Low.3M;\ndef c_x = rng3m >= 0.2;"
    r = frag(snip, CTX)
    return r and "def rng3m_2 = (High.3M - Low.3M) / Low.3M;" in r["src"] and "def c_x = rng3m_2 >= 0.2;" in r["src"]


@case("同名参数值不同 → 改名,不覆盖原参数")
def _():
    r = frag("input minRng = 0.30;\ndef c_y = rng3m >= minRng;", CTX)
    return r and "input minRng_2 = 0.30;" in r["src"] and "def c_y = rng3m >= minRng_2;" in r["src"]


@case("整词替换:x.minRng / minRng2 不被改")
def _():
    ctx = "input n = 1;\ndef c = close > n;\nplot scan = c;"
    r = frag("input n = 2;\ndef n2 = 3;\ndef d = close > n and volume > n2;", ctx)
    return r and "def d = close > n_2 and volume > n2;" in r["src"] and "def n2 = 3;" in r["src"]


@case("term 复制(参数 + 表达式)追加:参数一字不差沿用,表达式进 plot")
def _():
    r = frag("input minRng = 0.15;\nrng3m >= minRng", CTX)
    return r and r["src"].rstrip().endswith("plot pasted = rng3m >= minRng;") and "minRng_2" not in r["src"]


@case("纯表达式用到原脚本名字 → 按片段追加")
def _():
    r = frag("rng1m <= 0.05", CTX)
    return r and r["src"].rstrip().endswith("plot pasted = rng1m <= 0.05;")


@case("粘进来的全是原脚本里一字不差的参数 / 数值定义 → 报「一字不差」,不是「没有 plot」")
def _():
    return raises("def rng3m = (High.3M - Low.3M) / High.3M;", CTX, must="一字不差")


@case("原脚本里 plot 叫 pasted 时换个名字")
def _():
    ctx = "def c = close > 1;\nplot pasted = c;"
    r = frag("def d = close > 2;", ctx)
    return r and r["plot_name"] == "pasted_"


@case("context 本身编译不过 → 当没有 context")
def _():
    r = frag("def c = close > 10;", "def broken = ;")
    return r and not r["context"]


if __name__ == "__main__":
    bad = 0
    for name, fn in CASES:
        try:
            ok = bool(fn())
        except Exception as e:                       # noqa: BLE001
            ok = False
            name += f"  ({type(e).__name__}: {e})"
        print(("OK   " if ok else "FAIL ") + name)
        bad += 0 if ok else 1
    print(f"{len(CASES) - bad} passed, {bad} failed")
    if bad:
        sys.exit(1)
    print("ALL OK")
