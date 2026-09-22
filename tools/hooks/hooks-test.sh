#!/usr/bin/env bash
# 用**真实的** `atomcode hooks test` 把 .hooks.json 里每一条注册都跑一遍，输出留档。
#
#   bash tools/hooks/hooks-test.sh [输出目录]
#
# 注意 `hooks test <名>` 的匹配规则（cli/main.rs:3752）：按**事件名或 command 子串**
# 找第一个命中的 hook。同一个脚本注册在多个事件上时（audit.sh 挂了两个事件、
# budget.sh 挂了三个），必须用事件名来点名，否则永远命中字典序最靠前的那一条。
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:-${REPO}/docs/evidence/M4/hooks-test}"
CID="${HCA_DAEMON_CONTAINER:-hca-daemon}"
WS="${HCA_WORKSPACE_IN_CONTAINER:-/workspace}"
mkdir -p "$OUT"

run() { # run <文件名> <hooks test 的参数>
  local name="$1"; shift
  echo "── $name ($*) ─────────────────────────────"
  docker exec -w "$WS" "$CID" atomcode hooks test "$@" 2>&1 | tee "${OUT}/${name}.txt"
  echo
}

docker exec -w "$WS" "$CID" atomcode hooks list 2>&1 | tee "${OUT}/00-list.txt"
echo

# 8 条注册，逐条点名。
# ⚠️ 按**事件名**匹配时命中的是该事件下**名字字典序最靠前**的那一条
#    （hooks 是 BTreeMap，find 取第一个命中）——
#    所以 `hooks test UserPromptSubmit` 命中的是 hca-budget，不是 hca-context。
#    context / lang 另外用 command 子串点名。
run 01-guard-PreToolUse      PreToolUse
run 02-context-lang-budget-UserPromptSubmit UserPromptSubmit
run 03-lang                  lang.sh
run 03b-context              context.sh
run 04-budget-prompt         budget.sh
run 05-audit-PostToolUse     PostToolUse
run 06-audit-PostToolUseFailure PostToolUseFailure
run 07-budget-Stop           Stop
run 08-budget-StopFailure    StopFailure

echo "全部输出在 ${OUT}"
