#!/bin/sh
# opencode 容器入口 · 只做一件镜像做不了的事:按 .env 现生成 provider 配置。
#
# opencode 只从配置文件读 provider,不认 LLM_* 环境变量 —— 不生成这个文件,它会回落到
# 内置的 OpenCode Zen,你在 .env 里填的网关根本不会被调用,现象是「能聊天但答得驴唇不对马嘴」。
# 改完 .env 重新 `docker compose up -d opencode` 即生效。
#
# 以前这里还补三件事,现在都在源头修好了,故删除:
#   · pip 补装 mcp<2 —— 镜像自 1.18.12-slim.1 起用 pip --target 装的就是 1.x
#   · sed 改 uzi_mcp.py 的 httpx 超时 —— 改成读 UZI_HTTP_TIMEOUT(见 scripts/opencode-mcp/uzi_mcp.py)
#   · sed 改 .opencode/opencode.jsonc 的 MCP timeout —— 镜像源头已是 180000
set -e

# ── 会话数据目录可写自检(R0 预研结论第四节)────────────────────────
# 会话正文(opencode-local.db)与审计日志都写在 /home/hunter/.local 下。
# 这个目录不可写时,opencode 的插件会在 mkdir 处崩掉 —— 容器进入重启循环,
# 日志里只有一句 `EACCES: permission denied, mkdir ...`,看不出是卷属主的问题。
# 与其等它崩,不如在这里拦下并说清原因。
#
# Docker 具名卷不会出这个问题:空卷首次挂载会把镜像里该路径的内容与属主
# (1001:1001)拷进卷。K8s / 云平台的 PVC 不拷贝,卷根属主是平台给的(实测
# 1000:1003 或 0:0),容器以 1001 跑就写不进去。
if [ ! -w /home/hunter/.local ]; then
    owner=$(stat -c '%u:%g' /home/hunter/.local 2>/dev/null || echo '未知')
    echo "[boot] ❌ 会话数据目录 /home/hunter/.local 不可写(当前属主 ${owner},容器以 uid 1001 运行)。" >&2
    echo "[boot]    Docker 具名卷不会出这个问题;K8s / 云平台的 PVC 需要把卷属主设成 1001" >&2
    echo "[boot]    (securityContext.fsGroup: 1001)或加一个 initContainer 执行 chown。" >&2
    exit 1
fi

# ── 密钥(设计方案 3.2 · M1 子任务 C)────────────────────────────
# 必须 `.`(source)进来,不能直接执行 —— 直接执行的话 export 只作用于子进程,
# opencode 本体拿不到 JWT_SECRET,hunter-auth 插件验不了签,表现是对话莫名 401。
# 环境变量非空时优先(云平台走模板注入),否则读 hunter_secrets 卷里的 secrets.env
# (本地 compose 由 api 首启时生成)。读不到只告警不退出。
. /opt/hunter-boot/load-secrets.sh

python3 /opt/hunter-boot/gen-config.py

# 新镜像(1.18.12-slim.1 起)是单文件二进制,旧镜像只有 bun + 源码。两种都要能起来:
# 本脚本现在是 COPY 进包装镜像的(M1 · 见 deploy/opencode.Dockerfile),但基础镜像的
# 版本由 OPENCODE_TAG 决定,用户把它钉回旧标签是常态 —— 那时没有 opencode 二进制,
# 由镜像里的 bun 垫片兜住。(开发时仍可用 docker-compose.dev.yml 把本目录挂回去。)
# 监听地址:默认 0.0.0.0。Railway 老环境的私有网络是 IPv6-only,那里要设 HUNTER_BIND_HOST=::
BIND_HOST="${HUNTER_BIND_HOST:-0.0.0.0}"

if command -v opencode >/dev/null 2>&1; then
    exec opencode serve --hostname "$BIND_HOST" --port 3901
fi

echo "[boot] 镜像里没有 opencode 二进制(旧镜像),回落到源码启动" >&2
exec bun run packages/opencode/src/index.ts serve --hostname "$BIND_HOST" --port 3901
