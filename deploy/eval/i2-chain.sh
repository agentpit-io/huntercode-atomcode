#!/usr/bin/env bash
# 基线跑完就接着跑优化四条线，中间不留空档。
#
# 顺序里特意插了一次 **fork 冒烟**（q3 一道题、不计分）：fork 那两条线最怕的是
# 「参数写了但二进制不认」，那种失败在批次里看起来只是「跑得和 opt 一样」，
# 事后分不清是补丁没生效还是补丁没用。先单跑一次，把 config.toml 的回显和
# 工具数看一眼，再开正式批次。
set -u
LOGDIR=~/hca/i2-logs
say(){ printf '[chain %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

while pgrep -f "i2-batches.sh base" > /dev/null 2>&1 || pgrep -f "run_ab.py" > /dev/null 2>&1; do
  sleep 60
done
say "基线已结束，等 30 秒让机器落回空闲"; sleep 30; uptime

say "=== fork 冒烟：q3 一道题（不计分）==="
bash ~/hca/i2-batch.sh opt-fork full smoke-fork 1 --only q3 > "${LOGDIR}/smoke-fork.log" 2>&1
say "冒烟 rc=$?；起栈回显："
grep -E "fork 二进制|config.toml|system_prompt_file|^deny|api-probe|账本|MCP " "${LOGDIR}/smoke-fork.log" | head -12
grep -aE "rc=|用时" "${LOGDIR}/smoke-fork.log" | tail -4

say "=== 优化四条线 ==="
bash ~/hca/i2-batches.sh all
say "全部结束"
