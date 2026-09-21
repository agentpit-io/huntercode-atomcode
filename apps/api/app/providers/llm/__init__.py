"""LLM provider factory · 配置来自 runtime_config(环境变量非空 → 数据库)。

Set LLM_PROVIDER to one of:
  - openai_compat (default · works with OpenAI · OpenRouter · OneAPI · DeepSeek)
  - anthropic     (direct Claude · requires `anthropic` pip package)
  - saas_gemini   (alias for openai_compat pointed at hunter's Gemini gateway)

地址 / key / 模型名三项由 `app.services.runtime_config.llm()` 解析:
`.env` 里的 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_DEFAULT_MODEL` 非空时优先,
否则读数据库(初始化向导写入)。**没配就是没配**,这里不给任何默认值 ——
猜一个模型名(以前是 `gpt-4o-mini`)只会让用户收到一个看不懂的 404。

⚠️ 单例按配置内容缓存,不是按进程缓存:向导保存之后配置会在运行时变,
按进程缓存的话得重启容器才生效(而向导的卖点就是不用重启)。
"""
from loguru import logger

from .base import ILLM

_INSTANCE: ILLM | None = None
_INSTANCE_KEY: tuple | None = None


def get_llm() -> ILLM:
    import os

    from app.services.runtime_config import llm as _runtime_llm

    provider = (os.getenv("LLM_PROVIDER") or "openai_compat").lower()
    cfg = _runtime_llm()
    base_url, api_key, default_model = cfg.base_url, cfg.api_key, cfg.model

    # 配置没变就复用同一个实例(建 client 要开连接池,不该每次调用都建)。
    global _INSTANCE, _INSTANCE_KEY
    key = (provider, base_url, api_key, default_model)
    if _INSTANCE is not None and _INSTANCE_KEY == key:
        return _INSTANCE

    logger.info("[providers.llm] loading provider={} · 配置来源={} · model={}",
                provider, cfg.source, default_model or "(未配置)")

    if provider in ("openai_compat", "saas_gemini"):
        if not base_url:
            raise RuntimeError(
                "大模型尚未配置:缺 base_url。在 .env 里填 LLM_BASE_URL"
                "(如 https://api.openai.com/v1),或在首页完成初始化向导。"
            )
        if not default_model:
            raise RuntimeError(
                "大模型尚未配置:缺模型名。在 .env 里填 LLM_DEFAULT_MODEL,"
                "或在首页完成初始化向导。"
            )
        from .openai_compat import OpenAICompatLLM
        inst: ILLM = OpenAICompatLLM(base_url, api_key, default_model)
    elif provider == "anthropic":
        if not api_key:
            raise RuntimeError("LLM_PROVIDER=anthropic requires LLM_API_KEY")
        from .anthropic_impl import AnthropicLLM
        inst = AnthropicLLM(
            api_key,
            base_url=base_url or None,
            default_model=default_model or "claude-sonnet-4-6",
        )
    else:
        raise RuntimeError(
            f"unknown LLM_PROVIDER={provider!r} · "
            "expected one of: openai_compat | anthropic | saas_gemini"
        )
    _INSTANCE, _INSTANCE_KEY = inst, key
    return inst


__all__ = ["ILLM", "get_llm"]
