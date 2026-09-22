#!/usr/bin/env bash
# I2 第三段链路：「再优化」第二轮（opt3-fork-b / opt3-fork-a），每题 **6 遍**。
#
# 为什么还有第三段：§1.10a 把「收尾」直接量了一遍 —— 只有 90 ms，不是拟合出来的
# 1.4 秒。于是步数追平之后 HCA 这边剩下的可控开销基本只有一项：**正文写了多少字**
# （2.3 ms/字）。§2.9 两项就是冲这一项去的（篇幅上限 + 路标收紧），要再测一遍。
#
# **为什么是 6 遍而不是 3 或 5**：§1.10c —— 两套栈共用同一个 api，api 里有带 TTL
# 的行内缓存，**先调的那一方付全价、后调的命中缓存**（q3 的 market_screen：
# 先手 495～1 065 ms、后手 46～226 ms，36 格无一例外）。run_ab.py 每遍换先手，
# 所以奇数遍时先手按 2:1 分，两边的中位数落在不同档上，q3 上白送社区版约 0.5 秒。
# 偶数遍先手 3:3，这个偏差自然抵掉。
#
# 哨兵：`~/hca/GO-OPT3` 由人在「opt2 两个批次都结束 + 仓库已同步（新人设）」之后创建。
# 模板与人设是**挂**进容器的，中途同步会把还没跑完的批次污染成「一半旧一半新」。
set -u
LOGDIR=~/hca/i2-logs; mkdir -p "$LOGDIR"
say(){ printf '[chain3 %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

say "等哨兵 ~/hca/GO-OPT3（人确认 opt2 两批已结束、仓库已同步之后创建）"
for _ in $(seq 240); do            # 最多等 2 小时
  [ -f ~/hca/GO-OPT3 ] && break
  sleep 30
done
if [ ! -f ~/hca/GO-OPT3 ]; then
  say "✗ 等不到哨兵，退出（不跑任何批次，免得拿旧人设测出一份看起来像新数据的东西）"
  exit 1
fi
rm -f ~/hca/GO-OPT3

# 还有别的批次在跑就等 —— 同一台机器上两个批次一起跑，两边的计时都不能用。
# 同 R-19：判据只认真的批次进程（命令行里带 tools/eval/ 前缀的那个）
while pgrep -f "tools/eval/run_ab.py" >/dev/null 2>&1; do
  say "还有 run_ab.py 在跑，等 30 秒"; sleep 30
done
uptime

for spec in "opt-fork full opt3-fork-b" "opt-fork lite opt3-fork-a"; do
  set -- $spec; phase="$1"; line="$2"; out="$3"
  for _ in $(seq 40); do
    l1=$(cut -d' ' -f1 /proc/loadavg)
    awk -v l="$l1" 'BEGIN{exit !(l>4)}' || break
    say "负载 $l1 > 4，等 30 秒"; sleep 30
  done
  say "=== 批次 ${out}（${phase} / ${line} / 6 遍）开跑，负载 $(cut -d' ' -f1-3 /proc/loadavg) ==="
  bash ~/hca/i2-batch.sh "$phase" "$line" "$out" 6 > "${LOGDIR}/${out}.log" 2>&1
  rc=$?
  say "=== 批次 ${out} rc=${rc} ==="; tail -3 "${LOGDIR}/${out}.log"
  [ "$rc" = "0" ] || say "⚠ 非零退出，**继续跑后面的批次**（手里有多少数据算多少）"
done
say "第三段全部结束"
