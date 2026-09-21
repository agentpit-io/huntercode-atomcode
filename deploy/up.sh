#!/usr/bin/env bash
# HCA 一键拉起。**可重复执行** —— 再跑一遍就是「确保栈是健康的」，
# 不会因为容器已经在跑、或者上次跑到一半被人清掉而失败。
#
#     bash deploy/up.sh              # 构建（有缓存）+ 启动 + 等健康 + 自检
#     bash deploy/up.sh --no-build   # 不构建，只启动
#     bash deploy/up.sh --rebuild    # 强制不用缓存重建
#     bash deploy/up.sh --down       # 停掉并删容器（**不删数据卷**）
#     bash deploy/up.sh --status     # 只看状态
#
# 测试机注意（总控「测试机与 HunterLauncher 链路共用」）：
#   · 只碰 compose 项目 hca 与 hca-* 镜像/卷，绝不 prune、绝不 stop docker 服务。
#   · docker 不是 active 时**等**（另一条链路在测"无 Docker"场景，会自己恢复），
#     每 60 秒查一次，最多 60 分钟。
#   · 构建走 flock ~/.hca-heavy.lock 串行化，避免和对方的 cargo build 抢内存。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT=hca
COMPOSE_FILE="${HERE}/docker-compose.yml"
ENV_FILE="${HERE}/.env"
HEAVY_LOCK="${HOME}/.hca-heavy.lock"
DOCKER_WAIT_MINUTES="${HCA_DOCKER_WAIT_MINUTES:-60}"
HEALTH_TIMEOUT="${HCA_HEALTH_TIMEOUT:-300}"

MODE=up
BUILD=1
NOCACHE=0
for a in "$@"; do
  case "$a" in
    --no-build) BUILD=0 ;;
    --rebuild)  NOCACHE=1 ;;
    --down)     MODE=down ;;
    --status)   MODE=status ;;
    -h|--help)  sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数：$a" >&2; exit 2 ;;
  esac
done

log() { printf '[up.sh] %s\n' "$*"; }
die() { printf '[up.sh] ✗ %s\n' "$*" >&2; exit 1; }

dc() { docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

# ── docker 可用性 ───────────────────────────────────────────────────────────
wait_for_docker() {
  local waited=0
  while ! systemctl is-active --quiet docker 2>/dev/null; do
    if docker info >/dev/null 2>&1; then return 0; fi   # 没有 systemd 的环境
    if [ "$waited" -ge "$DOCKER_WAIT_MINUTES" ]; then
      die "等了 ${DOCKER_WAIT_MINUTES} 分钟 docker 还没起来。这台机器上另一条链路可能正在测「无 Docker」场景，稍后再试。"
    fi
    log "docker 服务不是 active，等 60 秒再看（已等 ${waited} 分钟 / 上限 ${DOCKER_WAIT_MINUTES}）"
    sleep 60
    waited=$((waited + 1))
  done
  docker info >/dev/null 2>&1 || die "docker 服务 active，但 docker info 失败（权限？）"
}

# ── 环境文件与密钥目录 ──────────────────────────────────────────────────────
prepare_env() {
  if [ ! -f "$ENV_FILE" ]; then
    log "没有 deploy/.env，从 env.example 复制一份（记得填 key）"
    cp "${HERE}/env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
  fi
  # shellcheck disable=SC1090
  set -a; . "$ENV_FILE"; set +a

  local secrets_dir="${HCA_SECRETS_DIR:-./secrets}"
  case "$secrets_dir" in
    /*) : ;;
    *)  secrets_dir="${HERE}/${secrets_dir#./}" ;;
  esac
  if [ ! -d "$secrets_dir" ]; then
    log "建密钥目录 ${secrets_dir}（700）"
    mkdir -p "$secrets_dir"
    chmod 700 "$secrets_dir"
  fi

  # 只检查「有没有」，绝不打印内容
  local key_file="${HCA_LLM_API_KEY_FILE:-}"
  if [ -n "${HCA_LLM_API_KEY:-}" ]; then
    log "模型 key：来自 HCA_LLM_API_KEY（环境变量）"
  elif [ -n "$key_file" ]; then
    local host_path="${secrets_dir}/$(basename "$key_file")"
    if [ -s "$host_path" ]; then
      log "模型 key：${host_path}（$(wc -c <"$host_path" | tr -d ' ') 字节，内容不打印）"
    else
      log "⚠ 模型 key 文件 ${host_path} 不存在或为空 —— daemon 起得来，但没有可用模型。"
      log "  放一把 key 进去再跑一次即可（不用重建镜像）。"
    fi
  else
    log "⚠ 既没有 HCA_LLM_API_KEY 也没有 HCA_LLM_API_KEY_FILE"
  fi
}

# ── 健康等待 ────────────────────────────────────────────────────────────────
wait_healthy() {
  local svc="$1" waited=0 cid state
  while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
    cid="$(dc ps -q "$svc" 2>/dev/null || true)"
    if [ -n "$cid" ]; then
      state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo unknown)"
      case "$state" in
        healthy|running) log "${svc}: ${state}（第 ${waited} 秒）"; return 0 ;;
        exited|dead)     log "✗ ${svc} 已退出，最后 40 行日志："; dc logs --tail 40 "$svc"; return 1 ;;
      esac
    fi
    sleep 3
    waited=$((waited + 3))
  done
  log "✗ ${svc} 在 ${HEALTH_TIMEOUT} 秒内没到 healthy，最后 60 行日志："
  dc logs --tail 60 "$svc"
  return 1
}

# ── 部署后自检（数字都来自真实调用，拿不到就写 —）─────────────────────────
selfcheck() {
  log "── 自检 ──"
  local cid
  cid="$(dc ps -q daemon)"
  [ -n "$cid" ] || { log "daemon 容器不在"; return 1; }

  local port="${HCA_DAEMON_PORT:-13456}"
  echo -n "  GET /health   : "
  docker exec "$cid" curl -fsS -m 5 "http://127.0.0.1:${port}/health" || echo "—（取不到）"
  echo
  echo -n "  GET /skills   : "
  docker exec "$cid" sh -c \
    "curl -fsS -m 10 -H \"Authorization: Bearer \$(cat /run/hca/daemon-token)\" http://127.0.0.1:${port}/skills" \
    | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d),'个：',' '.join(x['name'] for x in d))" \
    2>/dev/null || echo "—（取不到）"
  echo -n "  GET /mcp/status: "
  docker exec "$cid" sh -c \
    "curl -fsS -m 10 -H \"Authorization: Bearer \$(cat /run/hca/daemon-token)\" http://127.0.0.1:${port}/mcp/status" \
    | python3 -c "
import json,sys
d=json.load(sys.stdin)
s=d.get('servers',[])
ok=[x for x in s if x.get('status')=='connected']
print(f\"{len(ok)}/{len(s)} connected，工具 {sum(x.get('tool_count') or 0 for x in s)} 个，trusted={d.get('trusted')}，blocked={d.get('blocked')}\")
for x in s:
    if x.get('status')!='connected':
        print('     ⚠', x.get('name'), x.get('status'), x.get('error') or '')
" 2>/dev/null || echo "—（取不到）"
  echo "  token 卷       : $(docker exec "$cid" sh -c 'ls -l /run/hca | tail -n +2 | wc -l') 个文件（内容不打印）"
}

case "$MODE" in
  down)
    wait_for_docker
    prepare_env
    log "停止并删除 hca 的容器（数据卷保留：workspace / atomcode-home / daemon-token）"
    dc down --remove-orphans
    exit 0
    ;;
  status)
    wait_for_docker
    prepare_env
    dc ps
    selfcheck || true
    exit 0
    ;;
esac

wait_for_docker
prepare_env

if [ "$BUILD" = 1 ]; then
  avail=$(free -g | awk '/^Mem:/{print $7}')
  log "可用内存 ${avail}G（构建要 ≥3G）"
  if [ "${avail:-0}" -lt 3 ]; then
    log "⚠ 可用内存不足 3G，仍然继续（akshare 那一层是纯下载解包，不吃内存）"
  fi
  log "取重负载锁 ${HEAVY_LOCK}（和这台机器上另一条链路的 cargo build 串行化）"
  if [ "$NOCACHE" = 1 ]; then
    flock "$HEAVY_LOCK" docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build --no-cache
  else
    flock "$HEAVY_LOCK" docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build
  fi
fi

log "启动"
dc up -d --remove-orphans

wait_healthy llm-shim || die "llm-shim 没起来"
wait_healthy daemon   || die "daemon 没起来"

selfcheck
log "完成。daemon 在 compose 内网 http://daemon:${HCA_DAEMON_PORT:-13456}（不对宿主发布）"
