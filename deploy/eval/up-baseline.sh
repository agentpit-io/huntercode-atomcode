#!/usr/bin/env bash
# A/B 评测基线（hunter-community 1.2.0 · opencode 版）一键拉起 —— **可重复执行**。
#
#     bash deploy/eval/up-baseline.sh            # 建网络 + 生成密钥 + 起栈 + 建账号 + 播种数据
#     bash deploy/eval/up-baseline.sh --status   # 只看状态
#     bash deploy/eval/up-baseline.sh --down     # 停并删容器（数据卷保留）
#
# 与总控规则的对应：
#   · 项目名 hca-baseline，只碰 hca / hca-* 前缀的资源，绝不 prune、绝不停 docker。
#   · 所有宿主端口只绑 127.0.0.1（评测脚本在宿主上跑，需要能连；不对公网）。
#   · 密钥全部随机生成、600 存进 $HCA_SECRETS_DIR，**不打印、不进仓库**。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
PROJECT=hca-baseline
COMPOSE_FILE="${HERE}/docker-compose.baseline.yml"
ENV_FILE="${HERE}/.env"
NET=hca-eval-net
SECRETS_DIR="${HCA_SECRETS_DIR:-$HOME/hca/secrets}"
HEALTH_TIMEOUT="${HCA_HEALTH_TIMEOUT:-420}"
API="http://127.0.0.1:18300"
OPENCODE="http://127.0.0.1:13931"
OPENCODE_CONTAINER="${PROJECT}-opencode-1"
OPENCODE_WORKSPACE="/opt/opencode-workspace"

MODE=up
for a in "$@"; do
  case "$a" in
    --down)   MODE=down ;;
    --status) MODE=status ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "未知参数：$a" >&2; exit 2 ;;
  esac
done

# ⚠️ log 一律写 **stderr**。第一版写的是 stdout，而 ensure_secret 是用
# `$(ensure_secret …)` 取返回值的 —— 新生成密钥那一次，日志行会连同密钥一起被
# 捕获进变量，写进 .env 就变成「HUNTER_INTERNAL_KEY=[baseline] 生成密钥 …」
# 外加一行裸 hex。实测症状：api 与 daemon 的 internal key 对不上，
# /api/internal/* 一律 401 internal auth failed，而两边看起来都「配好了」。
log() { printf '[baseline] %s\n' "$*" >&2; }
die() { printf '[baseline] ✗ %s\n' "$*" >&2; exit 1; }
dc()  { docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

wait_for_docker() {
  local waited=0
  while ! systemctl is-active --quiet docker 2>/dev/null; do
    docker info >/dev/null 2>&1 && return 0
    [ "$waited" -ge 60 ] && die "等了 60 分钟 docker 还没起来"
    log "docker 不是 active，等 60 秒（已等 ${waited} 分钟）"; sleep 60; waited=$((waited+1))
  done
  docker info >/dev/null 2>&1 || die "docker active 但 docker info 失败"
}

# ── 密钥：没有就随机生成一把，存进 secrets 目录（600），永不打印 ──────────────
ensure_secret() {
  local name="$1" f="${SECRETS_DIR}/$1"
  if [ ! -s "$f" ]; then
    mkdir -p "$SECRETS_DIR"; chmod 700 "$SECRETS_DIR"
    openssl rand -hex 32 > "$f"; chmod 600 "$f"
    log "生成密钥 ${name}（内容不打印）"
  fi
  cat "$f"
}

prepare_env() {
  [ -s "${SECRETS_DIR}/llm-test-key" ] || die "缺 ${SECRETS_DIR}/llm-test-key（模型通道 key）"
  local pg jwt setup internal llmkey
  pg="$(ensure_secret baseline-pg-password)"
  jwt="$(ensure_secret baseline-jwt-secret)"
  setup="$(ensure_secret baseline-setup-token)"
  # HUNTER_INTERNAL_KEY 两边共用：基线的 api / opencode 与 HCA 的 daemon 都读它
  internal="$(ensure_secret hunter-internal-key)"
  llmkey="$(cat "${SECRETS_DIR}/llm-test-key")"

  umask 077
  cat > "$ENV_FILE" <<EOF
# 由 deploy/eval/up-baseline.sh 生成（600）。**不要提交**。
BASELINE_PG_PASSWORD=${pg}
BASELINE_JWT_SECRET=${jwt}
BASELINE_SETUP_TOKEN=${setup}
HUNTER_INTERNAL_KEY=${internal}
HCA_LLM_API_KEY=${llmkey}
HCA_LLM_UPSTREAM_URL=${HCA_LLM_UPSTREAM_URL:-https://hunter.agentpit.io/api/saas/llm/v1}
HCA_LLM_MODEL=${HCA_LLM_MODEL:-hunter-chat}
# truesource 等 SaaS 功能与模型通道共用同一把 key（实测 /api/saas/truesource/* 认它）
HUNTER_API_KEY=${llmkey}
BASELINE_SCHEMA_SANITIZE=${BASELINE_SCHEMA_SANITIZE:-auto}
EOF
  chmod 600 "$ENV_FILE"
  log "已写 ${ENV_FILE}（600，内容不打印）"
}

ensure_net() {
  docker network inspect "$NET" >/dev/null 2>&1 || {
    log "建外部网络 ${NET}"
    docker network create --label hca.component=eval-net "$NET" >/dev/null
  }
}

wait_healthy() {
  local svc="$1" waited=0 cid state
  while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
    cid="$(dc ps -q "$svc" 2>/dev/null || true)"
    if [ -n "$cid" ]; then
      state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo unknown)"
      case "$state" in
        healthy|running) log "${svc}: ${state}（第 ${waited} 秒）"; return 0 ;;
        exited|dead) log "✗ ${svc} 已退出，最后 40 行："; dc logs --tail 40 "$svc"; return 1 ;;
      esac
    fi
    sleep 3; waited=$((waited+3))
  done
  log "✗ ${svc} ${HEALTH_TIMEOUT} 秒未健康，最后 60 行："; dc logs --tail 60 "$svc"; return 1
}

case "$MODE" in
  down)
    wait_for_docker; [ -f "$ENV_FILE" ] || prepare_env
    dc down --remove-orphans; exit 0 ;;
  status)
    wait_for_docker; [ -f "$ENV_FILE" ] || prepare_env
    dc ps
    echo -n "  api  /health : "; curl -fsS -m 5 "${API}/api/health" 2>/dev/null || echo "—"
    echo
    echo -n "  opencode     : "
    curl -fsS -m 5 -u "opencode:" "${OPENCODE}/session" -o /dev/null -w '%{http_code}\n' 2>/dev/null || echo "—"
    exit 0 ;;
esac

wait_for_docker
ensure_net
prepare_env

log "启动（镜像全部是 GHCR 预构建，不构建）"
dc up -d --remove-orphans

wait_healthy postgres || die "postgres 没起来"
wait_healthy redis    || die "redis 没起来"
wait_healthy api      || die "api 没起来"
wait_healthy llm-shim || die "llm-shim 没起来"
wait_healthy opencode || die "opencode 没起来"

log "── 建评测账号并播种数据（真实调用 api，失败即退出）──"
python3 "${HERE}/seed_eval_account.py" \
  --api "$API" \
  --secrets "$SECRETS_DIR" \
  || die "播种失败"

log "── 把论点/持仓文件铺进基线工作区（与 HCA 侧逐字节相同）──"
# 为什么这一步必须有：论点是 PUT /api/watchlist/{code}/thesis 播进 api 的，
# 但**两边的 MCP 里没有任何一个工具会把论点读回来**（watchlist / portfolio /
# uzi / hunter_cap / hunter_user / screener 逐个 grep 过，零命中）。
# 论点只能从工作区文件读到。HCA 侧由 tools/eval/seed_workspace.py 铺了
# theses/*.md，基线侧如果不铺，第 2 类题（持仓论点复核）对基线就是
# 结构性不可能完成的 —— 那比的是评测设置，不是 agent。
# 详见 docs/eval/setup-defect/README.md（2026-09-22 正式批次跑到一半时发现）。
docker exec "$OPENCODE_CONTAINER" sh -c \
  "mkdir -p ${OPENCODE_WORKSPACE}/holdings ${OPENCODE_WORKSPACE}/theses" \
  || die "基线工作区建目录失败"
python3 "${HERE}/../../tools/eval/seed_workspace.py" \
  --account "${SECRETS_DIR}/eval-account.json" \
  --container "$OPENCODE_CONTAINER" \
  --workspace "$OPENCODE_WORKSPACE" \
  || die "基线工作区铺账本失败"

log "── 自检 ──"
echo -n "  api /health           : "; curl -fsS -m 5 "${API}/api/health" || echo "—"; echo
echo -n "  opencode GET /session : "
curl -fsS -m 10 -u "opencode:" "${OPENCODE}/session" -o /dev/null -w '%{http_code}\n' || echo "—"
log "完成。opencode 在 http://127.0.0.1:13931（只绑回环），api 在 ${API}"
