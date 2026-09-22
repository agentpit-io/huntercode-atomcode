#!/usr/bin/env bash
# 端到端回归的统一入口 —— **在测试机宿主上跑**，对着真部署打。
#
#     bash tools/e2e/run.sh                       # 全套：MCP 冒烟 → M3 10 步 → M4 6 项 → SSE 重连
#     bash tools/e2e/run.sh --suites mcp,m3       # 只跑其中几套
#     bash tools/e2e/run.sh --base http://localhost:3200 --secrets ~/hca/secrets --out ~/hca/pw/m5
#
# 为什么要有这个文件：`docs/开发者指南.md` 从 M4 起就写着这条命令，
# **而它一直不存在**（M5 补）。和 R-5 / R-15 是同一族问题 ——
# 文档里写着的取证命令跑不通，等于没写。所以这里只做一件事：
# 把指南里那几条真实命令按正确的工作目录、正确的参数串起来，一条不改。
#
# 前置（测试机上一次性装好，见 tools/e2e/README.md）：
#   mkdir -p ~/hca/pw && cd ~/hca/pw && npm i playwright@1.63.0 && npx playwright install chromium --with-deps
# Playwright 脚本**必须从 ~/hca/pw 跑**（node_modules 在那儿），这也是这个包装脚本存在的理由之一。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="http://localhost:3200"
SECRETS="${HOME}/hca/secrets"
OUT="${HOME}/hca/pw/out"
PW_DIR="${HCA_PW_DIR:-${HOME}/hca/pw}"
SUITES="mcp,m3,m4,sse"

while [ $# -gt 0 ]; do
  case "$1" in
    --base)    BASE="$2"; shift 2 ;;
    --secrets) SECRETS="$2"; shift 2 ;;
    --out)     OUT="$2"; shift 2 ;;
    --pw-dir)  PW_DIR="$2"; shift 2 ;;
    --suites)  SUITES="$2"; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

has() { case ",${SUITES}," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
mkdir -p "$OUT"

declare -a NAMES=() CODES=()
run() {  # run <名字> <命令…>
  local name="$1"; shift
  echo ""
  echo "===== ${name} ===== $(date '+%Y-%m-%d %H:%M:%S')"
  "$@"
  local rc=$?
  NAMES+=("$name"); CODES+=("$rc")
  echo "----- ${name} 退出码 ${rc} -----"
  return 0   # 一套失败不挡后面几套；汇总里如实记
}

# 1) MCP 冒烟：零 token，逐个真调。**在 daemon 容器里跑** —— 宿主没有那些 venv。
if has mcp; then
  run "MCP 冒烟（9 个 server 逐个真调）" bash -c \
    "docker cp '${HERE}/mcp_smoke.py' hca-daemon:/tmp/mcp_smoke.py >/dev/null &&
     docker exec hca-daemon /opt/hca/venv/bin/python /tmp/mcp_smoke.py --json-out /tmp/mcp_smoke.json &&
     docker cp hca-daemon:/tmp/mcp_smoke.json '${OUT}/mcp_smoke.json' >/dev/null"
fi

# 2) Playwright。**脚本文件本身必须落在 PW_DIR 里**，不能只是 cd 过去再按仓库路径跑：
#    node 的 ESM 解析是按「导入它的那个文件」的位置找 node_modules 的，跟 cwd 无关。
#    这个包装脚本的第一版就是这么写的，四套里三套当场 ERR_MODULE_NOT_FOUND ——
#    正是它本来要修的那类「文档里写着、实际跑不通」的问题。
if has m3 || has m4 || has sse; then
  [ -d "${PW_DIR}/node_modules/playwright" ] || {
    echo "✗ ${PW_DIR} 里没有 playwright。先装：mkdir -p ${PW_DIR} && cd ${PW_DIR} && npm i playwright@1.63.0 && npx playwright install chromium --with-deps" >&2
    exit 3
  }
  cp "${HERE}"/lib.mjs "${HERE}"/m3-web.mjs "${HERE}"/m4-regression.mjs "${HERE}"/m3-sse-reconnect.mjs "${PW_DIR}/"
fi

if has m3; then
  run "M3 真浏览器 10 步" bash -c \
    "cd '${PW_DIR}' && node ./m3-web.mjs --base '${BASE}' --secrets '${SECRETS}' --out '${OUT}/m3'"
fi
if has m4; then
  run "M4 回归 6 项" bash -c \
    "cd '${PW_DIR}' && node ./m4-regression.mjs --base '${BASE}' --secrets '${SECRETS}' --out '${OUT}/m4'"
fi
if has sse; then
  run "SSE 断线重连（只断 SSE）" bash -c \
    "cd '${PW_DIR}' && node ./m3-sse-reconnect.mjs --base '${BASE}' --secrets '${SECRETS}'"
fi

echo ""
echo "===== 汇总 ====="
fail=0
for i in "${!NAMES[@]}"; do
  if [ "${CODES[$i]}" -eq 0 ]; then printf '  ✓ %s\n' "${NAMES[$i]}"
  else printf '  ✗ %s（退出码 %s）\n' "${NAMES[$i]}" "${CODES[$i]}"; fail=1; fi
done
echo "产物：${OUT}"
exit "$fail"
