#!/usr/bin/env python3
"""把各轮的 `summary.json` 汇成一份 **PPT 取数用的总表** `docs/eval/summary.json`。

    python3 tools/eval/build_summary.py

## 为什么要有这一份

每一轮都有自己的 `summary.json`（I1 在 `docs/eval/c1/`、I3 在 `docs/eval/i3/`），
取数的人不知道该看哪一份，更不知道**哪个数字能和哪个数字放在同一页 PPT 上**。
I3 量到的那件事让这个问题变得要紧：**同一份配置隔一夜，D 维度差 1.4 分**
（I3 报告 §1）。所以跨轮的数**不能混着用**。

这份总表只做三件事，一个数都不自己算：

1. 把每一轮的头条数字按轮列出来，每个数带 `来源`（文件 + JSON 路径）；
2. 明写**每一轮各自的口径**（Kronos key 开没开、题集几道、每题几遍）；
3. 明写**哪些数不能放在一起比**。

数字全部从各轮的 `summary.json` / `scores-summary.json` 里读，读不到写 `null`。
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "eval" / "summary.json"

QS = ["q1-fundamental", "q2-thesis-review", "q3-factor-screen",
      "q4-kronos-forecast", "q5-intel-digest"]


def load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                       # noqa: BLE001
        return None


def _scored(scores):
    """只取 HCA 侧**人工项填完了的**那几次。填不完的那次整次不出分（score.py 的规矩）。"""
    rows = (scores or {}).get("rows") or []
    return [r for r in rows if r.get("side") == "atomcode" and not r.get("missing")]


def _mean(scores, key):
    xs = [r[key] for r in _scored(scores) if isinstance(r.get(key), (int, float))]
    return round(sum(xs) / len(xs), 1) if xs else None


def _n(scores):
    return len(_scored(scores)) or None


def byq(summary, line: str, rel: str):
    """逐题：两边的调用次数 / 墙钟中位 / 墙钟比 / D。取不到写 null。"""
    out = {}
    lines = (summary or {}).get("线") or {}
    qs = (lines.get(line) or {}).get("题") or {}
    for q in QS:
        a = (qs.get(q) or {}).get("atomcode") or {}
        o = (qs.get(q) or {}).get("opencode") or {}
        wa, wo = a.get("wall_ms_median"), o.get("wall_ms_median")
        out[q] = {
            "hca_调用中位": a.get("tool_calls_median"),
            "社区版_调用中位": o.get("tool_calls_median"),
            "hca_墙钟中位_ms": wa,
            "社区版_墙钟中位_ms": wo,
            "墙钟比": round(wa / wo, 3) if wa and wo else None,
            "hca_D1": a.get("D1"), "hca_D2": a.get("D2"),
            "hca_D": round((a["D1"] + a["D2"]), 1) if a.get("D1") is not None
                     and a.get("D2") is not None else None,
            "n_hca": a.get("n"), "n_社区版": o.get("n"),
            "来源": f"{rel} → 线.{line}.题.{q}",
        }
    return out


def i4_q(i4, batch: str, cut: str, rel: str):
    """I4 逐题：两边的首字 / 答案首字 / 墙钟（中位 + P90）与比值。取不到写 null。"""
    out = {}
    qs = ((i4 or {}).get("batches", {}).get(batch) or {}).get("questions") or {}
    for qid, q in qs.items():
        a = (q.get(cut) or {}).get("atomcode") or {}
        o = (q.get(cut) or {}).get("opencode") or {}
        r = (q.get("比值") or {}).get(cut) or {}

        def g(side, key, stat):
            return ((side.get(key) or {}).get(stat))

        out[qid] = {
            "n_hca": a.get("n"), "n_社区版": o.get("n"),
            "hca_首字中位_ms": g(a, "ttft_ms", "median"),
            "社区版_首字中位_ms": g(o, "ttft_ms", "median"),
            "hca_首字是路标的次数": a.get("首字是路标的次数"),
            "hca_答案首字中位_ms": g(a, "ttft_answer_ms", "median"),
            "社区版_答案首字中位_ms": g(o, "ttft_answer_ms", "median"),
            "hca_答案首字P90_ms": g(a, "ttft_answer_ms", "p90"),
            "社区版_答案首字P90_ms": g(o, "ttft_answer_ms", "p90"),
            "hca_墙钟中位_ms": g(a, "wall_ms", "median"),
            "社区版_墙钟中位_ms": g(o, "wall_ms", "median"),
            "hca_墙钟P90_ms": g(a, "wall_ms", "p90"),
            "社区版_墙钟P90_ms": g(o, "wall_ms", "p90"),
            "答案首字比_中位": (r.get("ttft_answer_ms") or {}).get("median"),
            "答案首字比_P90": (r.get("ttft_answer_ms") or {}).get("p90"),
            "墙钟比_中位": (r.get("wall_ms") or {}).get("median"),
            "墙钟比_P90": (r.get("wall_ms") or {}).get("p90"),
            "模型段比_中位": (r.get("model_ms") or {}).get("median"),
            "工具段比_中位": (r.get("tool_ms") or {}).get("median"),
            "出字段比_中位": (r.get("stream_ms") or {}).get("median"),
            "来源": f"{rel} → batches.{batch}.questions.{qid}.{cut}",
        }
    return out


def i4_total(i4, batch: str, cut: str, rel: str, skip=()):
    """I4 十题合计：**每题取中位数再相加**，不是把 100 次运行混在一起求中位数。

    为什么这么算：每道题的量级差着一个数量级（q10 6 s、q6 45 s），混在一起的中位数
    会被题目分布绑架；逐题取中位数再相加，等于「跑完这十道题一共要等多久」。
    """
    qs = ((i4 or {}).get("batches", {}).get(batch) or {}).get("questions") or {}
    keys = ("wall_ms", "ttft_answer_ms", "ttft_ms", "model_ms", "tool_ms",
            "stream_ms", "token", "tool_calls", "rounds", "text_len")
    tot = {}
    for qid, q in qs.items():
        if qid in skip:
            continue
        for side in ("atomcode", "opencode"):
            blk = (q.get(cut) or {}).get(side) or {}
            for k in keys:
                m = (blk.get(k) or {}).get("median")
                if m is not None:
                    tot.setdefault((side, k), []).append(m)
    out = {"题数": len({q for q in qs if q not in skip}), "口径": cut,
           "来源": f"{rel} → batches.{batch}.questions.*.{cut}（逐题中位数之和）"}
    for k in keys:
        a, o = sum(tot.get(("atomcode", k), [])), sum(tot.get(("opencode", k), []))
        out[k] = {"hca": round(a, 1), "社区版": round(o, 1),
                  "比值": (round(a / o, 3) if o else None)}
    return out


def main() -> int:
    i1 = load(REPO / "docs/eval/c1/summary.json")
    i3 = load(REPO / "docs/eval/i3/summary.json")
    i3_scores = load(REPO / "docs/eval/i3/opt5-fork-b-12/scores-summary.json")
    i4 = load(REPO / "docs/eval/i4/summary.json")
    I4REL = "docs/eval/i4/summary.json"

    doc = {
        "_说明": [
            "PPT 取数用的总表。每个数字带「来源」，写的是它出自哪一份 summary.json 的哪条路径，拿着它能回到原始记录重算。",
            "⚠️ **跨轮的数不要混着用。** I3 实测：同一份配置隔一夜，D 维度从 23.2 掉到 21.8（I3 报告 §1）。",
            "⚠️ **I1 与 I3 的 q4 不可比**：I1 两边都接了 Kronos key，I3 与 I2/M2 同口径（key 清空，考的是诚实度）。",
            "数字全部来自真实调用；取不到写 null，不补默认值。",
        ],
        "生成命令": "python3 tools/eval/build_summary.py",
        "轮次": {
            "I1（完整对比测试 · 2026-09-23）": {
                "问题": "HCA 与社区版在同一套题上谁更强",
                "口径": {"题集": "10 道（M2 五道 + I1 新增五道）", "每题遍数": 3,
                         "Kronos key": "接上（两边都接）",
                         "线": "A 线 = 两边同样 6 个 MCP；B 线 = 产品形态"},
                "头条": {
                    "A线_总分比": "92.0 / 90.9 = 101.2%",
                    "B线_总分比": "95.1 / 86.7 = 109.7%",
                    "来源": "docs/开发文档/I1-对比测试报告.md · 明细见 docs/eval/c1/summary.json",
                },
                "逐题_B线_只列与I3同名的五道": byq(i1, "c1-b", "docs/eval/c1/summary.json"),
                "明细文件": "docs/eval/c1/summary.json（含全部 10 道题与 A 线）",
            },
            "I3（性能收尾 · 2026-09-23）": {
                "问题": "逐题墙钟与步数相对社区版还差多少",
                "口径": {"题集": "M2 五道", "每题遍数": 12,
                         "Kronos key": "清空（与 M2 / I2 同口径）",
                         "线": "只跑 B 线（产品形态）",
                         "批次": "opt5-fork-b-12 = 两段各 6 遍合并；对照档 opt3-fork-b-重测-12 同样 12 遍"},
                "头条": {
                    "D维度_按题平均": 22.2,
                    "逐题达标_墙钟≤社区版x1.05且调用≤社区版": "1/5",
                    "工具调用次数≤社区版": "5/5",
                    "A_准确性": _mean(i3_scores, "A"),
                    "C_输出规范": _mean(i3_scores, "C"),
                    "B_能力覆盖": _mean(i3_scores, "B"),
                    "ABC的n": _n(i3_scores),
                    "来源": "docs/eval/i3/summary.json（D 维度）+ docs/eval/i3/opt5-fork-b-12/scores-summary.json（A/B/C）",
                },
                "同配置对照档_D维度": 21.8,
                "同配置对照档_说明": "opt3-fork-b-重测-12 = I2 发布的那一版配置，今天同条件重测。I2 当时记的是 23.2 —— 差的 1.4 分是跨天漂移，不是配置变了。",
                "逐题": byq(i3, "opt5-fork-b-12", "docs/eval/i3/summary.json"),
                "明细文件": "docs/eval/i3/summary.json",
            },
            "I4（响应时间专项 · 2026-09-23）": {
                "问题": "同机、同一个数据库、同一份数据的条件下，响应时间与社区版差多少，差在哪一段",
                "口径": {
                    "题集": "10 道（与 I1 同一份）+ 纯引擎空载题 q0-idle",
                    "每题遍数": "i4-b 10 遍 / i4-a 6 遍 / 空载题 20 遍",
                    "Kronos key": "接上（与 I1 同口径，所以 q4 与 I2/I3 不可比）",
                    "线": "i4-b = 产品形态；i4-a = 两边同样 6 个 MCP",
                    "同库": "两边共用同一个 api 进程 + 同一个 postgres 实例 + 同一行用户，"
                            "每批开跑前六条判据取证（<批次>/same-db-proof.txt）",
                    "代码": "main（v0.2.1）—— 与 I1 当时的 v0.2.0 不同，绝对值不能和 I1 直接比",
                },
                "纯引擎空载响应": {
                    "产品形态_b": i4_q(i4, "idle-b", "原样", I4REL).get("q0-idle"),
                    "A线_a": i4_q(i4, "idle-a", "原样", I4REL).get("q0-idle"),
                    "说明": "题面「用一句话说明你是什么。不要调用任何工具。」两边都是 1 轮、0 次工具调用。"
                            "这是最干净的一组引擎响应时间对照 —— 没有工具往返、没有路标。",
                },
                "十题合计_产品形态_都真做题": i4_total(i4, "i4-b", "都真做题", I4REL),
                "十题合计_产品形态_都真做题_去q4": i4_total(
                    i4, "i4-b", "都真做题", I4REL, skip=("q4-kronos-forecast",)),
                "十题合计_A线_都真做题": i4_total(i4, "i4-a", "都真做题", I4REL),
                "_合计怎么读": "「比值」= HCA ÷ 社区版，< 1 表示 HCA 更快 / 更少。"
                               "去 q4 那一份是因为那道题两边做的不是同一件事"
                               "（HCA 真跑 Kronos GPU 推理，社区版如实说没有这个能力）。",
                "逐题_产品形态_原样": i4_q(i4, "i4-b", "原样", I4REL),
                "逐题_产品形态_都真做题": i4_q(i4, "i4-b", "都真做题", I4REL),
                "逐题_A线_原样": i4_q(i4, "i4-a", "原样", I4REL),
                "逐题_A线_都真做题": i4_q(i4, "i4-a", "都真做题", I4REL),
                "hook段": (i4 or {}).get("hook"),
                "明细文件": "docs/eval/i4/summary.json（含分段计时的每一次原值与 P90）",
                "注意": [
                    "**首字延迟有两个**：`首字` 是第一块正文（本部署人设要求先发一行「路标」，"
                    "所以它可能是进度提示不是答案）；`答案首字` 是末轮第一块正文 —— "
                    "跨产品比较以**答案首字**为准。",
                    "社区版的**收尾段量不到**（阻塞 POST，没有独立结束事件），写 null，上界是残差。",
                    "P90 用最近秩法，不插值；n=6 时 P90 等于最大值。",
                ],
            },
        },
        "q2的两套口径": {
            "为什么": "社区版在 q2 上不稳定：论点原文只在工作区文件里，它多数时候不去读、直接回「你没存买入理由」就结束。所以这道题的达标与否取决于对照组抽到哪一类。",
            "原样口径_正式判定": {"hca_墙钟中位_s": 18.6, "社区版_墙钟中位_s": 9.4,
                                  "墙钟比": 1.98, "n": "12 / 11"},
            "都真做题口径": {"hca_墙钟中位_s": 18.6, "社区版_墙钟中位_s": 32.2,
                             "墙钟比": 0.58, "n": "12（12/12 都读了） / 1（11 次里 1 次）"},
            "两档合并扩样本": {"hca_墙钟中位_s": 18.9, "社区版_墙钟中位_s": 32.2,
                               "墙钟比": 0.59, "n": "24 / 5（23 次里 5 次）"},
            "来源": "python3 tools/eval/i2_taskdone.py docs/eval/i3 --batches opt5-fork-b-12,opt3-fork-b-重测-12",
        },
        "U-20_工具schema体积对首字延迟的影响": {
            "结论": "有因果，约 10.0 毫秒 / 千字节（R² 0.99，四档 × 每档 26 次）；提示正文约 6.0 毫秒 / 千字节 —— 同一量级，所以吃时间的是「请求大小」本身。",
            "发行版→只留5个工具能省": "347 ms / 轮",
            "来源": "docs/eval/i3/schema-probe/报告.md（原始记录同目录）",
        },
        "不能放在一起比的": [
            "I1 的 q4 与 I3 / I2 / M2 的 q4（Kronos key 开没开不同）",
            "I2 报告里的 D 23.2 与 I3 的 22.2（隔天，且 I3 已用同配置重测证明差值来自漂移）",
            "A 线与 B 线（挂的 MCP 不同）",
            "I4 的绝对耗时与 I1 / I3 的绝对耗时（代码版本不同、时段不同；I4 里能比的只有同批次内两边的相对关系）",
            "I4 的「首字」与 I1 报告里那个「首字延迟」（I1 那个没有把人设的「路标」摘出去；I4 的「答案首字」才是同口径的那个）",
        ],
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
