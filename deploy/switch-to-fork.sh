#!/usr/bin/env bash
# 把一个已经装好的 HCA 部署切到 fork 变体（决策 14）。**幂等**：再跑一遍就是「确保是 fork」。
#
#     bash deploy/switch-to-fork.sh /path/to/atomcode        # 切过去
#     bash deploy/switch-to-fork.sh --off                    # 切回官方二进制
#
# 三步（与 docs/部署与运维.md §6.3 一致，这里只是把它们串起来免得漏）：
#   1. deploy/fork-image.sh 叠那一层镜像（自带 sha256 校验，值现算并回显）
#   2. 往 deploy/.env 写三行（已有就改，不重复追加）
#   3. up.sh --no-build 用新镜像重建容器，然后回显 config.toml 里那两行**证明它生效了**
#
# 为什么要有第 3 步的回显：这三个参数在官方二进制上**配了也不生效**（读不到），
# 所以「配置写进去了」和「它在起作用」是两件事，必须分别看到。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/.." && pwd)"
ENVF="${HERE}/.env"
PROJECT="${HCA_COMPOSE_PROJECT:-hca}"
PERSONA="${HCA_FORK_PERSONA:-/opt/hca/personas/hunter-research.md}"
DENY="${HCA_FORK_DENY:-group:codeintel,group:atomgit,group:subagent,code_review,ast_grep,list_sessions,schedule_wakeup,recall,web_fetch,web_search,glob,grep,search_replace,open_file}"

say(){ printf '[switch-to-fork] %s\n' "$*"; }
set_kv() {   # 幂等地写 .env：有这个键就替换，没有就追加
  local k="$1" v="$2"
  if grep -q "^${k}=" "$ENVF" 2>/dev/null; then
    python3 - "$ENVF" "$k" "$v" <<'PY'
import sys
p, k, v = sys.argv[1:4]
lines = open(p, encoding="utf-8").read().splitlines()
out = [f"{k}={v}" if l.startswith(f"{k}=") else l for l in lines]
open(p, "w", encoding="utf-8").write("\n".join(out) + "\n")
PY
  else
    printf '%s=%s\n' "$k" "$v" >> "$ENVF"
  fi
}

[ -f "$ENVF" ] || { echo "✗ 没有 $ENVF —— 这个脚本是给「已经装好的部署」用的" >&2; exit 2; }

if [ "${1:-}" = "--off" ]; then
  say "切回官方二进制：把 HCA_DAEMON_VARIANT 清空（人设与 deny 两行留着也不生效，但一起注掉更清楚）"
  set_kv HCA_DAEMON_VARIANT ""
  set_kv HCA_LLM_SYSTEM_PROMPT_FILE ""
  set_kv HCA_TOOLS_DENY ""
  cd "$REPO" && bash deploy/up.sh --no-build
  exit 0
fi

BIN="${1:?用法：switch-to-fork.sh <fork 二进制路径> | --off}"
[ -f "$BIN" ] || { echo "✗ 二进制不在：$BIN" >&2; exit 2; }

say "① 叠 fork 镜像层"
cd "$REPO" && bash deploy/fork-image.sh "$BIN"

say "② 写 deploy/.env 三行"
set_kv HCA_DAEMON_VARIANT "-fork"
set_kv HCA_LLM_SYSTEM_PROMPT_FILE "$PERSONA"
set_kv HCA_TOOLS_DENY "$DENY"

say "③ 用新镜像重建容器"
bash deploy/up.sh --no-build

say "④ 证明它生效了（不是「配置写进去了」而已）"
C="${PROJECT}-daemon"
docker exec "$C" atomcode --version
echo "--- config.toml 里那两段（回显得出来才算生效）---"
docker exec "$C" sh -c 'grep -nE "system_prompt_file|^\[tools\]|^deny|^allow" /data/atomcode/config.toml' \
  || { echo "✗ config.toml 里没有那两段 —— hca-init 没写进去，检查三个环境变量是否真的传进容器" >&2; exit 3; }
say "完成。想进一步确认模型看到的工具清单真的变短了，用 tools/probe/fork_verify.py"
