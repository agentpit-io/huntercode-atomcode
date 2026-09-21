"""BearResearcher — 空头辩论节点

Sentinel 已验证利空事实注入辩论 prompt，明确区分真实利空与投毒噪音。
"""
import os
from loguru import logger

from agents.translation import ensure_chinese
from openai import OpenAI

from agents.state import EnhancedAgentState, DebateState


def run_bear_researcher(state: EnhancedAgentState, debate: DebateState) -> DebateState:
    """一轮空头辩论，更新 DebateState"""
    verified_str = _format_facts(state.verified_facts)
    filtered_str = _format_filtered(state.filtered_facts)

    system = (
        "你是一位空头分析师，主张卖出/回避该股票。使用简体中文输出所有内容。\n\n"
        "【重要约束】：\n"
        f"Sentinel 新闻系统已分析过以下信息：\n"
        f"已验证真实事实（可引用作为证据）：\n{verified_str}\n\n"
        f"以下内容已被判定为投毒/低质新闻（禁止引用作为证据）：\n{filtered_str}\n\n"
        "在你的空头论证中，必须区分真实利空和噪音：\n"
        "- 用已验证的事实支撑空头逻辑\n"
        "- 对被过滤内容如需提及，必须注明'此信息已被 Sentinel 过滤，可信度低'"
    )

    sentiment_note = ""
    if state.sentinel_opinion == "看多":
        sentiment_note = (
            f"\n注意：Sentinel 新闻面综合研判为「{state.sentinel_opinion}」"
            f"（置信度 {int(state.sentinel_confidence*100)}%）。"
            "你需要解释为什么尽管新闻面偏正面，但技术面或其他风险仍支撑空头逻辑。"
        )

    user = (
        f"股票：{state.stock_name}（{state.ticker}）\n"
        f"当日涨跌：{state.change_pct:+.2f}% | 触发条件：{state.trigger_desc}\n\n"
        f"技术面分析：\n{state.market_report}\n\n"
        f"Sentinel 新闻情报摘要：\n{state.sentinel_report}\n"
        f"{sentiment_note}\n\n"
        f"当前辩论历史：\n{debate.history}\n\n"
        f"多头上一轮论点：\n{debate.current_response}\n\n"
        "请发表有说服力的空头论点，明确区分真实利空与被过滤噪音，约 300-400 字。"
    )

    response = _call_llm(user)
    argument = f"【空头分析师】：{response}"

    return DebateState(
        history=debate.history + "\n" + argument,
        bull_history=debate.bull_history,
        bear_history=debate.bear_history + "\n" + argument,
        current_response=argument,
        count=debate.count + 1,
    )


def _format_facts(facts: list) -> str:
    if not facts:
        return "（暂无已验证事实）"
    return "\n".join(
        f"  {i}. [{f.get('source','')}] {f.get('fact','')} （可信度 {f.get('weight',0):.2f}）"
        for i, f in enumerate(facts, 1)
    )


def _format_filtered(facts: list) -> str:
    if not facts:
        return "（无被过滤内容）"
    return "\n".join(f"  - {f.get('text','')}" for f in facts)


def _resolve_llm() -> tuple[str, str, str]:
    """(base_url, api_key, model) · ONE_API_* 优先,否则 runtime_config
    (环境变量非空 → 数据库 · 初始化向导写的)。

    **在函数里现取,不做模块级常量** —— 向导改完配置后 api 进程不重启也要生效。
    **不给 base_url / model 任何默认值**:原来的默认地址是我们自己演示站的网关
    (104.197.139.51:3000),开源用户没配 LLM_BASE_URL 时,他的数据会被发到我们
    的服务器上;模型名猜一个 gemini-3.5-flash 同理只会换来一个看不懂的 404。
    """
    from app.services.runtime_config import llm as _runtime_llm

    cfg = _runtime_llm()
    return ((os.getenv("ONE_API_BASE_URL") or "").strip() or cfg.base_url,
            (os.getenv("ONE_API_KEY") or "").strip() or cfg.api_key,
            (os.getenv("ONE_API_MODEL") or "").strip() or cfg.model)


def _call_llm(user: str) -> str:
    system = "你是一位专业的空头股票分析师，使用简体中文，直接输出分析文本（非 JSON）。"
    # 地址 / key / 模型名见 _resolve_llm() —— 环境变量非空优先,否则读数据库。
    # timeout 60 → 120 因为推理型模型 reasoning tokens 一多就 40-60s+。
    base_url, api_key, model = _resolve_llm()
    if not (base_url and api_key and model):
        logger.warning("bear_researcher: 大模型尚未配置(base_url {} · key {} · model {})"
                       " · 请在 .env 里填 LLM_BASE_URL / LLM_API_KEY / LLM_DEFAULT_MODEL,"
                       "或在首页完成初始化向导",
                       "有" if base_url else "无", "有" if api_key else "无",
                       "有" if model else "无")
        return "（空头分析暂不可用）"
    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=120)
        resp  = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            # DeepSeek-R1 等推理型模型 reasoning tokens 常 2000-5000+,
            # 老值 800 全被 reasoning 吃完 · message.content 返空。抬到 4096
            # 与 bull_researcher 对齐,同时补 finish_reason/usage 便于排错。
            max_tokens=4096,
            temperature=0.7,
        )
        content = resp.choices[0].message.content or ""
        if not content.strip():
            finish = resp.choices[0].finish_reason if resp.choices else "unknown"
            usage  = resp.usage
            logger.warning(
                "bear_researcher: content 空 · finish={} tokens_in={} tokens_out={} model={}",
                finish,
                usage.prompt_tokens if usage else "?",
                usage.completion_tokens if usage else "?",
                model,
            )
            return "（空头分析暂不可用）"
        # 语言守卫：跑成英文就净化/翻译，拿不到中文再落占位（见 agents/translation.py）
        return ensure_chinese(content) or "（空头分析暂不可用）"
    except Exception as e:
        logger.warning("bear_researcher LLM call failed: {}", e)
        return "（空头分析暂不可用）"
