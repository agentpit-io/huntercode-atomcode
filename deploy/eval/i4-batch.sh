#!/usr/bin/env bash
# I4 的批次驱动 —— 起栈 → 同库取证 → 等负载降下来 → 预热（数据丢弃）→ 正式跑。
#
#     bash i4-batch.sh opt-fork full  i4-b    6  --question-set all
#     bash i4-batch.sh opt-fork lite  i4-a    6  --question-set all
#     bash i4-batch.sh opt-fork full  idle-b 20  --question-set idle
#
# 与 I1/I3 的三处不同，都是 I4 任务书要求的：
#   1. **每批开跑前记 `uptime`，负载 > 2 就等**（最多等 30 分钟）。
#   2. **每批开跑前跑一次同库证据脚本**，输出存进批次目录，不通过就不开跑。
#   3. **先预热一次再开跑，预热数据丢弃** —— 预热跑进 `<批次>/warmup/`，
#      汇总脚本只读批次目录下的 `*.json`，不会递归进 warmup/，所以自然被排除。
#      预热跑哪道题用 `I4_WARMUP_ONLY` 指定（默认 q1；空载批次要传 q0，
#      否则 `--only q1` 会把 idle 题集整个筛空、预热变成跑了个寂寞）。
set -u
PHASE="${1:?opt-fork}"
LINE="${2:?full|lite}"
OUT="${3:?输出目录名}"
REPEAT="${4:-6}"
shift 4 2>/dev/null || shift $#

export HCA_EVAL_REPO="${HCA_EVAL_REPO:-/home/support/hca/repo-i4}"
cd "$HCA_EVAL_REPO"
OUTDIR="docs/eval/i4/${OUT}"
mkdir -p "$OUTDIR"

echo "[i4] ==== 批次 ${OUT}（阶段 ${PHASE} · 线 ${LINE} · 每题 ${REPEAT} 遍）===="
echo "[i4] 开跑前 uptime：$(uptime)"

# ── 负载闸：> 2 就等 ───────────────────────────────────────────────────────
# I3 自己犯过的错：探针把机器压到负载 6～7 还在计时（报告 §5）。这一轮把它变成闸。
waited=0
while :; do
  load1=$(awk '{print $1}' /proc/loadavg)
  if awk -v l="$load1" 'BEGIN{exit !(l<=2.0)}'; then break; fi
  [ "$waited" -ge 30 ] && { echo "[i4] ✗ 等了 30 分钟负载仍是 ${load1}，放弃开跑"; exit 7; }
  echo "[i4] 负载 ${load1} > 2，等 60 秒（已等 ${waited} 分钟）"; sleep 60; waited=$((waited+1))
done
echo "[i4] 负载闸通过：$(cat /proc/loadavg)"

bash deploy/eval/i4-up.sh "$PHASE" "$LINE" || { echo "[i4] ✗ 起栈失败"; exit 1; }

export HCA_DAEMON_CONTAINER=hca-i4-daemon
export HCA_SECRETS_DIR=/home/support/hca/secrets
export HCA_SHIM_TRACE_FILE=/home/support/hca/i4-trace/requests.jsonl
export HCA_EVAL_UP_CMD="true"

# ── 同库取证：不通过就不开跑 ───────────────────────────────────────────────
bash deploy/eval/i4-same-db-proof.sh "$HCA_DAEMON_CONTAINER" \
  > "${OUTDIR}/same-db-proof.txt" 2>&1
rc=$?
tail -3 "${OUTDIR}/same-db-proof.txt"
if [ "$rc" != "0" ]; then
  echo "[i4] ✗ 同库证据不通过（见 ${OUTDIR}/same-db-proof.txt），不开跑"; exit 6
fi
echo "[i4] 同库证据通过 → ${OUTDIR}/same-db-proof.txt"

# ── 预热：每边各一次，数据丢弃 ─────────────────────────────────────────────
# 为什么要预热：两边的第一次调用都要付一笔只付一次的钱（daemon 首次绑会话、
# opencode 首次建会话、api 的连接池与行内缓存、MCP 冷启动）。I2/I3 的批次里
# 这笔钱摊在第一遍上，正好会让「谁先跑」的先手偏差和它混在一起。
if [ "${I4_SKIP_WARMUP:-0}" != "1" ]; then
  echo "[i4] 预热（跑进 ${OUTDIR}/warmup/，汇总时不读）"
  python3 tools/eval/run_ab.py --out "${OUTDIR}/warmup" --repeat 1 \
    --gap 5 --min-quota 2000000 --mcp-retries 5 --mcp-backoff 60 \
    --only "${I4_WARMUP_ONLY:-q1}" "$@" || echo "[i4] ⚠ 预热非零退出，如实记录后继续"
fi

echo "[i4] 正式开跑 $(date '+%H:%M:%S')"
python3 tools/eval/run_ab.py --out "$OUTDIR" --repeat "$REPEAT" \
  --gap 8 --min-quota 2000000 --mcp-retries 5 --mcp-backoff 60 "$@"
rc=$?
echo "[i4] 收跑后 uptime：$(uptime)"
echo "RC=${rc}  批次 ${OUT} 结束 $(date '+%H:%M:%S')"
exit "$rc"
