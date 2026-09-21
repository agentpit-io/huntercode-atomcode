"""用户 SKILL 导出接口 · `/api/internal/skills/{key}/*`

## 要解决的问题

用户在界面里装的 SKILL 由 api 写进 `HUNTER_USER_SKILLS_DIR`
(默认 `/opt/hunter-user-skills`)。本地 compose 下 api 和 opencode
bind mount **同一个宿主目录**,所以 opencode 看得到。
但云平台(Zeabur / Sealos / Railway …)上两个服务**不能共用一个卷** ——
用户装完 SKILL,侧栏里有了,模型却说没有这个能力。

## 做法(R0 实测结论,见 `docs/setup-wizard/R0-预研结论.md` §2)

设计方案 3.5 原本要在 huntercode 里写一个同步插件。实测发现**不用写**:
opencode 原生就支持从 URL 拉 SKILL(`packages/opencode/src/skill/discovery.ts`
的 `Discovery.pull(base)`)。所以改成 api 把用户目录**原样导出成静态清单**,
opencode 配 `skills.urls` 自己来拉:

    "skills": { "urls": ["http://api:8000/api/internal/skills/<内部口令>/"] }

    GET <base>index.json          → {"skills":[{"name","files","version"}]}
    GET <base><name>/<file>       → 文件原始字节

opencode 侧的行为(源码实测,不是推测):
  · `version` 不变 → 只补本地缺失的文件,不重下 —— 所以 version 必须内容敏感
  · `version` 变了 → 下到 staging 目录,全部就位后 **原子 rename** 换版,
    换版过程中模型读到的始终是完整的旧版本
  · files 里不含 `SKILL.md` 的条目直接跳过 —— 所以清单里不放这种
  · `pull` 只返回**本次清单里**的目录,删掉的 SKILL 下次 refresh 就不再被扫到
  · api 写完 SKILL 后照旧调 `POST /skill/refresh`(`opencode_admin.refresh_skills`),
    实测写入 → 模型可见 **0.07 秒**

## 鉴权为什么放在 URL 路径段里

`Discovery.pull` 用的是**裸 GET**(`HttpClientRequest.get` + `acceptJson`),
带不了任何自定义头 —— `X-Hunter-Internal-Key` 这条路走不通。
所以口令只能作为路径的一段。配合「api 在 compose 网络内部、不对外暴露端口」
这个前提,风险与其它 `/api/internal/*` 接口一致。

两条必须守住的线:
  · 比对用 `secrets.compare_digest`(常数时间),不给计时侧信道
  · 不匹配返回 **404 而不是 401** —— 401 等于告诉扫描者「这个路径存在,
    只是口令不对」,而 404 和「压根没这个接口」无法区分。
    `HUNTER_INTERNAL_KEY` 没配置时同理:整个接口直接 404,
    **绝不能退化成「没设口令就不校验」**。

## 只导出用户 SKILL

内置 SKILL(`HUNTER_SKILLS_DIR`)**不在这里导出** —— 它们已经随镜像打进了
opencode 的 `/opt/opencode-workspace/.opencode/skills/`。重复导出会让同一个
SKILL 在模型那里出现两次,而且两份可能还不是同一个版本。
"""
from __future__ import annotations

import mimetypes
import os
import secrets

from fastapi import APIRouter, HTTPException, Response
from loguru import logger

from app.services import skill_files

router = APIRouter(prefix="/internal/skills", tags=["internal-skills"])

# 这个 404 的 detail 对所有失败原因都一样 —— 口令错、SKILL 不存在、
# 路径越界,外面看到的必须是同一句话,否则又成了信息泄露。
_NOT_FOUND = "not found"


def _check_key(key: str) -> None:
    """常数时间比对内部口令。不匹配 / 未配置 → 404。

    在函数里读 env 而不是模块级常量:测试要能改环境变量重跑,
    而且部署时也可能在进程启动后才注入(boot.sh 生成口令的场景)。
    每请求一次 `os.getenv` 的开销可以忽略。
    """
    expect = os.getenv("HUNTER_INTERNAL_KEY", "")
    if not expect:
        # 没有口令就不该开放这个接口。**不要**在这里放行 ——
        # 一个「没配置就人人可读」的接口比没有这个接口危险得多。
        logger.warning("[internal.skills] HUNTER_INTERNAL_KEY 未配置 · 导出接口保持关闭")
        raise HTTPException(404, _NOT_FOUND)
    # compare_digest 对 str 要求纯 ASCII,口令里有非 ASCII 字符会抛 TypeError,
    # 所以统一按字节比。长度不同时它也是常数时间返回 False。
    if not secrets.compare_digest((key or "").encode("utf-8"), expect.encode("utf-8")):
        logger.warning("[internal.skills] 口令不匹配 · 返回 404")
        raise HTTPException(404, _NOT_FOUND)


@router.get("/{key}/index.json")
async def skills_index(key: str):
    """导出清单。opencode 的 `Discovery.pull` 第一个拉的就是它。

    schema 必须**逐字**是 `{"skills":[{"name","files","version"}]}` ——
    opencode 侧用 effect Schema 严校验,多一层包装就会整份解析失败,
    表现是它记一条 `failed to fetch index` 然后当作没有任何 SKILL。
    """
    _check_key(key)
    skills = skill_files.export_index()
    logger.info("[internal.skills] 导出清单 · {} 个用户 SKILL", len(skills))
    return {"skills": skills}


@router.get("/{key}/{name}/{path:path}")
async def skill_file(key: str, name: str, path: str):
    """单个文件的原始字节。

    `SKILL.md` 固定 `text/markdown`(清单里一定有它,是 opencode 判断
    「这个条目算不算数」的依据);其余按扩展名猜,猜不出按二进制给。
    opencode 拿到的是 arrayBuffer,写盘时不看 Content-Type,
    所以这里的 media_type 只影响人用 curl 调试时的观感 —— 但仍然要给对。
    """
    _check_key(key)
    f = skill_files.resolve_user_skill_file(name, path)
    if f is None:
        # name 非法 / 文件不存在 / 路径越界 / 超过单文件上限 —— 对外一律 404。
        # ⚠️ 日志里**不要**打 request.url.path:那里面带着内部口令。
        logger.info("[internal.skills] 文件不可导出 name={!r} path={!r}", name[:64], path[:128])
        raise HTTPException(404, _NOT_FOUND)

    if f.name == "SKILL.md":
        media = "text/markdown; charset=utf-8"
    else:
        guessed, _ = mimetypes.guess_type(f.name)
        media = guessed or "application/octet-stream"
        if media.startswith("text/") and "charset" not in media:
            media += "; charset=utf-8"

    try:
        data = f.read_bytes()
    except OSError as e:
        logger.warning("[internal.skills] 读失败 {}/{}: {}", name[:64], path[:128], e)
        raise HTTPException(404, _NOT_FOUND)
    # 二进制原样返回,不做任何编码转换 —— SKILL 目录里可能有图片/字体
    return Response(content=data, media_type=media)
