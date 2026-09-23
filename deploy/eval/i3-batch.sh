#!/usr/bin/env bash
# I3 的批次驱动 —— 就是 i1-batch.sh 换一套目录/项目名，题集回到 M2 那五道。
#
#     bash i3-batch.sh opt-fork full opt5-fork-b 6      # B 线 · 产品形态（全部 MCP）
#
# 与 I2 同口径：`run_ab.py` 默认 `--question-set m2`、`deploy/.env` 从 repo-i2 拷来
# （KRONOS_API_KEY 是空的）、遍数取**偶数**（先手 3:3，抵掉 api 行内缓存的先手偏差）。
set -u
PHASE="${1:?opt-fork}"
LINE="${2:?full|lite}"
OUT="${3:?输出目录名}"
REPEAT="${4:-6}"
shift 4 2>/dev/null || shift $#

# 仓库目录可换：I3 要把「opt3 那版人设」与「opt5 改对版」在**同一天、同一台机器、
# 同一个网关**上对着跑一遍（I2 的 opt3 数字是 9 月 22 日夜里量的，不能直接当对照）。
# 办法是另放一份只有 distro/ 不同的仓库副本 ~/hca/repo-i3-opt3，其余一个字不差。
export HCA_EVAL_REPO="${HCA_EVAL_REPO:-/home/support/hca/repo-i3}"
cd "$HCA_EVAL_REPO"
bash deploy/eval/i3-up.sh "$PHASE" "$LINE" || { echo "✗ 起栈失败"; exit 1; }

export HCA_DAEMON_CONTAINER=hca-i3-daemon
export HCA_SECRETS_DIR=/home/support/hca/secrets
export HCA_SHIM_TRACE_FILE=/home/support/hca/i3-trace/requests.jsonl
export HCA_EVAL_UP_CMD="true"

python3 tools/eval/run_ab.py --out "docs/eval/i3/${OUT}" --repeat "$REPEAT" \
  --gap 8 --min-quota 2000000 --mcp-retries 5 --mcp-backoff 60 "$@"
echo "RC=$?  批次 ${OUT} 结束 $(date -u +%H:%M:%S)"
