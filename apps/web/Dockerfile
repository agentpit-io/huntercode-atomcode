# Hunter Community · Web · Next.js 15 + Node 22
# Multi-stage: deps → build → runtime
#
# 国内网络（M4 · 计划 v0.2 TP-08「镜像与 npm 源可配置」）：
#   BASE_IMAGE_PREFIX  基础镜像前缀，例 docker.m.daocloud.io/ 或 mirror.gcr.io/library/
#                      （**要带结尾的斜杠**；留空 = 直连 Docker Hub）
#   NPM_REGISTRY       npm 源，例 https://registry.npmmirror.com（留空 = 官方源）
# 两个都由 deploy/docker-compose.yml 的 build.args 从 .env 传进来，
# install.sh 的 --image-prefix / --npm-registry 就是写那两个变量。
ARG BASE_IMAGE_PREFIX=""
FROM ${BASE_IMAGE_PREFIX}node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
ARG NPM_REGISTRY=""
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi; \
    npm ci --no-audit --no-fund

FROM ${BASE_IMAGE_PREFIX}node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* 是构建期烘进客户端产物的，必须在 build 之前作为 ARG 传进来；
# 只在 compose 的 environment 里传是无效的（那是运行时，浏览器拿不到）。
# 默认留空 = 同源相对路径，保持反代部署（演示站）的现有行为不变。
ARG NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_TELEMETRY_DISABLED=1
# next build 的堆上限。默认由 V8 按机器内存算，在 2 核 8G 且同机还有别的重活
# （HCA 测试机上另一条链路在跑 cargo）时容易被挤爆 —— 而 OOM 在 docker build
# 的输出里长得像编译错误，很容易误判成代码问题。显式压住。
ARG NODE_OPTIONS="--max-old-space-size=3072"
ENV NODE_OPTIONS=$NODE_OPTIONS
RUN npm run build

FROM ${BASE_IMAGE_PREFIX}node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN apk add --no-cache curl && \
    addgroup -g 1001 -S nodejs && \
    adduser -S nextjs -u 1001

COPY --from=builder --chown=nextjs:nodejs /app/.next ./.next
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/node_modules ./node_modules
COPY --from=builder --chown=nextjs:nodejs /app/package.json ./package.json
# 入口脚本 · 从 hunter_secrets 卷读共享密钥(见脚本头注释)
COPY --chown=nextjs:nodejs entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:3000 || exit 1

CMD ["/app/entrypoint.sh"]
