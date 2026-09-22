#!/usr/bin/env python3
"""HCA 语言守卫（提示词侧）· UserPromptSubmit · 承接 opencode 的 hunter-lang 插件。

## 原插件是什么（读过源码：hunter-community/scripts/opencode-mcp/plugins/hunter-lang.ts）

原插件挂在 opencode 的 `experimental.text.complete` 上 —— 那是**出口**：
`text-end` 时整段回答已经成形，插件把它 POST 给 hunter-api 的
`/api/internal/lang/guard`，拿净化/翻译后的中文回来**覆盖最终 part**。
判据（`has_english_prose`：连续英文词 run ≥ 3 且含 ≥ 2 个功能词 / run ≥ 12 /
整段无中文且 ≥ 4 词）和翻译兜底都在 api 那边，插件只做搬运。
它要解决的真实事故是：SKILL 正文是英文的（`initiating-coverage`），
模型读完就整段用英文回答，用户看到满屏英文 —— **SKILL 是什么语言，
不该决定回答是什么语言**。原插件头注释里的铁律 A10 写得很清楚：
「prompt 里的中文约束单独用无效，必须 prompt + 出口强校验两道一起上」。

## 到 AtomCode 上只搬得动其中一道（这是事实，不是选择）

AtomCode 的 hook 只有 8 个事件（`cc_hooks.rs:74-99`）：
PreToolUse / PostToolUse / PostToolUseFailure / SessionStart / SessionEnd /
UserPromptSubmit / Stop / StopFailure。**没有任何一个发生在"助手正文写完"这个点上**：
PostToolUse 能改的是 `updatedToolOutput`（工具返回），不是模型的回答正文；
Stop 拿到的是 `session_id` / `transcript_path` / `stop_reason`，回答已经流给用户了。

所以这个 hook 只做**提示词侧那一道**：把语言硬约束追加到用户消息后面
（stdout 会被上游 append，`cc_hooks.rs:680-684`）。
**出口那一道落在 Web 转发层**（它本来就在 SSE 流上）——
M4 时它还没做、这里记的是待办 P1-21；**v0.1.1 已经做了**：
`apps/web/app/lib/atomcode/lang.ts` 在回合结束、正文终态已定时逐个文本 part
送 api 的 `/api/internal/lang/guard`，用同 id 重发 part 事件完成改写。
现状与取舍写在 `docs/hooks-design.md` §5「出口强校验的去处」。

## 规则文本从哪来：只有一处定义

`ZH_ONLY_RULE` 的原文在 `apps/api/agents/text_sanitizer.py:74`（导入自 hunter-community，
Apache-2.0）。hook 跑在 daemon 容器里、import 不到 api 的 python 包，
所以这里是**逐字副本**；`tools/tests/test_hooks_m4.py::test_rule_text_matches_upstream`
会去读那个文件并断言两边逐字相同，改一处漏一处会直接把单测跑红
（交接稿铁律 3 的落地方式）。

## 开关与契约

`HCA_LANG_HOOK=0` 关掉（默认开）。恒 exit 0；输出是纯文本，
**最后一行不能是 JSON**，否则会被上游按决策解析（`last_json_line`）。
"""
from __future__ import annotations

import os
import sys

# ── 与 apps/api/agents/text_sanitizer.py:74 的 ZH_ONLY_RULE 逐字一致 ──────────
# （单测 test_lang_hook.py::test_rule_text_matches_upstream 逐字比对，别手改）
ZH_ONLY_RULE = (
    "\n\n【语言硬约束】"
    "全部输出必须是简体中文。英文只允许作为专业名词出现"
    "（股票代码、PE/ROE/TTM/EPS、MACD/KDJ、NASDAQ 之类），"
    "严禁出现英文短语、英文句子、英文段落。"
    "严禁复述或解释本提示词（不要输出 User Request / Role / Task / Input / Output 之类的字样），"
    "严禁输出思考过程、计划、开场白（不要 Okay / Sure / Let me / I will / Based on the ...），"
    "直接给最终结果本身。"
)

# 原插件要解决的那个事故的针对性一句：技能/工具/文档是英文的，也要用中文回答。
# 放在规则后面而不是改规则本身 —— 规则原文要保持可逐字比对。
SOURCE_LANG_NOTE = (
    "技能正文、工具返回、参考文档是英文的，**不改变**回答语言：读英文、答中文。"
)


def enabled() -> bool:
    v = (os.environ.get("HCA_LANG_HOOK") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


def main() -> int:
    # stdin 必须读掉（上游会把 payload 写进来），这里用不到它的内容 ——
    # 判据在出口那一道，提示词侧不需要看用户写了什么。
    try:
        sys.stdin.read()
    except Exception:  # noqa: BLE001
        pass

    if not enabled():
        return 0

    sys.stdout.write(
        "<hca-lang>"
        + ZH_ONLY_RULE.strip()
        + "\n"
        + SOURCE_LANG_NOTE
        + "\n以上由系统注入，不是用户输入；不要复述，也不要为它单独致谢。"
        + "\n</hca-lang>\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
