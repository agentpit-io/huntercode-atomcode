#!/usr/bin/env bash
# HunterCode · AtomCode 发行版（HCA）· 一键安装 / 升级 / 回滚
#
#   bash install.sh                      # 交互安装
#   bash install.sh --non-interactive \
#        --dir ~/hca --channel oneapi --api-key-file ./key --yes
#   bash install.sh --upgrade [--ref v0.1.1]
#   bash install.sh --rollback
#   bash install.sh --status
#   bash install.sh --help
#
# 这个脚本可以两种方式跑：
#   ① 在仓库里跑（deploy/install.sh）—— 直接用当前这份代码；
#   ② 单独下载一份跑（Release 附件）—— 自己 git clone 到安装目录。
#
# 设计原则（总控红线）：
#   · 所有数字都来自真实调用（key 校验真打接口、健康检查真查端点），拿不到打印 —；
#   · 密钥只写 0600 的文件，不回显、不进日志、不进 ps（用 --api-key-file 或交互输入）；
#   · 除 web 之外不开放任何端口；单用户免登录强制关闭；
#   · 可重复执行：装过一次再跑一次 = 校对并补齐，不会把数据搞坏。
set -euo pipefail

VERSION_OF_SCRIPT="0.1.0"
DEFAULT_REPO_GITCODE="https://gitcode.com/agentpit-io/huntercode-atomcode.git"
DEFAULT_REPO_GITHUB="https://github.com/agentpit-io/huntercode-atomcode.git"
DEFAULT_DIR="${HOME}/huntercode-atomcode"

# ── 通道默认值（已拍板决策 3：安装时三选一，默认 OneAPI · Gemini 3.8）──────────
ONEAPI_BASE="https://hunter.agentpit.io/api/saas/llm/v1"
ONEAPI_QUOTA="https://hunter.agentpit.io/api/saas/llm/quota"
ONEAPI_MODEL="hunter-chat"
OLLAMA_BASE_DEFAULT="http://host.docker.internal:11434/v1"
# 官方网关背后的**真实模型**（2026-09-22 实测：GET .../llm/v1/models 回读
# display_name，hunter-chat 自报 gemini-3.8-flash）。只有在网关地址就是
# ONEAPI_BASE 时才敢用这两个名字 —— 自建 OneAPI 的同名模型可能是别的东西。
ONEAPI_MODEL_LABEL="hunter-chat=Gemini 3.8 Flash,hunter-deep=Gemini 3.1 Pro"
ONEAPI_PROVIDER_LABEL="Google Gemini"

MODE=install
INTERACTIVE=1
ASSUME_YES=0
DIR=""
CHANNEL=""
BASE_URL=""
QUOTA_URL=""
MODEL_LABEL=""
PROVIDER_LABEL=""
MODEL=""
API_KEY=""
API_KEY_FILE=""
# 数据接口的两把 key（与模型通道分开计量，已拍板决策 12）
DATA_KEY=""
DATA_KEY_FILE=""
KRONOS_KEY=""
KRONOS_KEY_FILE=""
WEB_PORT=3200
API_PORT=8200
PUBLIC_HOST=""
ADMIN_EMAIL=""
REF=""
REPO=""
IMAGE_PREFIX=""
NPM_REGISTRY=""
PIP_INDEX_URL=""
HUNTER_REGISTRY=""
ALLOW_UNVERIFIED=0
DO_BUILD=1
PROJECT=""
IMAGE_TAG=""

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  cat <<'EOF'

参数
  --dir <路径>            安装目录（默认 ~/huntercode-atomcode）
  --non-interactive       一个问题都不问，全部从参数取（缺必填项直接失败）
  -y, --yes               交互模式下所有确认按「是」
  --channel <名>          模型通道：oneapi（默认）| official | ollama
  --base-url <url>        通道地址（oneapi 默认 HunterCode 网关；ollama 默认宿主 11434）
  --model <名>            模型名（oneapi 默认 hunter-chat；另两个必填）
  --model-label <名>      网页上给人看的模型名（例：Gemini 3.8 Flash）。
                          不给就按通道取默认值；请求用的仍是 --model 那个 ID
  --provider-label <名>   模型选择器的分组标题（例：Google Gemini）
  --api-key <key>         API key（会出现在 ps 里，**建议改用下一项**）
  --api-key-file <路径>   从文件读 key（推荐）
  --data-key-file <路径>  数据接口 key（truesource / 行情增强）。**与模型通道分开计量**
  --kronos-key-file <路径> Kronos key（K 线预测与回测看板）
                          两把都可以留空；留空时对应 MCP 被调用会明确说去哪申请，不假装成功。
                          也有 --data-key / --kronos-key 直接给值（会出现在 ps 里，不推荐）
  --quota-url <url>       配额接口（只有 oneapi 通道用；用于校验 key 与预算 hook）
  --web-port <口>         网页端口（默认 3200，**这是唯一对外的端口**）
  --api-port <口>         api 端口，只绑 127.0.0.1（默认 8200）
  --public-host <主机>    打印访问地址时用的主机名/IP（默认自动探测）
  --admin-email <邮箱>    首个管理员邮箱（默认 admin@hca.agentpit.io）
  --ref <git ref>         装/升到哪个版本（默认取最新 tag，没有 tag 就用默认分支）
                          想装开发中的快照就显式写 --ref main
  --repo <url>            源仓库（默认 GitCode，拉不动自动换 GitHub）
  --image-prefix <前缀>   基础镜像前缀，给国内镜像源用（例：docker.m.daocloud.io/）
  --npm-registry <url>    npm 源（例：https://registry.npmmirror.com）
  --pip-index-url <url>   pip/uv 源（例：https://pypi.tuna.tsinghua.edu.cn/simple）
  --hunter-registry <仓>  api 镜像所在仓库（默认 ghcr.io/agentpit-io）
  --allow-unverified-key  key 校验失败也继续（离网安装用，会显著警告）
  --no-build              不构建镜像（用已经在本机的）
  --project <名>          compose 项目名（默认 hca）。同一台机器上装第二套时必须换名，
                          否则两套会抢同一批容器与数据卷
  --image-tag <tag>       自建镜像的 tag（默认 dev）。同机两套栈要用不同 tag，
                          否则后构建的那套会把另一套的镜像覆盖掉
  --upgrade               升级（见文件头）
  --rollback              回滚到上一次升级前的状态
  --status                只看当前状态
  --help                  这份说明

环境变量（等价于同名参数，方便 CI）：HCA_INSTALL_DIR / HCA_CHANNEL / HCA_LLM_API_KEY /
HCA_LLM_API_KEY_FILE / HCA_LLM_BASE_URL / HCA_LLM_MODEL / HCA_LLM_MODEL_LABEL /
HCA_LLM_PROVIDER_LABEL / HCA_QUOTA_URL /
HUNTER_API_KEY / KRONOS_API_KEY（或 HCA_DATA_API_KEY_FILE / HCA_KRONOS_API_KEY_FILE）
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    --non-interactive) INTERACTIVE=0; shift ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    --channel) CHANNEL="$2"; shift 2 ;;
    --model-label) MODEL_LABEL="$2"; shift 2 ;;
    --provider-label) PROVIDER_LABEL="$2"; shift 2 ;;
    --base-url) BASE_URL="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --api-key) API_KEY="$2"; shift 2 ;;
    --api-key-file) API_KEY_FILE="$2"; shift 2 ;;
    --data-key) DATA_KEY="$2"; shift 2 ;;
    --data-key-file) DATA_KEY_FILE="$2"; shift 2 ;;
    --kronos-key) KRONOS_KEY="$2"; shift 2 ;;
    --kronos-key-file) KRONOS_KEY_FILE="$2"; shift 2 ;;
    --quota-url) QUOTA_URL="$2"; shift 2 ;;
    --web-port) WEB_PORT="$2"; shift 2 ;;
    --api-port) API_PORT="$2"; shift 2 ;;
    --public-host) PUBLIC_HOST="$2"; shift 2 ;;
    --admin-email) ADMIN_EMAIL="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --image-prefix) IMAGE_PREFIX="$2"; shift 2 ;;
    --npm-registry) NPM_REGISTRY="$2"; shift 2 ;;
    --pip-index-url) PIP_INDEX_URL="$2"; shift 2 ;;
    --hunter-registry) HUNTER_REGISTRY="$2"; shift 2 ;;
    --allow-unverified-key) ALLOW_UNVERIFIED=1; shift ;;
    --no-build) DO_BUILD=0; shift ;;
    --project) PROJECT="$2"; shift 2 ;;
    --image-tag) IMAGE_TAG="$2"; shift 2 ;;
    --upgrade|upgrade) MODE=upgrade; shift ;;
    --rollback|rollback) MODE=rollback; shift ;;
    --status|status) MODE=status; shift ;;
    install) shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1（--help 看说明）" >&2; exit 2 ;;
  esac
done

DIR="${DIR:-${HCA_INSTALL_DIR:-}}"
CHANNEL="${CHANNEL:-${HCA_CHANNEL:-}}"
API_KEY="${API_KEY:-${HCA_LLM_API_KEY:-}}"
API_KEY_FILE="${API_KEY_FILE:-${HCA_LLM_API_KEY_FILE_HOST:-}}"
DATA_KEY="${DATA_KEY:-${HUNTER_API_KEY:-}}"
DATA_KEY_FILE="${DATA_KEY_FILE:-${HCA_DATA_API_KEY_FILE:-}}"
KRONOS_KEY="${KRONOS_KEY:-${KRONOS_API_KEY:-}}"
KRONOS_KEY_FILE="${KRONOS_KEY_FILE:-${HCA_KRONOS_API_KEY_FILE:-}}"
BASE_URL="${BASE_URL:-${HCA_LLM_BASE_URL_UPSTREAM:-}}"
MODEL="${MODEL:-${HCA_LLM_MODEL:-}}"
QUOTA_URL="${QUOTA_URL:-${HCA_QUOTA_URL:-}}"
MODEL_LABEL="${MODEL_LABEL:-${HCA_LLM_MODEL_LABEL:-}}"
PROVIDER_LABEL="${PROVIDER_LABEL:-${HCA_LLM_PROVIDER_LABEL:-}}"
PROJECT="${PROJECT:-${HCA_COMPOSE_PROJECT:-hca}}"
IMAGE_TAG="${IMAGE_TAG:-${HCA_IMAGE_TAG:-}}"

# ── 输出 ────────────────────────────────────────────────────────────────────
BOLD=""; DIM=""; RED=""; GRN=""; YEL=""; RST=""
if [ -t 1 ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; RST=$'\033[0m'
fi
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s▶ %s%s\n' "$BOLD" "$*" "$RST"; }
ok()   { printf '  %s✓%s %s\n' "$GRN" "$RST" "$*"; }
warn() { printf '  %s⚠%s %s\n' "$YEL" "$RST" "$*"; }
die()  { printf '\n%s✗ %s%s\n' "$RED" "$*" "$RST" >&2; exit 1; }
ask() {
  # ask <提示> <默认值> → 回显到 stdout
  local prompt="$1" def="${2:-}" ans=""
  if [ "$INTERACTIVE" = 0 ]; then printf '%s' "$def"; return 0; fi
  if [ -n "$def" ]; then printf '  %s [%s]: ' "$prompt" "$def" >&2
  else printf '  %s: ' "$prompt" >&2; fi
  IFS= read -r ans || true
  printf '%s' "${ans:-$def}"
}
confirm() {
  [ "$ASSUME_YES" = 1 ] && return 0
  [ "$INTERACTIVE" = 0 ] && return 0
  local ans; printf '  %s [y/N]: ' "$1" >&2; IFS= read -r ans || true
  case "${ans:-}" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}
now_sh() { TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S'; }

# ── 1. 环境检测 ─────────────────────────────────────────────────────────────
OS="$(uname -s)"
preflight() {
  step "检测运行环境"
  say "  时间：$(now_sh)（上海）· 脚本版本 ${VERSION_OF_SCRIPT}"
  case "$OS" in
    Linux) ok "系统：Linux $(uname -r)" ;;
    Darwin)
      ok "系统：macOS $(sw_vers -productVersion 2>/dev/null || uname -r)"
      warn "macOS 只做过脚本审阅，**没有在真机上跑过**（见 docs/开发文档/M4-成果与测试报告.md）。"
      warn "已知差异：Ollama 通道要用 http://host.docker.internal:11434/v1；"
      warn "Docker Desktop 默认内存可能不足 4G，web 镜像构建会 OOM。"
      ;;
    *) warn "系统：$OS（未验证）" ;;
  esac

  command -v docker >/dev/null 2>&1 || die "没有 docker。装好 Docker 再来：https://docs.docker.com/engine/install/"
  local dv; dv="$(docker --version 2>/dev/null | head -1 || true)"
  docker info >/dev/null 2>&1 || die "docker 装了但连不上守护进程（${dv:-未知版本}）。确认 docker 已启动、当前用户在 docker 组里（sudo usermod -aG docker \$USER 后重新登录）。"
  ok "docker：${dv}"

  docker compose version >/dev/null 2>&1 \
    || die "没有 compose v2（docker compose）。旧的 docker-compose(v1) 不支持本项目的 compose 文件，请升级 Docker。"
  ok "compose：$(docker compose version --short 2>/dev/null || docker compose version | head -1)"

  for c in curl python3; do
    command -v "$c" >/dev/null 2>&1 || die "缺 ${c}（安装与自检都要用）。"
  done
  if [ "$DO_BUILD" = 1 ]; then
    command -v git >/dev/null 2>&1 || warn "没有 git —— 只能用本地已有代码，--upgrade / --rollback 不可用。"
  fi
  ok "curl / python3 就位"

  # 内存与磁盘：不是硬拦，但要如实说
  local mem_g="—" disk_g="—"
  if [ "$OS" = Linux ]; then
    mem_g="$(free -g 2>/dev/null | awk '/^Mem:/{print $2}')"
  elif [ "$OS" = Darwin ]; then
    mem_g="$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1024 / 1024 / 1024 ))"
  fi
  disk_g="$(df -Pg . 2>/dev/null | awk 'NR==2{print $4}' || df -P . | awk 'NR==2{printf "%d", $4/1024/1024}')"
  say "  内存 ${mem_g}G · 当前分区可用 ${disk_g}G"
  if [ "${mem_g:-0}" != "—" ] && [ "${mem_g:-0}" -lt 4 ] 2>/dev/null; then
    warn "内存不足 4G：web 镜像构建（next build）很可能 OOM。可以先在别的机器上构建镜像，再 --no-build 部署。"
  fi
  if [ "${disk_g:-0}" != "—" ] && [ "${disk_g:-0}" -lt 20 ] 2>/dev/null; then
    warn "可用磁盘不足 20G：镜像 + 依赖大约要 8～12G，postgres 数据另算。"
  fi
}

# ── 2. 安装目录与代码 ───────────────────────────────────────────────────────
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_REPO=""
[ -f "${HERE}/docker-compose.yml" ] && [ -f "${HERE}/../pins.lock" ] && SRC_REPO="$(cd "${HERE}/.." && pwd)"

resolve_dir() {
  step "选择安装目录"
  if [ -z "$DIR" ]; then
    DIR="$(ask "安装到哪个目录" "$DEFAULT_DIR")"
  fi
  # 展开 ~
  case "$DIR" in "~"|"~/"*) DIR="${HOME}/${DIR#\~/}" ;; esac
  mkdir -p "$DIR" || die "建不了目录 ${DIR}"
  DIR="$(cd "$DIR" && pwd)"
  ok "安装目录：${DIR}"
  if [ -f "${DIR}/pins.lock" ]; then
    ok "目录里已经有一份代码 —— 这次是**在原地校对并补齐**，不会动 deploy/.env 里已有的值。"
  fi
}

pick_repo() { [ -n "$REPO" ] && { printf '%s' "$REPO"; return; }; printf '%s' "$DEFAULT_REPO_GITCODE"; }

fetch_code() {
  step "准备代码"
  if [ -f "${DIR}/pins.lock" ] && [ -f "${DIR}/deploy/docker-compose.yml" ]; then
    ok "用 ${DIR} 里现有的代码"
  elif [ -n "$SRC_REPO" ] && [ "$SRC_REPO" != "$DIR" ]; then
    say "  从脚本所在的仓库复制一份到安装目录（排除 .git / node_modules / .next / target）"
    tar -C "$SRC_REPO" \
        --exclude=.git --exclude=node_modules --exclude=.next --exclude=target \
        --exclude='deploy/.env' --exclude='deploy/secrets' -cf - . | tar -C "$DIR" -xf -
    ok "代码已就位（来源：${SRC_REPO}）"
  elif [ -n "$SRC_REPO" ] && [ "$SRC_REPO" = "$DIR" ]; then
    ok "就在仓库里安装（${DIR}）"
  else
    command -v git >/dev/null 2>&1 || die "目录里没有代码，而本机没有 git —— 没法拉源码。"
    [ -z "$(ls -A "$DIR" 2>/dev/null)" ] \
      || die "${DIR} 非空但又不是一份 HCA 代码。换个空目录，或者先把里面的东西挪走。"
    local repo ref; repo="$(pick_repo)"; ref="${REF:-}"
    say "  git clone ${repo}"
    # **克隆并保留 .git** —— --upgrade / --rollback 全靠它。
    # 美国机房访问 GitCode 的 HTTPS 会返回 418，所以失败自动换 GitHub。
    #
    # 先克隆到一个临时目录再整份搬进来（含 .git），**不 rmdir 安装目录** ——
    # 用户完全可能在安装目录里跑这个脚本，把自己的 cwd 删掉之后
    # git 会直接 `fatal: Unable to read current working directory`。
    local tmp; tmp="$(mktemp -d "${TMPDIR:-/tmp}/hca-clone.XXXXXX")"
    if ! git clone --quiet "$repo" "${tmp}/repo" 2>/dev/null; then
      warn "${repo} 拉不动，换 GitHub：${DEFAULT_REPO_GITHUB}"
      rm -rf "${tmp}/repo"
      git clone --quiet "$DEFAULT_REPO_GITHUB" "${tmp}/repo" \
        || { rm -rf "$tmp"; die "两个仓库都拉不动，检查网络。"; }
    fi
    # 没给 --ref 就取**最新的 tag**，与 --upgrade 的默认一致，也与用法里写的一致。
    # 之前这里不给 ref 就停在默认分支（main）—— 而 README 教的装法是
    # `curl .../v0.1.1/deploy/install.sh && bash install.sh`：
    # 下载的是 v0.1.1 的安装脚本，装出来的却是 main。发布之间 main 是超前的，
    # 「装了个发布版」与「装了个开发中的快照」是两件事。
    if [ -z "$ref" ]; then
      ref="$( cd "${tmp}/repo" && git tag --sort=-v:refname | head -1 )"
      if [ -n "$ref" ]; then
        say "  没给 --ref，取最新的 tag：${ref}"
      else
        say "  没给 --ref，仓库里也没有 tag，用默认分支"
      fi
    fi
    if [ -n "$ref" ]; then
      ( cd "${tmp}/repo" && git -c advice.detachedHead=false checkout --quiet "$ref" ) \
        || { rm -rf "$tmp"; die "没有这个版本：${ref}"; }
    fi
    ( cd "${tmp}/repo" && tar cf - . ) | ( cd "$DIR" && tar xf - )
    rm -rf "$tmp"
    ok "代码已就位（来源：$( cd "$DIR" && git remote get-url origin ) @ ${ref:-默认分支} · $( cd "$DIR" && git rev-parse --short HEAD )）"
  fi
  [ -f "${DIR}/deploy/up.sh" ] || die "${DIR}/deploy/up.sh 不在 —— 代码不完整。"
}

# ── 3. 模型通道三选一 + 真实校验 key ────────────────────────────────────────
KEY_CHECK_NOTE=""
read_key() {
  # 优先文件，其次参数，最后交互（交互输入不回显）
  if [ -n "$API_KEY_FILE" ]; then
    [ -s "$API_KEY_FILE" ] || die "--api-key-file ${API_KEY_FILE} 不存在或为空"
    API_KEY="$(tr -d ' \t\r\n' < "$API_KEY_FILE")"
    KEY_CHECK_NOTE="来自文件 ${API_KEY_FILE}"
    return
  fi
  if [ -n "$API_KEY" ]; then KEY_CHECK_NOTE="来自参数/环境变量"; return; fi
  if [ "$INTERACTIVE" = 0 ]; then
    die "--non-interactive 下必须给 --api-key-file 或 --api-key（Ollama 通道可以给空文件）"
  fi
  printf '  API key（输入不回显，直接回车＝暂不填）: ' >&2
  IFS= read -rs API_KEY || true
  printf '\n' >&2
  KEY_CHECK_NOTE="交互输入"
}

# 数据接口的两把 key（与模型通道分开计量，已拍板决策 12）。
# 都可以留空 —— 留空时对应 MCP 会在被调用时明确说去哪申请，而不是假装成功。
read_data_keys() {
  if [ -n "$DATA_KEY_FILE" ]; then
    [ -s "$DATA_KEY_FILE" ] || die "--data-key-file ${DATA_KEY_FILE} 不存在或为空"
    DATA_KEY="$(tr -d ' \t\r\n' < "$DATA_KEY_FILE")"
  fi
  if [ -n "$KRONOS_KEY_FILE" ]; then
    [ -s "$KRONOS_KEY_FILE" ] || die "--kronos-key-file ${KRONOS_KEY_FILE} 不存在或为空"
    KRONOS_KEY="$(tr -d ' \t\r\n' < "$KRONOS_KEY_FILE")"
  fi
  [ "$INTERACTIVE" = 0 ] && return 0
  if [ -z "$DATA_KEY" ]; then
    printf '  数据接口 key（truesource / 行情增强；不回显，回车＝跳过）: ' >&2
    IFS= read -rs DATA_KEY || true; printf '\n' >&2
  fi
  if [ -z "$KRONOS_KEY" ]; then
    printf '  Kronos key（K 线预测与回测看板；不回显，回车＝跳过）: ' >&2
    IFS= read -rs KRONOS_KEY || true; printf '\n' >&2
  fi
  [ -z "$DATA_KEY" ]   && warn "数据接口 key 没填 —— truesource 类工具会返回「未配置」说明，不影响其余 8 个 MCP。"
  [ -z "$KRONOS_KEY" ] && warn "Kronos key 没填 —— 回测看板与 K 线预测出不来数，其余功能不受影响。"
  return 0
}

# 真实打接口校验。回显的数字全部来自响应体。
verify_key() {
  local channel="$1" base="$2" key="$3" model="$4" quota="$5"
  local tmp rc http
  tmp="$(mktemp)"; trap 'rm -f "$tmp"' RETURN

  if [ "$channel" = oneapi ] && [ -n "$quota" ]; then
    http="$(curl -sS -o "$tmp" -w '%{http_code}' -m 20 \
             -H "Authorization: Bearer ${key}" "$quota" || echo 000)"
    if [ "$http" = 200 ]; then
      python3 - "$tmp" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
def g(*ks):
    for k in ks:
        if isinstance(d.get(k),(int,float)): return d[k]
    return None
used=g("used_today","used","used_tokens","consumed")
lim=g("limit_daily","limit")
rem=g("remaining")
parts=[]
if used is not None: parts.append(f"今日已用 {used}")
if lim  is not None: parts.append(f"日上限 {lim}")
if rem  is not None: parts.append(f"剩余 {rem}")
print("配额接口 200 · " + ("、".join(parts) if parts else "返回里没有配额字段（key 有效）"))
PY
      return 0
    fi
    if [ "$http" = 401 ] || [ "$http" = 403 ]; then
      say "  配额接口 HTTP ${http}：$(head -c 200 "$tmp")"
      return 1
    fi
    warn "配额接口 HTTP ${http}（不是 401/403），改用 ${base}/models 再试一次"
  fi

  # OpenAI 兼容的 /models：三个通道都支持
  http="$(curl -sS -o "$tmp" -w '%{http_code}' -m 20 \
           -H "Authorization: Bearer ${key}" "${base%/}/models" || echo 000)"
  if [ "$http" != 200 ]; then
    say "  ${base%/}/models → HTTP ${http}：$(head -c 200 "$tmp")"
    return 1
  fi
  MODEL_LIST_OK=1
  python3 - "$tmp" "$model" <<'PY'
import json,sys
try:
    d=json.load(open(sys.argv[1]))
except Exception as e:
    print(f"/models 返回的不是 JSON（{e}）—— key 可能有效但接口不标准"); sys.exit(0)
items=d.get("data") if isinstance(d,dict) else d
names=[str((i or {}).get("id") or (i or {}).get("name") or "") for i in (items or []) if isinstance(i,dict)]
names=[n for n in names if n]
want=sys.argv[2]
print(f"/models 200 · {len(names)} 个模型" + (f"：{', '.join(names[:8])}" + ("…" if len(names)>8 else "") if names else ""))
if want:
    print(("模型 " + want + " 在列表里") if want in names
          else f"⚠ 模型 {want} 不在 /models 返回的列表里（有些网关不把可用模型全列出来，不一定是错）")
PY
  return 0
}

choose_channel() {
  step "模型通道（三选一）"
  if [ -z "$CHANNEL" ]; then
    cat <<'EOF'
  1) OneAPI · Gemini 3.8（默认，推荐）
     OpenAI 兼容网关。预置 HunterCode 的地址，也可以填自建 OneAPI。
     模型 hunter-chat；key 形如 hunt_tools_****（在 https://hunter.agentpit.io/dev/api-keys 申请）
  2) 自带官方 Key
     任何 OpenAI 兼容端点（OpenAI / DeepSeek / 通义 / 自建）。要自己填 base_url 与模型名。
  3) 本地 Ollama
     完全离线。要先在宿主上 ollama serve，并 ollama pull 一个带工具调用能力的模型。
EOF
    local sel; sel="$(ask "选哪个" "1")"
    case "$sel" in
      1|oneapi|"") CHANNEL=oneapi ;;
      2|official)  CHANNEL=official ;;
      3|ollama)    CHANNEL=ollama ;;
      *) die "选项只有 1/2/3" ;;
    esac
  fi
  case "$CHANNEL" in
    oneapi)
      BASE_URL="${BASE_URL:-$(ask "网关地址" "$ONEAPI_BASE")}"
      MODEL="${MODEL:-$(ask "模型名" "$ONEAPI_MODEL")}"
      if [ -z "$QUOTA_URL" ]; then
        # 同一套网关：.../llm/v1 → .../llm/quota
        case "$BASE_URL" in
          *"/v1") QUOTA_URL="${BASE_URL%/v1}/quota" ;;
          *) QUOTA_URL="" ;;
        esac
      fi
      ;;
    official)
      BASE_URL="${BASE_URL:-$(ask "OpenAI 兼容的 base_url（要带 /v1）" "")}"
      [ -n "$BASE_URL" ] || die "official 通道必须给 --base-url"
      MODEL="${MODEL:-$(ask "模型名" "")}"
      [ -n "$MODEL" ] || die "official 通道必须给 --model"
      QUOTA_URL=""
      ;;
    ollama)
      BASE_URL="${BASE_URL:-$(ask "Ollama 的 OpenAI 兼容地址" "$OLLAMA_BASE_DEFAULT")}"
      MODEL="${MODEL:-$(ask "模型名（例：qwen3:8b）" "")}"
      [ -n "$MODEL" ] || die "ollama 通道必须给 --model"
      QUOTA_URL=""
      ;;
    *) die "--channel 只能是 oneapi / official / ollama" ;;
  esac
  # 网页模型选择器上**给人看的名字**（请求仍然用 MODEL 那个 ID）。
  # 规矩：只有确凿知道背后是什么，才写具体名字；否则留空 = 界面显示原始 ID。
  case "$CHANNEL" in
    oneapi)
      # 官方网关才套实测出来的 Gemini 名字；自建 OneAPI 的 hunter-chat 可能是别的模型
      if [ "$BASE_URL" = "$ONEAPI_BASE" ]; then
        MODEL_LABEL="${MODEL_LABEL:-$ONEAPI_MODEL_LABEL}"
        PROVIDER_LABEL="${PROVIDER_LABEL:-$ONEAPI_PROVIDER_LABEL}"
      fi
      ;;
    official|ollama)
      # 用户自己填的模型名就是真实模型名，直接拿它当显示名；
      # 分组标题用通道的中文名，比 provider id 好懂
      MODEL_LABEL="${MODEL_LABEL:-$MODEL}"
      if [ "$CHANNEL" = ollama ]; then
        PROVIDER_LABEL="${PROVIDER_LABEL:-本地 Ollama}"
      else
        PROVIDER_LABEL="${PROVIDER_LABEL:-自带官方 Key}"
      fi
      ;;
  esac
  ok "通道 ${CHANNEL} · base_url ${BASE_URL} · 模型 ${MODEL}"
  [ -n "$MODEL_LABEL" ] && say "  界面显示名：${MODEL_LABEL}（分组 ${PROVIDER_LABEL}）"
  [ -n "$QUOTA_URL" ] && say "  配额接口：${QUOTA_URL}"

  read_key
  if [ -z "$API_KEY" ]; then
    if [ "$CHANNEL" = ollama ]; then
      API_KEY="ollama"   # Ollama 不校验 key，但 OpenAI 客户端要求非空
      ok "Ollama 通道：用占位 key（Ollama 本身不鉴权）"
    else
      warn "没有 key —— 栈能起来，但一句话也问不了。之后把 key 写进 ${DIR}/deploy/secrets/llm-key 再 bash deploy/up.sh 即可。"
      return 0
    fi
  fi

  step "校验 key（真实调用接口，不是本地格式检查）"
  say "  key ${KEY_CHECK_NOTE}：${API_KEY:0:11}****（${#API_KEY} 字符，全文不回显）"
  local out
  if out="$(verify_key "$CHANNEL" "$BASE_URL" "$API_KEY" "$MODEL" "$QUOTA_URL" 2>&1)"; then
    printf '%s\n' "$out" | sed 's/^/  /'
    ok "key 可用"
  else
    printf '%s\n' "$out" | sed 's/^/  /'
    if [ "$ALLOW_UNVERIFIED" = 1 ]; then
      warn "key 没校验过，但你给了 --allow-unverified-key —— 继续安装。"
      warn "如果 key 其实是坏的，表现会是：网页能打开、发消息后没有回答。"
    else
      die "key 校验没过（上面是接口的原始返回）。确认 key 与地址；离网安装可以加 --allow-unverified-key。"
    fi
  fi
}

# ── 4. 写 .env ──────────────────────────────────────────────────────────────
set_env_kv() {
  # set_env_kv <文件> <键> <值>  —— 有则替换，无则追加（值里有 / 也安全：用 python 改）
  python3 - "$1" "$2" "$3" <<'PY'
import sys,io
path,key,val=sys.argv[1],sys.argv[2],sys.argv[3]
try:
    lines=io.open(path,encoding="utf-8").read().splitlines()
except FileNotFoundError:
    lines=[]
out=[];done=False
for ln in lines:
    if ln.startswith(key+"="):
        if not done: out.append(f"{key}={val}"); done=True
    else: out.append(ln)
if not done: out.append(f"{key}={val}")
io.open(path,"w",encoding="utf-8").write("\n".join(out)+"\n")
PY
}

write_env() {
  step "生成 deploy/.env 与密钥目录"
  local envf="${DIR}/deploy/.env"
  local secrets="${DIR}/deploy/secrets"
  local adminsec="${DIR}/secrets-admin"
  mkdir -p "$secrets" "$adminsec"; chmod 700 "$secrets" "$adminsec"
  if [ ! -f "$envf" ]; then
    cp "${DIR}/deploy/env.example" "$envf"
    ok "从 env.example 生成 deploy/.env"
  else
    ok "deploy/.env 已存在 —— 只改这次明确给出的项"
  fi
  chmod 600 "$envf"

  # 模型通道
  set_env_kv "$envf" HCA_LLM_UPSTREAM_URL "$BASE_URL"
  if [ "$CHANNEL" = oneapi ]; then
    # 经 llm-shim（兜底修并行 tool_calls 缺 index，见 env.example 的说明）
    set_env_kv "$envf" HCA_LLM_BASE_URL "http://llm-shim:3999/v1"
  else
    # 官方 key 与 Ollama 直连：shim 只对 hunter 网关那个协议缺陷有用
    set_env_kv "$envf" HCA_LLM_BASE_URL "$BASE_URL"
  fi
  set_env_kv "$envf" HCA_LLM_MODEL "$MODEL"
  set_env_kv "$envf" HCA_LLM_PROVIDER_NAME "$CHANNEL"
  set_env_kv "$envf" HCA_LLM_MODEL_LABEL "$MODEL_LABEL"
  set_env_kv "$envf" HCA_LLM_PROVIDER_LABEL "$PROVIDER_LABEL"
  set_env_kv "$envf" HCA_LLM_API_KEY_FILE "/run/secrets/llm-key"
  set_env_kv "$envf" HCA_LLM_API_KEY ""
  [ -n "$QUOTA_URL" ] && set_env_kv "$envf" HCA_QUOTA_URL "$QUOTA_URL"

  # 数据接口的两把 key。与模型通道**分开计量**（已拍板决策 12），所以分开配置。
  # 它们是 MCP 进程从环境变量读的（compose 的 daemon/api 段直接透传），
  # 不走 /run/secrets 那条路 —— 所以落在 0600 的 .env 里，不在 ps 里出现。
  # 不填就是空：MCP 照样连得上、列得出工具，调用时明确告诉用户去哪申请。
  [ -n "$DATA_KEY" ]   && set_env_kv "$envf" HUNTER_API_KEY "$DATA_KEY"
  [ -n "$KRONOS_KEY" ] && set_env_kv "$envf" KRONOS_API_KEY "$KRONOS_KEY"

  # key 只落文件（0600），不进 .env、不进 ps
  if [ -n "$API_KEY" ]; then
    printf '%s' "$API_KEY" > "${secrets}/llm-key"
    chmod 600 "${secrets}/llm-key"
    ok "key 写进 ${secrets}/llm-key（0600，只读挂进容器 /run/secrets/llm-key）"
  fi

  # 端口与暴露（总控端口表：只有 web 对公网）
  set_env_kv "$envf" HCA_WEB_HOST_PORT "$WEB_PORT"
  set_env_kv "$envf" HCA_API_HOST_PORT "$API_PORT"
  set_env_kv "$envf" HUNTER_SINGLE_USER "0"
  set_env_kv "$envf" REGISTRATION_MODE "invite"
  set_env_kv "$envf" HCA_SECRETS_DIR "./secrets"
  set_env_kv "$envf" HCA_COMPOSE_PROJECT "$PROJECT"
  [ -n "$IMAGE_TAG" ] && set_env_kv "$envf" HCA_IMAGE_TAG "$IMAGE_TAG"
  set_env_kv "$envf" HCA_ADMIN_SECRETS_DIR "$adminsec"
  [ -n "$ADMIN_EMAIL" ] && set_env_kv "$envf" HCA_ADMIN_EMAIL "$ADMIN_EMAIL"
  [ -z "$PUBLIC_HOST" ] && PUBLIC_HOST="$(detect_host)"
  set_env_kv "$envf" HCA_PUBLIC_HOST "$PUBLIC_HOST"

  # 国内网络：镜像前缀、npm/pip 源、api 镜像仓库
  [ -n "$IMAGE_PREFIX" ]    && set_env_kv "$envf" HCA_BASE_IMAGE_PREFIX "$IMAGE_PREFIX"
  [ -n "$NPM_REGISTRY" ]    && set_env_kv "$envf" HCA_NPM_REGISTRY "$NPM_REGISTRY"
  [ -n "$PIP_INDEX_URL" ]   && set_env_kv "$envf" HCA_PIP_INDEX_URL "$PIP_INDEX_URL"
  [ -n "$HUNTER_REGISTRY" ] && set_env_kv "$envf" HUNTER_REGISTRY "$HUNTER_REGISTRY"

  ok "deploy/.env 写好（随机口令由 up.sh 生成并写回，值不回显）"
}

detect_host() {
  # 只用于打印访问地址。拿不到就写 127.0.0.1，绝不编一个
  local h=""
  h="$(curl -fsS -m 3 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
  [ -z "$h" ] && h="$(curl -fsS -m 3 -H 'Metadata-Flavor: Google' \
        http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip 2>/dev/null || true)"
  [ -z "$h" ] && h="$(hostname -I 2>/dev/null | awk '{print $1}')"
  printf '%s' "${h:-127.0.0.1}"
}

# ── 5. 拉起 ─────────────────────────────────────────────────────────────────
bring_up() {
  step "构建并拉起（第一次要下镜像、装依赖、构建 web，慢的是这一步）"
  local t0 t1
  t0="$(date +%s)"
  local args=()
  [ "$DO_BUILD" = 0 ] && args+=(--no-build)
  ( cd "$DIR" && HCA_COMPOSE_PROJECT="$PROJECT" bash deploy/up.sh "${args[@]}" ) || die "拉起失败（上面有 up.sh 打的原因与容器日志）"
  t1="$(date +%s)"
  UP_SECONDS=$(( t1 - t0 ))
  ok "栈已就绪，本步耗时 ${UP_SECONDS} 秒"
}

# ── 6. 装完自检 + 打印 ──────────────────────────────────────────────────────
dump_config_toml() {
  # 真正生效的 config.toml 是 daemon 容器启动时按 .env 渲染的（deploy/daemon/hca-init.py）。
  # 这里把**容器里那一份**抄出来（key 打码），让人能核对，而不是在宿主上再渲染一遍。
  local cid out="${DIR}/config.toml.rendered"
  cid="$( cd "$DIR" && docker compose -p "$PROJECT" -f deploy/docker-compose.yml --env-file deploy/.env ps -q daemon 2>/dev/null )"
  [ -n "$cid" ] || { warn "daemon 容器不在，拿不到 config.toml"; return 0; }
  if docker exec "$cid" sh -c 'cat "${ATOMCODE_HOME:-/data/atomcode}/config.toml"' 2>/dev/null \
      | sed -E 's/^(api_key[[:space:]]*=[[:space:]]*").{0,11}[^"]*(")/\1****已打码\2/' > "$out"; then
    chmod 600 "$out"
    ok "生效中的 config.toml 已抄到 ${out}（api_key 打码）"
    # 只打有效行：daemon 启动后会把上游那份**几百行的注释模板**写回 config.toml，
    # 整份打出来会把安装日志冲掉。完整内容在上面那个文件里。
    grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$out" | sed 's/^/     /'
  else
    warn "读不到容器里的 config.toml"
  fi
}

selfcheck_extra() {
  step "自检（数字都来自真实调用）"
  local cid port
  cid="$( cd "$DIR" && docker compose -p "$PROJECT" -f deploy/docker-compose.yml --env-file deploy/.env ps -q daemon 2>/dev/null )"
  [ -n "$cid" ] || { warn "daemon 容器不在"; return 1; }
  port="$(grep -E '^HCA_DAEMON_PORT=' "${DIR}/deploy/.env" | cut -d= -f2)"; port="${port:-13456}"
  local rc=0
  # **断言而不是打印**：`.hooks.json` 解析失败是**静默**的（questions A8）——
  # 写错一个逗号，hook 全部不生效，`hooks list` 照样返回 0 而没有任何报错。
  # 只把数字打出来等于没查：M4 就是这么写的，一直没发现它从不失败。
  printf '  hook 注册      : '
  local hn
  hn="$(docker exec -w /workspace "$cid" atomcode hooks list 2>/dev/null \
        | awk '/^ *Total/{print $2}' | head -1)"
  if [ "${hn:-0}" -ge "${HCA_EXPECT_HOOKS:-8}" ] 2>/dev/null; then
    echo "${hn} 条"
  else
    echo "—（拿到 ${hn:-空}，期望 ≥ ${HCA_EXPECT_HOOKS:-8}；.hooks.json 可能解析失败了）"
    rc=1
  fi
  # 大小闸（P0-11）不在镜像里的话，MCP 返回会退回「被内核砍成半截 JSON」，
  # 而服务照样 healthy —— 同样是「不查就看不见」的一类。
  printf '  MCP 大小闸     : '
  if docker exec "$cid" test -f /opt/hca/mcp/hca_size_guard.py; then
    echo "在（/opt/hca/mcp/hca_size_guard.py）"
  else
    echo "—（缺失：MCP 返回的大小闸未生效）"; rc=1
  fi
  printf '  MCP            : '
  docker exec "$cid" sh -c \
    "curl -fsS -m 15 -H \"Authorization: Bearer \$(cat /run/hca/daemon-token)\" http://127.0.0.1:${port}/mcp/status" \
    | python3 -c "
import json,sys
d=json.load(sys.stdin); s=d.get('servers',[])
ok=[x for x in s if x.get('status')=='connected']
print(f\"{len(ok)}/{len(s)} connected，工具 {sum(x.get('tool_count') or 0 for x in s)} 个\")
" 2>/dev/null || echo "—（取不到）"
  printf '  技能           : '
  docker exec "$cid" sh -c \
    "curl -fsS -m 15 -H \"Authorization: Bearer \$(cat /run/hca/daemon-token)\" http://127.0.0.1:${port}/skills" \
    | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d),'个')" 2>/dev/null || echo "—（取不到）"
  printf '  网页           : '
  curl -fsS -o /dev/null -w 'HTTP %{http_code}（%{time_total}s）\n' -m 20 \
    "http://127.0.0.1:${WEB_PORT}/" || echo "—（取不到）"
  printf '  网页→daemon    : '
  curl -fsS -m 20 "http://127.0.0.1:${WEB_PORT}/api/opencode/config" \
    | python3 -c "import json,sys;d=json.load(sys.stdin);m=d.get('model');print('model='+str(m) if m else '—')" \
    2>/dev/null || echo "—（取不到）"
  printf '  对外端口       : '
  if command -v ss >/dev/null 2>&1; then
    ss -lntp 2>/dev/null | awk -v p=":${WEB_PORT}" '$4 ~ p {print $4}' | paste -sd' ' -
  else echo "—（没有 ss）"; fi
  return "$rc"
}

print_done() {
  local adminf="${DIR}/secrets-admin/admin.txt"
  cat <<EOF

${BOLD}安装完成${RST}  $(now_sh)（上海时间）
  访问地址    http://${PUBLIC_HOST}:${WEB_PORT}
  管理员账号  ${adminf}（0600，口令在文件里，**这里不打印**）
              没建成的话：cd ${DIR} && bash deploy/up.sh --admin
  模型通道    ${CHANNEL} · ${MODEL} · ${BASE_URL}
  界面显示名  ${MODEL_LABEL:-（未设，界面显示模型 ID）} · 分组 ${PROVIDER_LABEL:-（未设）}
  安装目录    ${DIR}（compose 项目名 ${PROJECT}）
              deploy/.env           所有配置（0600）
              deploy/secrets/       模型 key（只读挂进容器）
              config.toml.rendered  生效中的 daemon 配置（key 打码）

常用命令（都在 ${DIR} 下跑）
  bash deploy/up.sh --status     看状态与自检
  bash deploy/up.sh              重新拉起（可重复执行）
  bash deploy/up.sh --down       停掉（**不删数据卷**）
  bash deploy/install.sh --upgrade      升级到新版本
  bash deploy/install.sh --rollback     回滚到升级前

安全提醒
  · 只有 ${WEB_PORT} 对外；api / daemon / postgres / redis 都不发布到宿主。
  · 单用户免登录已强制关闭（HUNTER_SINGLE_USER=0），注册按邀请码。
  · 行情与持仓数据全部留在本机；模型只经你选的通道出网。
EOF
}

write_state() {
  local commit="—"
  if [ -d "${DIR}/.git" ]; then commit="$( cd "$DIR" && git rev-parse HEAD 2>/dev/null || echo — )"
  elif [ -f "${DIR}/.hca-source-commit" ]; then commit="$(cat "${DIR}/.hca-source-commit")"; fi
  python3 - "${DIR}/.hca-install.json" "$commit" "$CHANNEL" "$MODEL" "$BASE_URL" \
           "$WEB_PORT" "$API_PORT" "${REF:-}" "$(now_sh)" "${UP_SECONDS:-0}" <<'PY'
import json,sys
p,commit,ch,model,base,web,api,ref,ts,secs=sys.argv[1:11]
try: st=json.load(open(p))
except Exception: st={}
st.update({"commit":commit,"channel":ch,"model":model,"base_url":base,
           "web_port":int(web),"api_port":int(api),"ref":ref or None,
           "installed_at":ts,"last_up_seconds":int(secs),
           "script_version":"0.1.0"})
json.dump(st,open(p,"w"),ensure_ascii=False,indent=2)
PY
  chmod 600 "${DIR}/.hca-install.json"
}

load_state() {
  [ -f "${DIR}/.hca-install.json" ] || die "${DIR} 里没有 .hca-install.json —— 这个目录不是 install.sh 装出来的，或者装到一半失败了。"
  CHANNEL="$(python3 -c "import json;print(json.load(open('${DIR}/.hca-install.json')).get('channel') or '')")"
  MODEL="$(python3 -c "import json;print(json.load(open('${DIR}/.hca-install.json')).get('model') or '')")"
  BASE_URL="$(python3 -c "import json;print(json.load(open('${DIR}/.hca-install.json')).get('base_url') or '')")"
  WEB_PORT="$(python3 -c "import json;print(json.load(open('${DIR}/.hca-install.json')).get('web_port') or 3200)")"
  API_PORT="$(python3 -c "import json;print(json.load(open('${DIR}/.hca-install.json')).get('api_port') or 8200)")"
  PROJECT="$(sed -n 's/^HCA_COMPOSE_PROJECT=//p' "${DIR}/deploy/.env" 2>/dev/null | tail -1)"
  PROJECT="${PROJECT:-hca}"
  PUBLIC_HOST="$(grep -E '^HCA_PUBLIC_HOST=' "${DIR}/deploy/.env" 2>/dev/null | cut -d= -f2)"
  PUBLIC_HOST="${PUBLIC_HOST:-127.0.0.1}"
}

# ── 升级 / 回滚 ─────────────────────────────────────────────────────────────
#
# 升级 = 换代码 + 重建镜像 + 重新拉起；**数据卷一律不动**
# （workspace / atomcode-home / postgres / redis 都保留）。
# 升级前把「当前 commit + 当前 .env」存成回滚点，回滚就是把它们放回去再拉起。
#
# ⚠️ 数据库迁移是**单向**的：回滚只回代码与镜像，库结构留在新版（本项目的迁移都是
#    加表加列，旧代码照样跑）。如果某次升级带了破坏性迁移，会在发布说明里写明。
save_rollback_point() {
  local rp="${DIR}/.hca-rollback.json" commit="—"
  [ -d "${DIR}/.git" ] && commit="$( cd "$DIR" && git rev-parse HEAD )"
  cp "${DIR}/deploy/.env" "${DIR}/.hca-rollback.env" && chmod 600 "${DIR}/.hca-rollback.env"
  python3 - "$rp" "$commit" "$(now_sh)" <<'PY'
import json,sys
json.dump({"commit":sys.argv[2],"saved_at":sys.argv[3]},
          open(sys.argv[1],"w"),ensure_ascii=False,indent=2)
PY
  chmod 600 "$rp"
  ok "回滚点已存：commit ${commit}（.hca-rollback.json + .hca-rollback.env）"
}

do_upgrade() {
  resolve_dir
  load_state
  [ -d "${DIR}/.git" ] || die "升级需要 git 仓库（${DIR}/.git 不在）。当初是用「复制代码」方式装的话，请在安装目录里 git init 并 remote add origin，或者重新用 --ref 装一份。"
  step "升级"
  local cur tgt
  cur="$( cd "$DIR" && git rev-parse --short HEAD )"
  say "  当前：${cur}"
  ( cd "$DIR" && git fetch --all --tags --quiet ) || warn "git fetch 失败（离网？）—— 只能切到本地已有的版本"
  if [ -z "$REF" ]; then
    REF="$( cd "$DIR" && git tag --sort=-v:refname | head -1 )"
    [ -n "$REF" ] || REF="origin/main"
    say "  没给 --ref，取最新的 tag：${REF}"
  fi
  # ⚠️ 给的是**分支名**时必须解到 `origin/<分支>`，不能解本地同名分支 ——
  # 安装目录是 clone 出来的，本地 `feat/x` 停在当初 clone 的那个提交上，
  # `git rev-parse feat/x` 拿到的是旧的，脚本会一本正经地说「已经是最新，无需升级」
  # （M4 第一次跑升级验证就是这么"成功"的）。tag 与 commit 不受影响。
  local resolve="$REF"
  if ( cd "$DIR" && git rev-parse --verify --quiet "refs/remotes/origin/${REF}" >/dev/null ); then
    resolve="origin/${REF}"
    say "  ${REF} 是分支 → 按远端 ${resolve} 解析"
  fi
  tgt="$( cd "$DIR" && git rev-parse --short "$resolve" 2>/dev/null )" || die "解析不了版本 ${REF}"
  if [ "$cur" = "$tgt" ]; then
    ok "已经是 ${REF}（${tgt}），无需升级"
    return 0
  fi
  say "  目标：${REF}（${tgt}）"
  confirm "确认升级 ${cur} → ${tgt}？数据卷不动，升级失败可以 --rollback" || die "已取消"

  save_rollback_point
  ( cd "$DIR" && git -c advice.detachedHead=false checkout --quiet "$tgt" ) || die "切版本失败（工作区有本地改动？git status 看一下）"
  ok "代码已切到 ${REF}"
  bring_up
  if selfcheck_extra; then
    dump_config_toml
    write_state
    ok "升级完成：${cur} → ${tgt}"
    print_done
  else
    warn "自检没全过。回滚命令： cd ${DIR} && bash deploy/install.sh --rollback"
    exit 1
  fi
}

do_rollback() {
  resolve_dir
  load_state
  [ -f "${DIR}/.hca-rollback.json" ] || die "没有回滚点（.hca-rollback.json）—— 只有 --upgrade 过的安装才有。"
  local back
  back="$(python3 -c "import json;print(json.load(open('${DIR}/.hca-rollback.json'))['commit'])")"
  local saved
  saved="$(python3 -c "import json;print(json.load(open('${DIR}/.hca-rollback.json'))['saved_at'])")"
  step "回滚"
  say "  回到 commit ${back}（回滚点存于 ${saved}）"
  say "  ⚠ 只回代码与镜像：数据卷与库结构留在当前状态（本项目的迁移都是加表加列，旧代码能跑）"
  confirm "确认回滚？" || die "已取消"
  ( cd "$DIR" && git -c advice.detachedHead=false checkout --quiet "$back" ) || die "切回 ${back} 失败"
  cp "${DIR}/.hca-rollback.env" "${DIR}/deploy/.env" && chmod 600 "${DIR}/deploy/.env"
  ok "代码与 .env 已回到升级前"
  bring_up
  selfcheck_extra || warn "自检没全过 —— 回滚后的版本也有问题，看上面的项"
  dump_config_toml
  write_state
  ok "回滚完成（当前 $( cd "$DIR" && git rev-parse --short HEAD )）"
}

do_status() {
  resolve_dir
  [ -f "${DIR}/.hca-install.json" ] && { step "安装信息"; sed 's/^/  /' "${DIR}/.hca-install.json"; }
  load_state
  ( cd "$DIR" && docker compose -p "$PROJECT" -f deploy/docker-compose.yml --env-file deploy/.env ps ) || true
  selfcheck_extra || true
}

# ── 主流程 ──────────────────────────────────────────────────────────────────
case "$MODE" in
  upgrade)  preflight; do_upgrade ;;
  rollback) preflight; do_rollback ;;
  status)   do_status ;;
  install)
    cat <<EOF
${BOLD}HunterCode · AtomCode 发行版 —— 一键安装${RST}
  一个装在你自己机器上的 A 股投研助手：对话式投研 + 9 个数据源 MCP + 6 个投研技能，
  行情、持仓、研究结论全部留在本机。底座是 AtomCode（MIT），界面是 HunterCode（Apache-2.0）。
EOF
    preflight
    resolve_dir
    fetch_code
    choose_channel
    read_data_keys
    write_env
    bring_up
    if ! selfcheck_extra; then
      die "自检没全过（上面标 — 的那几项）。栈可能起来了但不可用，别当成装好了。"
    fi
    dump_config_toml
    write_state
    print_done
    ;;
esac
