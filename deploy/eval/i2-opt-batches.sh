#!/usr/bin/env bash
# I2 优化两条线 + fork 两条线（评测机 hca-bench-01）。
#
#     bash deploy/eval/i2-opt-batches.sh base       # 基线两条线
#     bash deploy/eval/i2-opt-batches.sh opt        # 四个外部面两条线
#     bash deploy/eval/i2-opt-batches.sh fork       # fork 两条线
#     bash deploy/eval/i2-opt-batches.sh all        # 优化四条线（opt + fork）
#
# 顺序：opt-b → opt-a → opt-fork-b → opt-fork-a。
# 先 opt 后 fork 的道理和「先基线后优化」一样 —— 万一中途出事，手里留下的是
# 「四个外部面的收益」而不是「只有 fork 的数，不知道其中多少来自外部面」。
set -u
WHICH="${1:-all}"
LOGDIR=~/hca/i2-logs; mkdir -p "$LOGDIR"
say(){ printf '[opt-batches %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

SPECS=()
case "$WHICH" in
  all)  SPECS=("opt full opt-b" "opt lite opt-a" "opt-fork full opt-fork-b" "opt-fork lite opt-fork-a") ;;
  base) SPECS=("baseline full baseline-b" "baseline lite baseline-a") ;;
  opt)  SPECS=("opt full opt-b" "opt lite opt-a") ;;
  fork) SPECS=("opt-fork full opt-fork-b" "opt-fork lite opt-fork-a") ;;
  *) echo "用法：$0 [base|opt|fork|all]" >&2; exit 2 ;;
esac

for spec in "${SPECS[@]}"; do
  set -- $spec; phase="$1"; line="$2"; out="$3"
  # 负载闸：总控要求评测期间机器上不许有别的负载，> 4 就等（最多等 20 分钟）。
  for _ in $(seq 40); do
    l1=$(cut -d' ' -f1 /proc/loadavg)
    awk -v l="$l1" 'BEGIN{exit !(l>4)}' || break
    say "负载 $l1 > 4，等 30 秒"; sleep 30
  done
  say "=== 批次 ${out}（阶段 ${phase} / 线 ${line}）开跑，负载 $(cut -d' ' -f1-3 /proc/loadavg) ==="
  bash ~/hca/i2-batch.sh "$phase" "$line" "$out" 3 > "${LOGDIR}/${out}.log" 2>&1
  rc=$?
  say "=== 批次 ${out} rc=${rc} ==="; tail -3 "${LOGDIR}/${out}.log"
  [ "$rc" = "0" ] || say "⚠ 非零退出，**继续跑后面的批次**（手里有多少数据算多少）"
done
say "全部结束"
