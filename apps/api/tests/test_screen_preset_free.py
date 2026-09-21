"""官方示例原样运行不扣扫描次数 —— 「原样」判定(screen_source.official_preset_of)。不联网、不连库。

    cd apps/api && PYTHONPATH=. python tests/test_screen_preset_free.py     # 必须 ALL OK

最怕两种错:
  · 没改过却判成改过(界面把条件重新拼成脚本后注释 / 换行 / plot 顺序变了)→ 用户白扣次数
  · 改过却判成原样(关掉一条、改个数、加一条、换市场)→ 等于任何人都能免费扫描
"""
import re
import sys

from app.services.quant import screen_source as ss

failed = 0


def check(name, ok, extra=""):
    global failed
    if ok:
        print("PASS", name)
    else:
        failed += 1
        print("FAIL", name, extra)


NL = chr(10)


def statements(script):
    """去注释、切成一句一句(和界面重新拼脚本时的形状一样:一句一行、没有注释)。"""
    body = NL.join(line.split("#", 1)[0] for line in script.split(NL))
    return [re.sub(r"\s+", " ", s).strip() for s in body.split(";") if s.strip()]


def rebuild(script, plot_reverse=False):
    out = []
    for s in statements(script):
        m = re.match(r"^plot (\w+) = (.+)$", s)
        if m and plot_reverse and " and " in m.group(2) and " or " not in m.group(2) and "(" not in m.group(2):
            names = [x.strip() for x in m.group(2).split(" and ")]
            s = f"plot {m.group(1)} = " + " and ".join(reversed(names))
        out.append(s + ";")
    return NL.join(out)


check("至少有 4 个官方示例", len(ss.PRESETS) >= 4, str(len(ss.PRESETS)))
for p in ss.PRESETS:
    k, mk, sc = p["key"], p["market"], p["script"]
    r = ss.official_preset_of(sc, mk)
    check(f"{k} · 原文就是官方示例", r is not None and r["key"] == k and r["name"] == p["name"], str(r))
    check(f"{k} · 去掉注释、换行重排后仍是官方示例", (ss.official_preset_of(rebuild(sc), mk) or {}).get("key") == k)
    check(f"{k} · plot 里条件顺序颠倒仍是官方示例", (ss.official_preset_of(rebuild(sc, plot_reverse=True), mk) or {}).get("key") == k)
    other = [m for m in ("us", "a", "hk") if m != mk][0]
    check(f"{k} · 换到别的市场就不是", ss.official_preset_of(sc, other) is None)

    stmts = statements(sc)
    plot_i = [i for i, s in enumerate(stmts) if s.startswith("plot ")][0]
    plot = stmts[plot_i]
    m = re.match(r"^plot (\w+) = (.+)$", plot)
    names = [x.strip() for x in m.group(2).split(" and ")] if " and " in m.group(2) else []
    if len(names) >= 2 and all(re.fullmatch(r"\w+", x) for x in names):
        dropped = stmts[:plot_i] + [f"plot {m.group(1)} = " + " and ".join(names[1:])] + stmts[plot_i + 1:]
        check(f"{k} · 关掉一条(plot 少一个)就不是", ss.official_preset_of(NL.join(s + ";" for s in dropped), mk) is None)
        added = stmts[:plot_i] + ["def qa_extra = close > 1", f"plot {m.group(1)} = " + " and ".join(names + ['qa_extra'])] + stmts[plot_i + 1:]
        check(f"{k} · 追加一条就不是", ss.official_preset_of(NL.join(s + ";" for s in added), mk) is None)
    # 改一个数:把第一个出现的数字加 1
    changed = rebuild(sc)
    mnum = re.search(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])", changed.split(NL, 1)[0] + NL + changed.split(NL, 1)[1] if NL in changed else changed)
    if mnum:
        v = mnum.group(1)
        bumped = changed[:mnum.start()] + (str(int(v) + 1) if "." not in v else str(float(v) + 1)) + changed[mnum.end():]
        check(f"{k} · 改一个数就不是", ss.official_preset_of(bumped, mk) is None, v)

check("空脚本不是", ss.official_preset_of("", "us") is None)
check("写错的脚本不是(判不出来就当不是)", ss.official_preset_of("def a = close >;", "us") is None)
check("普通自写脚本不是", ss.official_preset_of("def c = close > 20;" + NL + "plot scan = c;", "us") is None)

print("ALL OK" if not failed else "SOME FAILED (%d)" % failed)
sys.exit(1 if failed else 0)
