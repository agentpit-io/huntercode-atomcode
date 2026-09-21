"""内部接口 · 把当前生效的大模型配置交给 opencode 容器(设计方案 3.3)。

opencode 启动时跑 `scripts/opencode/gen-config.py`,它要知道 provider 的地址、
key 和模型名才能写出配置文件。环境变量三项齐全时它自己就够了;**配置在数据库里
(向导填的)时只能来这儿问**。

## 为什么明文返回 api_key

opencode 拿这个 key 是要去调网关的,它需要的就是原值 —— 回一个打码值等于这个
接口白做。这条链路上的暴露面与改造前一致:

  · 鉴权走 `HUNTER_INTERNAL_KEY`(与 internal_tools.py 同一把口令、同一套写法)
  · api / opencode / llm-shim 三个容器**都不对外暴露端口**(compose 里只有 web
    映射了宿主端口),key 只在 docker 网络内部流动
  · 改造前这个 key 本来就以明文写在 opencode 的 `opencode.json` 里,
    `GET /config` 也明文回显(R0 §1.5)—— 不是新增的风险面

但**不要把这个接口代理给浏览器**。向导前端要看 key 的时候,走 M2 的
`/api/setup/status`,那边只给打码值。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from app.services import runtime_config

router = APIRouter(prefix="/internal", tags=["runtime-config"])

_INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "")


def _auth(request: Request) -> None:
    """与 internal_tools._auth 同一套:共享 secret 对不上就 401。"""
    key = request.headers.get("X-Hunter-Internal-Key", "")
    if key != _INTERNAL_KEY:
        raise HTTPException(401, "internal auth failed")


@router.get("/runtime/llm")
async def get_runtime_llm(request: Request) -> dict:
    """当前生效的大模型配置。`configured=False` 时调用方按「尚未配置」处理。

    **不要在这里替调用方兜底猜一个地址或模型名** —— 猜出来的结果是把用户的数据
    发到别人的服务器上,或者收到一个看不懂的 404。没配就是没配。
    """
    _auth(request)
    cfg = runtime_config.llm()
    # 日志里只说来源与有没有 key,不打印值
    logger.info("[internal] /runtime/llm · configured={} source={} model={} apiKey={}",
                cfg.configured, cfg.source, cfg.model or "(空)", "有" if cfg.api_key else "无")
    return {
        "configured": cfg.configured,
        "source": cfg.source,
        "base_url": cfg.base_url,
        "api_key": cfg.api_key,      # ⚠ 明文 · 见模块文档
        "model": cfg.model,
        "sanitize": cfg.sanitize,
    }
