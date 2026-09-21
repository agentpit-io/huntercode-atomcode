#!/bin/sh
# web 容器入口 · 只做一件镜像做不了的事:把 api 自动生成的共享密钥读进环境变量。
#
# 为什么 web 也要这个:`app/api/opencode/[...path]/route.ts` 里的 BFF 会带
# HUNTER_INTERNAL_KEY 回调 api 的 /api/internal/ocr/extract。api 首次启动时如果
# 自动生成了一个随机 HUNTER_INTERNAL_KEY(用户没在 .env 里填),而 web 这边还是
# 代码里的兜底值 `hunter-internal-local`,两边对不上 → OCR 一律 401。
# 症状是「上传图片没反应」,而 401 和「api 没起来」在前端长得一模一样,极难查。
#
# 优先级与 api / opencode 一致:**环境变量非空优先**(云平台由模板注入同一个值),
# 都为空时才回落到本地 compose 的 hunter_secrets 卷。
set -e

SECRETS=${HUNTER_SECRETS_FILE:-/opt/hunter-secrets/secrets.env}

if [ -f "$SECRETS" ]; then
    # 只认这两个键 —— 不 `.` 整个文件:那等于让一个文件里的任意内容在 web 容器里
    # 当 shell 跑,而且会把无关变量一起带进 node 进程。
    for k in HUNTER_INTERNAL_KEY JWT_SECRET; do
        eval "cur=\${$k:-}"
        [ -n "$cur" ] && continue
        v=$(grep -m1 "^$k=" "$SECRETS" | cut -d= -f2-) || v=""
        if [ -n "$v" ]; then
            export "$k=$v"
            echo "[web-boot] $k ← hunter_secrets 卷(长度 ${#v})" >&2
        fi
    done
fi

exec npm run start
