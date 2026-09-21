# -*- coding: utf-8 -*-
"""脚本修错(screen_nl.fix_script / script_changes)回归用例 —— 不联网,不连库,不依赖 pytest。

    cd apps/api && PYTHONPATH=. python tests/test_screen_fix.py

2026-09-13 用户粘了一份 ThinkScript 风格脚本,17 句只有 average_volume_300d_calc 取不到,
界面只给报错、不给 AI 按钮。修法见 screen_nl.py「脚本修错」节。这里盯这几件事:
  1. 改了哪几句由程序比对得出,改一个数字也必须被列出来(不采信模型自述)
  2. 模型只给替换指令;推理里举的例子(原文不在脚本里)不能被采用
  3. 替换按标识符边界,不许 SMA5 换到 SMA50 里;注释原样保留
  4. 改动太多 → 判失败,不把重写过的脚本当成「修好了」
  5. 均量周期取不到时报错写清可用值
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_API = os.path.dirname(_HERE)

FAILS = []


def check(name, ok):
    print(("OK   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


def _load():
    for n in ("app", "app.services", "app.services.quant", "app.services.online_analysis"):
        if n not in sys.modules:
            m = types.ModuleType(n)
            m.__path__ = []
            sys.modules[n] = m
    mods = {}
    for name in ("screen_dsl", "screen_nl"):
        full = f"app.services.quant.{name}"
        path = os.path.join(_API, "app", "services", "quant", f"{name}.py")
        spec = importlib.util.spec_from_file_location(full, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
        mods[name] = mod
    return mods["screen_dsl"], mods["screen_nl"]


dsl, nl = _load()

SMA = [5, 10, 20, 40, 50, 150, 200]
FIELDS = {"SMA5", "SMA10", "SMA20", "SMA40", "SMA50", "SMA150", "SMA200", "rs_rating",
          "high_63d", "low_63d", "high_21d", "low_21d", "price_52_week_high"}
FIELDS |= {f"average_volume_{n}d_calc" for n in (10, 30, 60, 90)}


def compile_(src):
    return dsl.compile_script(src, lambda n: n in FIELDS, SMA, SMA, [14])


ORIG = """# 注释
def c_price = close > 20;   # 价格 > $20
def c_rs = rs_rating >= 80;
def rng = (high_63d - low_63d) / high_63d;
def c_depth = rng >= 0.12 and rng <= 0.35;
def c_vdry = average_volume_10d_calc < average_volume_300d_calc * 0.9;  # 缩量
plot scan = c_price and c_rs
    and c_depth and c_vdry;
"""
# 2026-09-14 起 50 天均量由自家日线算、能编译了;「算不了的周期」换成超出上限(250 天)的 300 天
GOOD = ORIG.replace("average_volume_300d_calc", "average_volume_60d_calc")
FIX_LINE = "<<average_volume_300d_calc>> => <<average_volume_60d_calc>>"

# ── 1. 报错信息 ──────────────────────────────────────────────
try:
    compile_(ORIG)
    check("300 天均量应当编译失败", False)
    ERR = ""
except dsl.ScreenError as e:
    ERR = str(e)
    check("报错点名 300 天", "300" in ERR and "average_volume_300d_calc" in ERR)
    check("报错说清可用周期与自家日线上限", "10/30/60/90" in ERR and "250" in ERR)
check("⭐50 天均量能编译(自家日线算)并写进说明",
      "自家日线" in " ".join(compile_(ORIG.replace("average_volume_300d_calc", "average_volume_50d_calc")).notes))
check("改成 60 天能编译", bool(compile_(GOOD)))

# ── 2. looks_like_script(脚本走修错,不走大白话翻译)──────────
check("ThinkScript 脚本判为脚本", nl.looks_like_script(ORIG))
check("大白话不判为脚本", not nl.looks_like_script("成交量大于100万且站上50日线"))

# ── 3. script_changes ─────────────────────────────────────────
ch = nl.script_changes(ORIG, GOOD)
check("只改一句 → 列出一句", len(ch) == 1 and ch[0]["name"] == "def c_vdry")
check("对照里有前后原文", "300d" in ch[0]["before"] and "60d" in ch[0]["after"])
check("注释 / 换行 / 空白差异不算改动",
      nl.script_changes(ORIG, "def c_price = close  >  20;\n" + ORIG.split("\n", 2)[2]) == [])
sneaky = GOOD.replace("0.12", "0.10")
check("悄悄改了阈值也要列出来", {c["name"] for c in nl.script_changes(ORIG, sneaky)}
      == {"def c_vdry", "def c_depth"})
dropped = GOOD.replace("def c_rs = rs_rating >= 80;\n", "")
check("删掉的句子列成 after=None",
      any(c["name"] == "def c_rs" and c["after"] is None for c in nl.script_changes(ORIG, dropped)))
added = GOOD.replace("plot scan", "def c_x = close > 5;\nplot scan")
check("新增的句子列成 before=None",
      any(c["name"] == "def c_x" and c["before"] is None for c in nl.script_changes(ORIG, added)))

# ── 4. 替换指令的解析与应用 ────────────────────────────────────
noisy = ("Okay, the user wants ... for example <<SMA37>> => <<SMA40>> is how it works.\n"
         "Wait, is SMA50 valid?\n" + FIX_LINE + "\n")
check("推理里举的例子(原文不在脚本里)不采用",
      nl.parse_replacements(noisy, ORIG) == [("average_volume_300d_calc", "average_volume_60d_calc")])
check("只在注释里出现的原文不采用",
      nl.parse_replacements("<<$20>> => <<$30>>", ORIG) == [])
check("同一原文多次给出取最后一次",
      nl.parse_replacements("<<rs_rating>> => <<x>>\n<<rs_rating>> => <<y>>", ORIG) == [("rs_rating", "y")])
check("新写法里带 ; 或 # 的不采用(防止塞进新语句)",
      nl.parse_replacements("<<rs_rating>> => <<1; def z = 2>>", ORIG) == [])
check("按标识符边界替换:SMA5 不碰 SMA50",
      nl.apply_replacements("def a = SMA5 > SMA50;", [("SMA5", "SMA10")]) == "def a = SMA10 > SMA50;")
check("注释原样保留、注释里的同名不替换",
      nl.apply_replacements("def a = SMA5 > 1;  # SMA5 上方", [("SMA5", "SMA10")])
      == "def a = SMA10 > 1;  # SMA5 上方")
check("带运算符的片段也能替换",
      "high_21d - low_21d" in nl.apply_replacements(ORIG, [("high_63d - low_63d", "high_21d - low_21d")]))


# ── 5. fix_script(假模型)────────────────────────────────────
class _Msg:
    def __init__(self, c): self.message = types.SimpleNamespace(content=c)


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        return types.SimpleNamespace(choices=[_Msg(self.replies.pop(0))], usage=None)


def with_client(client):
    mod = types.ModuleType("app.services.online_analysis.llm_client")
    mod.get_client = lambda: client
    sys.modules["app.services.online_analysis.llm_client"] = mod


def run(replies, script=ORIG, err=None):
    fc = FakeClient(replies)
    with_client(fc)
    return fc, nl.fix_script(script, err or ERR, "美股", SMA, SMA, [14], validate=compile_)


fc, r = run([noisy])
check("推理 + 替换指令 → 一轮修好", r["attempts"] == 1 and len(r["changes"]) == 1)
check("修好的脚本保留用户注释", "# 缩量" in r["script"] and "# 价格 > $20" in r["script"])
check("报错原文喂给了模型", ERR in fc.calls[0]["messages"][1]["content"])

# 两处错:解析器先报 SMA37(c_price 在前);第一轮修掉它,露出 50d;第二轮在修过的基础上修 50d
ORIG2 = ORIG.replace("close > 20", "close > SMA37")
try:
    compile_(ORIG2)
    ERR2 = ""
except dsl.ScreenError as e:
    ERR2 = str(e)
check("两处错时先报前面那处", "SMA37" in ERR2)
fc, r = run(["<<SMA37>> => <<SMA40>>", FIX_LINE], script=ORIG2, err=ERR2)
check("修一处露出下一处 → 在修过的基础上继续",
      r["attempts"] == 2 and len(r["changes"]) == 2 and "average_volume_300d_calc" not in r["script"]
      and "SMA37" not in r["script"])
check("第二轮喂给模型的是已修过的脚本与新报错",
      "SMA40" in fc.calls[1]["messages"][1]["content"] and "300" in fc.calls[1]["messages"][1]["content"])
# 替换没解决当前报错 → 不前进(不把没用的改动攒下来)
fc2 = FakeClient(["<<rs_rating>> => <<rs_rating * 1>>", FIX_LINE])
with_client(fc2)
r = nl.fix_script(ORIG, ERR, "美股", SMA, SMA, [14], validate=compile_)
check("没解决报错的替换不攒进结果", r["attempts"] == 2 and len(r["changes"]) == 1)

fc, r = run(["<<foo_bar>> => <<baz>>", FIX_LINE])
check("第一轮原文找不到 → 重试", r["attempts"] == 2)

# 改动太多(4 句 > 上限 3 句)→ 三轮都超 → 失败
too_many = "\n".join([FIX_LINE, "<<20>> => <<30>>", "<<80>> => <<90>>",
                      "<<high_63d - low_63d>> => <<high_21d - low_21d>>"])
try:
    run([too_many] * 3)
    check("改动太多应当判失败", False)
except dsl.ScreenError as e:
    check("改动太多判失败,报错里带原始报错", "上限" in str(e) and "50" in str(e))

try:
    run(["NONE"] * 3)
    check("模型不给替换应当失败", False)
except dsl.ScreenError:
    check("模型不给替换 → 失败,不返回半成品", True)

with_client(None)
try:
    nl.fix_script(ORIG, ERR, "美股", SMA, SMA, [14], validate=compile_)
    check("没配 key 应当失败", False)
except dsl.ScreenError as e:
    check("没配 key 明说", "LLM" in str(e))

# ── 模型调用失败的中文说明(2026-09-19:DeepSeek 余额 0 时报错条原样显示英文 402)──
class APIStatusError(Exception):
    def __init__(self, msg, status_code):
        super().__init__(msg)
        self.status_code = status_code


class APITimeoutError(Exception):
    pass


class APIConnectionError(Exception):
    pass


RAW402 = "Error code: 402 - {'error': {'message': 'Insufficient Balance', 'type': 'unknown_error'}}"
cases = {
    "402 余额不足": (APIStatusError(RAW402, 402), "余额不足"),
    "401 密钥": (APIStatusError("Error code: 401 - invalid key", 401), "密钥"),
    "403 权限": (APIStatusError("forbidden", 403), "权限"),
    "404 模型名": (APIStatusError("model not found", 404), "LLM_DEFAULT_MODEL"),
    "429 限流": (APIStatusError("rate limited", 429), "限流"),
    "502 暂时出错": (APIStatusError("bad gateway", 502), "暂时出错"),
    "超时": (APITimeoutError("Request timed out."), "超时"),
    "连不上": (APIConnectionError("Connection error."), "连不上"),
    "418 认不出的状态码如实写": (APIStatusError("teapot", 418), "HTTP 418"),
    "没有状态码的异常": (RuntimeError("boom"), "没有正常返回"),
}
for label, (exc, want) in cases.items():
    t = nl.model_error_text(exc)
    check("模型报错 · " + label + " → 中文说明", want in t)
    check("模型报错 · " + label + " · 以「调用模型失败」开头(路由靠它退回 AI 次数)", t.startswith("调用模型失败"))
    check("模型报错 · " + label + " · 不带上游英文原话", "Insufficient" not in t and "Error code" not in t
          and "boom" not in t and "teapot" not in t and "timed out" not in t)


class RaisingClient:
    def __init__(self, exc):
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))
        self.exc = exc

    def _create(self, **kw):
        raise self.exc


for fn, call in (("fix_script", lambda: nl.fix_script(ORIG, ERR, "美股", SMA, SMA, [14], validate=compile_)),
                 ("translate", lambda: nl.translate("收盘价大于20", "美股", SMA, SMA, [14], validate=compile_))):
    with_client(RaisingClient(APIStatusError(RAW402, 402)))
    try:
        call()
        check(fn + " · 402 应当报错", False)
    except dsl.ScreenError as e:
        check(fn + " · 402 走中文说明", str(e).startswith("调用模型失败") and "余额不足" in str(e)
              and "Insufficient" not in str(e))

print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
