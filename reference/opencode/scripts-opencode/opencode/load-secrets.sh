#!/bin/sh
# opencode 容器 · 从密钥卷读 JWT_SECRET / HUNTER_INTERNAL_KEY(设计方案 3.2)
#
# **这个脚本必须被 `.`(source)进去,不能直接执行** —— 直接执行的话 export 只
# 作用于子进程,opencode 本体还是拿不到值。在 entrypoint.sh 开头加一行:
#
#     . /opt/hunter-boot/load-secrets.sh
#
# 为什么 opencode 需要这两个值:
#   · JWT_SECRET         —— hunter-auth 插件用它验 api 签发的 token。两边不一致
#                           时服务照常起来,只是对话莫名 401。
#   · HUNTER_INTERNAL_KEY —— MCP 回调 /api/internal/* 的共享口令,gen-config.py
#                           还要拿它去 api 拉大模型配置、拼 skills.urls。
#
# 优先级:**环境变量非空 → 密钥卷**。云平台(Zeabur/Sealos/Railway)靠模板把同
# 一个随机值注进每个服务的环境变量,那边压根没有共享卷;本地 compose 则由 api
# 首启时生成并写进 hunter_secrets 卷,这里只读挂载同一个卷。
#
# ⚠️ 判断用「非空」不是「已定义」:compose 的 `${JWT_SECRET:-}` 会把「用户没设」
# 变成空字符串注入容器。
#
# 读不到不报错、不退出:opencode 还是要起来(用户得能打开首页跑初始化向导)。
# 对不上的后果只是 401,而 401 有日志;起不来则连日志都难拿到。

HUNTER_SECRETS_FILE="${HUNTER_SECRETS_FILE:-${HUNTER_SECRETS_DIR:-/opt/hunter-secrets}/secrets.env}"

# 不用 `.`(source)这个文件 —— source 会让文件里的值盖掉环境变量里的值,
# 而优先级正好相反。按行取值,文件格式是最朴素的 `KEY=value`(见 apps/api/boot.sh)。
_hunter_read_secret() {
    [ -r "$HUNTER_SECRETS_FILE" ] || return 0
    sed -n "s/^$1=//p" "$HUNTER_SECRETS_FILE" | head -n 1
}

_hunter_jwt_src='环境变量'
_hunter_int_src='环境变量'

if [ -z "${JWT_SECRET:-}" ]; then
    JWT_SECRET="$(_hunter_read_secret JWT_SECRET)"
    _hunter_jwt_src='密钥卷'
fi
if [ -z "${HUNTER_INTERNAL_KEY:-}" ]; then
    HUNTER_INTERNAL_KEY="$(_hunter_read_secret HUNTER_INTERNAL_KEY)"
    _hunter_int_src='密钥卷'
fi

export JWT_SECRET HUNTER_INTERNAL_KEY

# 只打来源与长度,**绝不打印密钥本身**
if [ -z "$JWT_SECRET" ]; then
    echo "[load-secrets] WARN 没拿到 JWT_SECRET(环境变量为空,$HUNTER_SECRETS_FILE 也读不到)。" >&2
    echo "[load-secrets] WARN   opencode 将无法验证 api 签发的 token —— 对话会 401。" >&2
    echo "[load-secrets] WARN   处理:确认 compose 里 opencode 挂了 hunter_secrets 卷(只读即可)," >&2
    echo "[load-secrets] WARN   且 api 已成功启动过一次(密钥由它生成并写入)。" >&2
else
    echo "[load-secrets] JWT_SECRET 来源=$_hunter_jwt_src 长度=${#JWT_SECRET}" \
         "· HUNTER_INTERNAL_KEY 来源=$_hunter_int_src 长度=${#HUNTER_INTERNAL_KEY}" >&2
fi

unset _hunter_jwt_src _hunter_int_src
