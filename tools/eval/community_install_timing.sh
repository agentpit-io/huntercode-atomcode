#!/usr/bin/env bash
# 社区版（opencode 版）的从零安装 / 升级 / 回滚耗时 —— 补 docs/对比-opencode版.md §6 的「未测」（I1）。
#
#     bash tools/eval/community_install_timing.sh <工作目录> [输出 json]
#
# 对照的是 M4 §4 量 HCA 那三个数时的做法：**同一台机器、同样只记墙钟、
# 同样把每一步的命令与返回码落盘**。两边的安装方式本来就不同，这一点必须写进报告，
# 不能只把两个秒数并排放：
#
#   · HCA：`deploy/install.sh` —— 会**本地构建** daemon 镜像（取 npm 上的 AtomCode
#     二进制 + 双重 sha256 校验）与 web 镜像（Next.js 构建），所以 407 秒里一大半是构建。
#   · 社区版：`git clone` + `docker compose up -d` —— 四个自家服务**全部用 GHCR 上的
#     预构建镜像**，仓库里的默认 compose 一个 `build:` 都没有（它的注释里写明这是刻意的）。
#     所以它的安装时间 = 克隆 + 拉镜像 + 起容器等健康。
#
# 「拉镜像」这一步在本机**镜像已存在**时是 0 秒。脚本会如实分别记：
#   `pull_s`（本次实测）、`images_present_before`（跑之前本机有没有这些镜像）、
#   以及每个镜像在 registry 上的**压缩层字节数之和**（`docker manifest inspect` 实测），
#   这样报告里可以说清楚「这 0 秒是因为镜像已在本机」。
#
# 安全：
#   · 用临时目录 + 临时 compose 项目名（`hcaic-<时间戳>`），与现有任何栈无关。
#   · 端口全部不发布（`ports: []` 覆盖），不碰防火墙、不占公网端口。
#   · 收尾 `down -v` **只针对这个临时项目**，不 prune、不删任何共享镜像。
set -uo pipefail
WORK="${1:?工作目录}"
OUT="${2:-${WORK}/timing.json}"
REPO_URL="${HCA_COMMUNITY_REPO:-https://github.com/agentpit-io/hunter-community.git}"
NEW_VER="${HCA_COMMUNITY_VER:-1.2.0}"
OLD_VER="${HCA_COMMUNITY_OLD_VER:-1.1.0}"
PROJ="hcaic-$(date +%s)"
mkdir -p "$WORK"
LOG="${WORK}/steps.log"
: > "$LOG"

py() { python3 -c "$@"; }
now() { date +%s.%N; }
say() { echo "[ic] $*" | tee -a "$LOG"; }

# 记一条：名字、命令、返回码、墙钟秒
declare -A T
run_step() {
  local name="$1"; shift
  local t0 t1
  say "── ${name} ── $*"
  t0=$(now)
  "$@" >> "$LOG" 2>&1
  local rc=$?
  t1=$(now)
  T["${name}_s"]=$(py "print(round(${t1}-${t0},1))")
  T["${name}_rc"]=$rc
  say "   rc=${rc} 用时 ${T["${name}_s"]}s"
  return 0   # 不中断：某一步失败也要把它如实记下来
}

IMAGES=(
  "ghcr.io/agentpit-io/hunter-community-web"
  "ghcr.io/agentpit-io/hunter-community-api"
  "ghcr.io/agentpit-io/hunter-community-opencode"
  "ghcr.io/agentpit-io/hunter-community-llm-shim"
)

present_before=""
for i in "${IMAGES[@]}"; do
  if docker image inspect "${i}:${NEW_VER}" >/dev/null 2>&1; then
    present_before="${present_before}${i}:${NEW_VER} "
  fi
done
say "跑之前本机已有的镜像：${present_before:-（一个都没有）}"

SRC="${WORK}/src"
rm -rf "$SRC"
# 钉到 tag，装的就是 1.2.0 那一版，而不是默认分支的 HEAD
run_step clone git clone --depth 1 --branch "v${NEW_VER}" "$REPO_URL" "$SRC"

cd "$SRC" || { say "✗ 克隆目录不存在，停"; exit 1; }

# 端口一律不发布：这是别人的机器，不许占公网端口，也不许和已有的栈撞端口。
#
# ⚠️ **必须用 `!reset`**。compose 的列表字段在多文件合并时是**追加**不是替换 ——
# 写 `ports: []` 等于什么都没做，容器照样去绑 5442 / 8100，撞上机器上已有的栈，
# `up` 直接 rc=1（I1 第一次跑就是这么废掉的，原始日志留在 steps.log）。
cat > docker-compose.hcaic.yml <<'YML'
services:
  web:      {ports: !reset []}
  api:      {ports: !reset []}
  opencode: {ports: !reset []}
  postgres: {ports: !reset []}
  redis:    {ports: !reset []}
YML
DC=(docker compose -p "$PROJ" -f docker-compose.yml -f docker-compose.hcaic.yml)

run_step pull "${DC[@]}" pull
run_step up   "${DC[@]}" up -d --wait
if [ "${T[up_rc]:-1}" != "0" ]; then
  say "✗ 从零安装这一步就没成功（rc=${T[up_rc]:-?}）。后面的升级/回滚耗时会变成"
  say "  「在一个坏栈上反复重试」的耗时，没有意义 —— 直接拆干净、把失败如实记下来。"
  "${DC[@]}" down -v >> "$LOG" 2>&1
  printf '{"machine":"%s","ts_shanghai":"%s","project":"%s","aborted":"up 失败，未继续量升级/回滚","clone_s":%s,"pull_s":%s,"up_s":%s,"up_rc":%s,"steps_log":"%s"}\n' \
    "$(hostname)" "$(TZ=Asia/Shanghai date '+%F %T')" "$PROJ" \
    "${T[clone_s]:-null}" "${T[pull_s]:-null}" "${T[up_s]:-null}" "${T[up_rc]:-null}" "$LOG" > "$OUT"
  cat "$OUT"
  exit 1
fi

healthy=$(docker ps --filter "label=com.docker.compose.project=${PROJ}" \
          --format '{{.Names}} {{.Status}}' | grep -c healthy || true)
total=$(docker ps -a --filter "label=com.docker.compose.project=${PROJ}" --format '{{.Names}}' | wc -l)
say "起来的容器：${healthy}/${total} healthy"

# ── 升级：1.1.0 → 1.2.0（先降到旧版，再升回来，这样「升级」量的是真的换版本）──
run_step downgrade env HUNTER_VERSION="$OLD_VER" "${DC[@]}" up -d --wait
run_step upgrade   env HUNTER_VERSION="$NEW_VER" "${DC[@]}" up -d --wait
run_step rollback  env HUNTER_VERSION="$OLD_VER" "${DC[@]}" up -d --wait

# ── registry 上的压缩层大小（实测，不是估的）──────────────────────────────
declare -A SIZE
for i in "${IMAGES[@]}"; do
  s=$(docker manifest inspect "${i}:${NEW_VER}" 2>/dev/null \
      | py "import json,sys
d=json.load(sys.stdin)
ls=d.get('layers') or []
if not ls and d.get('manifests'):
    print(''); raise SystemExit
print(sum(x.get('size',0) for x in ls))" 2>/dev/null)
  SIZE["$i"]="${s:-}"
done

# ── 收尾：只拆这个临时项目 ────────────────────────────────────────────────
run_step teardown "${DC[@]}" down -v

{
  echo "{"
  echo "  \"machine\": \"$(hostname)\","
  echo "  \"ts_shanghai\": \"$(TZ=Asia/Shanghai date '+%F %T')\","
  echo "  \"project\": \"${PROJ}\","
  echo "  \"repo\": \"${REPO_URL}\","
  echo "  \"version_new\": \"${NEW_VER}\", \"version_old\": \"${OLD_VER}\","
  echo "  \"images_present_before\": \"${present_before}\","
  echo "  \"containers_healthy\": ${healthy:-0}, \"containers_total\": ${total:-0},"
  for k in clone pull up downgrade upgrade rollback teardown; do
    echo "  \"${k}_s\": ${T["${k}_s"]:-null}, \"${k}_rc\": ${T["${k}_rc"]:-null},"
  done
  echo "  \"registry_compressed_bytes\": {"
  n=0
  for i in "${IMAGES[@]}"; do
    n=$((n+1))
    sep=","; [ "$n" -eq "${#IMAGES[@]}" ] && sep=""
    echo "    \"${i}\": ${SIZE["$i"]:-null}${sep}"
  done
  echo "  },"
  echo "  \"steps_log\": \"${LOG}\""
  echo "}"
} > "$OUT"
say "已写 $OUT"
cat "$OUT"
