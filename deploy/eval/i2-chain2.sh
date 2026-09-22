#!/usr/bin/env bash
# I2 第二段链路：收尾开销探针 + 「再优化」两条线（opt2-fork-b / opt2-fork-a）。
#
# 为什么要有第二段：第一段（基线两条线 + 优化两条线 + fork 两条线）跑完之后，
# 数据指出三件第一段没做的事 —— 组合工具缺公告（q5 因此走 akshare 三连）、
# 正文比社区版长 2～14 倍（§1.9 量出这是步数追平后墙钟差的大头）、
# q4 要先试调一次才知道本部署没有预测能力。三项都改完，要再测一遍。
#
# **重复次数从 3 提到 5**。用户口径是每题 3 次，但 §1.9 那张分布表显示这个通道上
# 单轮模型的 p90 是中位的 2 倍以上、> 8 秒的慢轮占 7%～14% ——
# 3 次的中位数分辨不了 5% 的差。5 次仍然不够精确，但比 3 次可信，
# 而且 3 次那一版的数照旧留在报告里（opt-fork-*），两个口径都能查。
#
# 顺序上卡一个哨兵文件：仓库要先从开发机同步过来（新的 MCP / 人设 / hook / 探针），
# 而同步必须发生在第一段全部结束之后 —— 模板与 MCP 目录是**挂**进容器的，
# 中途改会把还没跑完的批次污染成「一半旧一半新」。
set -u
LOGDIR=~/hca/i2-logs; mkdir -p "$LOGDIR"
say(){ printf '[chain2 %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

# ⚠️ 这里第一版是「pgrep 到没有 i2-opt-batches.sh / run_ab.py 就算第一段结束」。
# **那个判据是错的**：一个批次结束、下一个批次起栈之前有几十秒的空档，
# 里面既没有 run_ab.py 也可能一瞬间没有那个 pgrep 模式命中 ——
# 实测在 opt-a 结束、opt-fork-b 起栈之间就被它当成「第一段已结束」了，
# 差一步就在 opt-fork-b 跑着的时候去动挂进容器的仓库目录。
# 现在只认哨兵：由人（或上一段的最后一行）确认全部批次都结束之后才创建它。
say "等哨兵 ~/hca/GO-OPT2（**由人确认第一段全部批次结束、且仓库已同步之后创建**）"
for _ in $(seq 480); do            # 最多等 4 小时
  [ -f ~/hca/GO-OPT2 ] && break
  sleep 30
done

if [ ! -f ~/hca/GO-OPT2 ]; then
  say "✗ 等不到哨兵，退出（不跑任何批次，免得拿旧代码测出一份看起来像新数据的东西）"
  exit 1
fi
rm -f ~/hca/GO-OPT2
uptime

say "=== 收尾开销探针（不计分，5+5 次短问题）==="
bash ~/hca/repo-i2/deploy/eval/i2-finish-probe.sh > "${LOGDIR}/finish-probe.log" 2>&1
say "探针 rc=$?"; grep -E "收尾|逐次|摘掉后" "${LOGDIR}/finish-probe.log" | head -12

for spec in "opt-fork full opt2-fork-b" "opt-fork lite opt2-fork-a"; do
  set -- $spec; phase="$1"; line="$2"; out="$3"
  for _ in $(seq 40); do
    l1=$(cut -d' ' -f1 /proc/loadavg)
    awk -v l="$l1" 'BEGIN{exit !(l>4)}' || break
    say "负载 $l1 > 4，等 30 秒"; sleep 30
  done
  say "=== 批次 ${out}（${phase} / ${line} / 5 次）开跑，负载 $(cut -d' ' -f1-3 /proc/loadavg) ==="
  bash ~/hca/i2-batch.sh "$phase" "$line" "$out" 5 > "${LOGDIR}/${out}.log" 2>&1
  say "=== 批次 ${out} rc=$? ==="; tail -3 "${LOGDIR}/${out}.log"
done
say "第二段全部结束"
