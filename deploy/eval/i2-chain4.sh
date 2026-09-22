#!/usr/bin/env bash
# I2 第四段链路：opt4-fork-b / opt4-fork-a，每题 **4 遍**。
#
# 遍数从 6 降到 4 是**时间预算**，不是口径放松：偶数这一点必须保住（§1.10c 的先手偏差
# 靠它抵掉），而 4 遍已经是能保住偶数的最小值。代价是中位数更抖 —— 所以 opt4 的数
# 只用来回答「§2.9.4 有没有把字数压下去」，逐题达标的正式判定仍以 6 遍的 opt3 为主，
# 两批的数都在报告 §4.6 里并排列出。
#
# 为什么还有第四段：`opt3` 证明了「剩下的差主要是答得长」（q3 的墙钟差里出字占 71%，
# q4 更极端 —— 模型那一段 HCA 比社区版还快 475 ms，全部差距来自多写的 537 个字），
# 但**光给字数上限模型不照做**（q3 上限 900、实际写 1 583）。所以 §2.9.4 把它改成
# 按结构卡（结论 ≤ 3 句 / 依据最多 4 条 / 风险项最多 3 条 / 不写额外段落），
# 另加 §2.9.5「资产清单不是叫你去读」（A 线 q1 那次多出来的 read_file）。
# 这一段就是测这两项。
#
# **为什么是 6 遍而不是 3 或 5**：§1.10c —— 两套栈共用同一个 api，api 里有带 TTL
# 的行内缓存，**先调的那一方付全价、后调的命中缓存**（q3 的 market_screen：
# 先手 495～1 065 ms、后手 46～226 ms，36 格无一例外）。run_ab.py 每遍换先手，
# 所以奇数遍时先手按 2:1 分，两边的中位数落在不同档上，q3 上白送社区版约 0.5 秒。
# 偶数遍先手 3:3，这个偏差自然抵掉。
#
# 哨兵：`~/hca/GO-OPT4` 由人在「opt2 两个批次都结束 + 仓库已同步（新人设）」之后创建。
# 模板与人设是**挂**进容器的，中途同步会把还没跑完的批次污染成「一半旧一半新」。
set -u
LOGDIR=~/hca/i2-logs; mkdir -p "$LOGDIR"
say(){ printf '[chain4 %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

say "等哨兵 ~/hca/GO-OPT4（人确认 opt2 两批已结束、仓库已同步之后创建）"
for _ in $(seq 240); do            # 最多等 2 小时
  [ -f ~/hca/GO-OPT4 ] && break
  sleep 30
done
if [ ! -f ~/hca/GO-OPT4 ]; then
  say "✗ 等不到哨兵，退出（不跑任何批次，免得拿旧人设测出一份看起来像新数据的东西）"
  exit 1
fi
rm -f ~/hca/GO-OPT4

# 还有别的批次在跑就等 —— 同一台机器上两个批次一起跑，两边的计时都不能用。
# 同 R-19：判据只认真的批次进程（命令行里带 tools/eval/ 前缀的那个）
while pgrep -f "tools/eval/run_ab.py" >/dev/null 2>&1; do
  say "还有 run_ab.py 在跑，等 30 秒"; sleep 30
done
uptime

for spec in "opt-fork full opt4-fork-b" "opt-fork lite opt4-fork-a"; do
  set -- $spec; phase="$1"; line="$2"; out="$3"
  for _ in $(seq 40); do
    l1=$(cut -d' ' -f1 /proc/loadavg)
    awk -v l="$l1" 'BEGIN{exit !(l>4)}' || break
    say "负载 $l1 > 4，等 30 秒"; sleep 30
  done
  say "=== 批次 ${out}（${phase} / ${line} / 4 遍）开跑，负载 $(cut -d' ' -f1-3 /proc/loadavg) ==="
  bash ~/hca/i2-batch.sh "$phase" "$line" "$out" 4 > "${LOGDIR}/${out}.log" 2>&1
  rc=$?
  say "=== 批次 ${out} rc=${rc} ==="; tail -3 "${LOGDIR}/${out}.log"
  [ "$rc" = "0" ] || say "⚠ 非零退出，**继续跑后面的批次**（手里有多少数据算多少）"
done
say "第四段全部结束"
