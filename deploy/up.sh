#!/usr/bin/env bash
# HCA 一键拉起。**可重复执行** —— 再跑一遍就是「确保栈是健康的」，
# 不会因为容器已经在跑、或者上次跑到一半被人清掉而失败。
#
#     bash deploy/up.sh              # 构建（有缓存）+ 启动 + 等健康 + 自检
#     bash deploy/up.sh --no-build   # 不构建，只启动
#     bash deploy/up.sh --rebuild    # 强制不用缓存重建
#     bash deploy/up.sh --down       # 停掉并删容器（**不删数据卷**）
#     bash deploy/up.sh --status     # 只看状态
#     bash deploy/up.sh --admin      # 只建/重置管理员账号（M3）
#
# 测试机注意（总控「测试机与 HunterLauncher 链路共用」）：
#   · 只碰 compose 项目 hca 与 hca-* 镜像/卷，绝不 prune、绝不 stop docker 服务。
#   · docker 不是 active 时**等**（另一条链路在测"无 Docker"场景，会自己恢复），
#     每 60 秒查一次，最多 60 分钟。
#   · 构建走 flock ~/.hca-heavy.lock 串行化，避免和对方的 cargo build 抢内存。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${HERE}/docker-compose.yml"
ENV_FILE="${HERE}/.env"
# compose 项目名。默认 hca；install.sh 装到别的目录时会给一个不同的名字，
# 这样同一台机器上可以并存两套栈（**卷也是按项目名隔离的**，互不覆盖）。
# .env 是在 prepare_env 里才 source 的，而这里就要用，所以直接从文件里取一行。
PROJECT="${HCA_COMPOSE_PROJECT:-}"
if [ -z "$PROJECT" ] && [ -f "$ENV_FILE" ]; then
  PROJECT="$(sed -n 's/^HCA_COMPOSE_PROJECT=//p' "$ENV_FILE" | tail -1)"
fi
PROJECT="${PROJECT:-hca}"
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
    --admin)    MODE=admin ;;
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
  # ── M3：web 对公网，这四项必须有值，缺了就地生成并写回 deploy/.env ──
  #
  # 为什么要写回文件而不是只 export：compose 的 `${X:?}` 读的是 --env-file，
  # 而且**重建容器时必须还是同一把** —— 每次现生成的话，JWT_SECRET 一变
  # 所有人的登录态失效、postgres 口令一变 api 直接连不上库。
  local changed=0
  for pair in \
      "HCA_PG_PASSWORD:postgres 口令" \
      "HCA_JWT_SECRET:JWT 签名密钥" \
      "HUNTER_SETUP_TOKEN:首启向导口令" \
      "HUNTER_INTERNAL_KEY:/api/internal 共享密钥"; do
    local var="${pair%%:*}" what="${pair#*:}"
    if ! grep -qE "^${var}=.+" "$ENV_FILE"; then
      # 只写文件，不回显 —— 总控红线 2
      local val
      val="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
      sed -i "/^${var}=/d" "$ENV_FILE"
      printf '%s=%s\n' "$var" "$val" >> "$ENV_FILE"
      log "生成 ${var}（${what}），已写进 deploy/.env（值不回显）"
      changed=1
    fi
  done
  [ "$changed" = 1 ] && chmod 600 "$ENV_FILE"

  # shellcheck disable=SC1090
  set -a; . "$ENV_FILE"; set +a

  # ── 公网暴露前置检查（总控「端口与暴露」）────────────────────────────
  # web 是本栈里唯一对公网的服务。单用户免登录开着 = 谁打开页面谁就是管理员。
  if [ "${HUNTER_SINGLE_USER:-0}" = "1" ]; then
    die "web 对公网（${HCA_WEB_HOST_PORT:-3200}），但 HUNTER_SINGLE_USER=1（免登录）。改成 0 再跑。"
  fi

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

# ── 管理员账号（总控：web 对公网必须关免登录，所以得有个真账号）────────────
create_admin() {
  local api_port="${HCA_API_HOST_PORT:-8200}"
  # admin.txt 默认写在容器挂载的那个密钥目录里；**但那个目录是只读挂进 daemon 的**，
  # 而 daemon 里跑着模型的 bash 工具。把管理员口令和模型能碰到的目录分开更稳妥，
  # 所以留一个单独的开关（测试机上指向 ~/hca/secrets，见部署记录）。
  local secrets_dir="${HCA_ADMIN_SECRETS_DIR:-${HCA_SECRETS_DIR:-./secrets}}"
  case "$secrets_dir" in
    /*) : ;;
    *)  secrets_dir="${HERE}/${secrets_dir#./}" ;;
  esac
  HCA_WEB_HOST_PORT="${HCA_WEB_HOST_PORT:-3200}" \
  HCA_PUBLIC_HOST="${HCA_PUBLIC_HOST:-127.0.0.1}" \
    python3 "${HERE}/create_admin.py" --api "http://127.0.0.1:${api_port}" --secrets "$secrets_dir"
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

  # ── M3：api 与 web ──
  local api_port="${HCA_API_HOST_PORT:-8200}" web_port="${HCA_WEB_HOST_PORT:-3200}"
  echo -n "  api /api/health: "
  curl -fsS -m 10 "http://127.0.0.1:${api_port}/api/health" || echo "—（取不到）"
  echo
  echo -n "  api 注册策略   : "
  curl -fsS -m 10 "http://127.0.0.1:${api_port}/api/auth/status" \
    | python3 -c "import json,sys;d=json.load(sys.stdin);print('single_user=',d.get('single_user'),' registration_mode=',d.get('registration_mode'),sep='')" \
    2>/dev/null || echo "—（取不到）"
  local rc=0
  echo -n "  web 首页       : "
  curl -fsS -o /dev/null -w 'HTTP %{http_code}（%{time_total}s）\n' -m 20 "http://127.0.0.1:${web_port}/" \
    || { echo "—（取不到）"; rc=1; }
  echo -n "  web→daemon     : "
  # 走 BFF 的公共资源端点：通了说明 web 读到了 daemon token 且内网可达。
  # **这一跳失败就是部署失败** —— M3 首次部署时它打的是 `model=—`，
  # 而 web 容器本身 healthy（健康检查只看首页），差点被当成正常（报告 §3.2）。
  if curl -fsS -m 20 "http://127.0.0.1:${web_port}/api/opencode/config" \
       | python3 -c "
import json,sys
d=json.load(sys.stdin)
m=d.get('model')
if not m: raise SystemExit(1)
print('model=',m,sep='')
"; then :; else echo "—（取不到：web 读不到 daemon token，或内网不通）"; rc=1; fi
  return "$rc"
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
  admin)
    wait_for_docker
    prepare_env
    create_admin
    exit $?
    ;;
esac

wait_for_docker
prepare_env

if [ "$BUILD" = 1 ]; then
  # web 那一层是 `next build`，**是这个栈里最吃内存的一步**（总控要求构建前查内存）。
  # 低于 3G 就等：这台机器上另一条链路的 cargo build 会把内存吃光，硬上会 OOM
  # 被杀，而 OOM 的报错在 docker build 里长得像编译错误，很容易误判成代码问题。
  waited=0
  while :; do
    avail=$(free -g | awk '/^Mem:/{print $7}')
    [ "${avail:-0}" -ge 3 ] && break
    if [ "$waited" -ge 30 ]; then
      die "等了 30 分钟可用内存仍不足 3G（当前 ${avail}G）。另一条链路可能在跑 cargo，稍后再试。"
    fi
    log "可用内存 ${avail}G < 3G，等 60 秒（已等 ${waited} 分钟 / 上限 30）"
    sleep 60
    waited=$((waited + 1))
  done
  log "可用内存 ${avail}G，开始构建"
  log "取重负载锁 ${HEAVY_LOCK}（和这台机器上另一条链路的 cargo build 串行化）"
  # next build 的堆上限由 compose 的 build arg 传进构建容器
  # （在宿主 export NODE_OPTIONS 是没用的，构建跑在容器里）——
  # 见 deploy/docker-compose.yml 的 web.build.args 与 apps/web/Dockerfile。
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
wait_healthy postgres || die "postgres 没起来"
wait_healthy redis    || die "redis 没起来"
wait_healthy api      || die "api 没起来"
wait_healthy web      || die "web 没起来"

# web 对公网 + 免登录已关 = 没有管理员账号这套栈就没法用。所以这一步失败就是部署失败，
# 不能只打个 ⚠ 就往下走（M3 首次部署正是这么漏过去的：邮箱 422，栈却报「完成」）。
admin_rc=0
create_admin || admin_rc=$?

sc_rc=0
selfcheck || sc_rc=$?
[ "$sc_rc" -eq 0 ] || die "自检没全过（上面标 — 的那几项）"
[ "$admin_rc" -eq 0 ] || die "管理员账号没建成（上面有原因）。栈在跑但没法登录；修完跑 bash deploy/up.sh --admin"
log "完成。web http://<本机 IP>:${HCA_WEB_HOST_PORT:-3200} · api 127.0.0.1:${HCA_API_HOST_PORT:-8200} · daemon 只在内网"
