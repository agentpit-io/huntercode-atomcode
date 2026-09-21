"""风控 3 方辩论 · Sprint B · TradingAgents 移植

3 位风控分析师从不同风险偏好角度评估综合判官的决策 · 各自独立发言:
  · risk_aggressive    · 激进派 · 主张放大仓位/加杠杆/延长持有 · 关注机会成本
  · risk_neutral       · 中性派 · 平衡收益与风险 · 常规仓位与止损
  · risk_conservative  · 保守派 · 主张缩仓/严格止损/快速离场 · 关注下行

各自看到:
  - 综合判官的决策 (BUY / HOLD / SELL + 置信度 + investment_plan)
  - 技术面 + 新闻面情报 + 多空辩论历史
  - 其他两位的前序发言(轮次内)

不使用 Deep Think (Flash 就够 · 只用于风控视角论证 · 综合裁决走 Deep Think)
"""
import os
from loguru import logger

from agents.translation import ensure_chinese
from openai import OpenAI

from agents.state import EnhancedAgentState


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


def _call_llm(system: str, user: str, max_tokens: int = 3500) -> str:
    """内部通用 LLM 调用 · 返 plain text · 与 bull/bear_researcher 同构"""
    # 地址 / key / 模型名见 _resolve_llm() —— 环境变量非空优先,否则读数据库。
    # timeout 60 → 120 因为推理型模型 reasoning tokens 一多就 40-60s+。
    base_url, api_key, model = _resolve_llm()
    if not (base_url and api_key and model):
        logger.warning("risk_perspective: 大模型尚未配置(base_url {} · key {} · model {})"
                       " · 请在 .env 里填 LLM_BASE_URL / LLM_API_KEY / LLM_DEFAULT_MODEL,"
                       "或在首页完成初始化向导",
                       "有" if base_url else "无", "有" if api_key else "无",
                       "有" if model else "无")
        return "（风控分析暂不可用）"
    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=120)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # 默认 3500 是给 DeepSeek-R1 等推理型模型的 reasoning tokens 留余量,
            # 老值 700 全被 reasoning 吃完 · message.content 返空。
            max_tokens=max_tokens,
            temperature=0.6,
        )
        content = resp.choices[0].message.content or ""
        if not content.strip():
            finish = resp.choices[0].finish_reason if resp.choices else "unknown"
            usage  = resp.usage
            logger.warning(
                "risk_perspective: content 空 · finish={} tokens_in={} tokens_out={} model={}",
                finish,
                usage.prompt_tokens if usage else "?",
                usage.completion_tokens if usage else "?",
                model,
            )
            return "（风控分析暂不可用）"
        # 语言守卫：跑成英文就净化/翻译，拿不到中文再落占位（见 agents/translation.py）
        return ensure_chinese(content) or "（风控分析暂不可用）"
    except Exception as e:
        logger.warning("risk_perspective LLM call failed: {}", e)
        return "（风控分析暂不可用）"


def _decision_cn(d: str) -> str:
    return {"BUY": "买入", "SELL": "卖出", "HOLD": "持有"}.get(d, d)


def _common_context(state: EnhancedAgentState, judgment: dict) -> str:
    decision = judgment.get("decision", "HOLD")
    confidence = judgment.get("confidence", 0.5)
    return (
        f"股票:{state.stock_name}({state.ticker})\n"
        f"综合判官决策:**{_decision_cn(decision)}** · 置信度 {int(float(confidence) * 100)}%\n"
        f"判官核心理由:{judgment.get('key_reason', '')}\n"
        f"投资计划:{judgment.get('investment_plan', '')[:300]}\n"
        f"止损参考:{judgment.get('stop_loss_hint', '(未给出)')}\n\n"
        f"【多头核心论点】{judgment.get('bull_summary', '')}\n"
        f"【空头核心论点】{judgment.get('bear_summary', '')}\n\n"
        f"【Sentinel 新闻研判】{state.sentinel_opinion} · 置信度 {int(state.sentinel_confidence * 100)}%\n"
        f"【技术面摘要】{(state.market_report or '')[:400]}\n"
    )


def run_risk_aggressive(state: EnhancedAgentState, judgment: dict, prior_debate: str = "") -> str:
    system = (
        "你是【激进派风控分析师】· 使用简体中文 · 直接输出分析文本(非 JSON)。\n\n"
        "你的立场:市场存在被低估的机会 · 判官的置信度可能偏保守 · 主张:\n"
        "  · 若判官建议 BUY → 论证是否可加大仓位/降低止损位/延长持有周期\n"
        "  · 若判官建议 HOLD → 论证是否可转为 BUY · 抓住反弹机会\n"
        "  · 若判官建议 SELL → 论证是否为过度反应 · 部分保留观察\n"
        "  · 关注机会成本 · 强调 fear of missing out\n"
        "你必须给出量化的仓位/止损/止盈建议(如 +5% 仓位 / 止损放宽至 -8%)。\n"
        "300 字以内 · 犀利有力 · 直接反驳保守派可能提的过度谨慎。"
    )
    user = _common_context(state, judgment)
    if prior_debate:
        user += f"\n【当前轮次已有的其他风控发言】\n{prior_debate}\n"
    user += "\n请发表激进派风控意见。"
    return _call_llm(system, user)


def run_risk_neutral(state: EnhancedAgentState, judgment: dict, prior_debate: str = "") -> str:
    system = (
        "你是【中性派风控分析师】· 使用简体中文 · 直接输出分析文本(非 JSON)。\n\n"
        "你的立场:在激进与保守之间寻求平衡 · 采用标准仓位与止损:\n"
        "  · 认可判官的核心判断 · 但会指出具体执行细节的风险点\n"
        "  · 主张常规仓位(单只股票 5-15%) · 常规止损(-5% ~ -10%)\n"
        "  · 明确指出激进派 vs 保守派各自的盲区\n"
        "  · 强调纪律性 · 反对情绪化操作\n"
        "你必须给出可执行的中庸方案(具体仓位百分比 · 具体止损位)。\n"
        "300 字以内 · 客观理性 · 不激进不保守。"
    )
    user = _common_context(state, judgment)
    if prior_debate:
        user += f"\n【当前轮次已有的其他风控发言】\n{prior_debate}\n"
    user += "\n请发表中性派风控意见。"
    return _call_llm(system, user)


def run_risk_conservative(state: EnhancedAgentState, judgment: dict, prior_debate: str = "") -> str:
    system = (
        "你是【保守派风控分析师】· 使用简体中文 · 直接输出分析文本(非 JSON)。\n\n"
        "你的立场:资金保全优先 · 判官的置信度可能被牛市/新闻情绪推高 · 主张:\n"
        "  · 若判官建议 BUY → 论证是否应减少仓位/收紧止损/缩短持有\n"
        "  · 若判官建议 HOLD → 论证是否应转为部分 SELL · 落袋为安\n"
        "  · 若判官建议 SELL → 论证是否应加速全部离场\n"
        "  · 强调 fat tail 风险 · 强调回撤对复利的破坏\n"
        "你必须给出量化的仓位/止损建议(如 -5% 仓位 / 止损收紧至 -3%)。\n"
        "300 字以内 · 冷静警惕 · 直接反驳激进派可能提的乐观论点。"
    )
    user = _common_context(state, judgment)
    if prior_debate:
        user += f"\n【当前轮次已有的其他风控发言】\n{prior_debate}\n"
    user += "\n请发表保守派风控意见。"
    return _call_llm(system, user)
