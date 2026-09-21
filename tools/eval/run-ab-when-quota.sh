#!/usr/bin/env bash
# 等网关配额够了再跑 A/B（5 题 × 3 次 × 2 边 = 30 次），跑完顺手把自动分算出来。
#
# 为什么要等：M1 实测单次运行 5 万～52 万 token。评测当天 used_today 已到
# 952 万 / 上限 1000 万，硬跑会跑到一半被截断 —— 那种半截数据比没有更糟。
# 门槛给 500 万；run_ab.py 自己还有每次运行前的 30 万下限闸，且 index.json
# 可断点续跑，所以就算中途配额见底也不会丢已跑完的部分。
#
# **开跑前先确认 hca-daemon 里跑的是官方二进制**：M2 期间同一台机器上还编了
# fork 变体（hca-daemon:fork）。A/B 的被测对象是**官方 5.1.0**，如果容器被换成
# fork 版，整批数据就废了，而且看日志根本看不出来。这道校验放在**循环里、
# 真正开跑之前**做 —— 守候要挂十几个小时，期间容器完全可能被换掉或重建。
#
# 自锁（flock -n）：配了 crontab 每 10 分钟兜底拉起，两个实例同时跑会把
# index.json 写坏。锁拿不到就安静退出。
set -uo pipefail

LOCK=~/.hca-ab-watch.lock
exec 9>"$LOCK"
flock -n 9 || { echo "[$(date -u +%FT%TZ)] 已有守候实例在跑，退出"; exit 0; }

K=$(cat ~/hca/secrets/llm-test-key)
NEED=${HCA_AB_MIN_QUOTA:-5000000}
OFFICIAL_SHA=40d86fa3e31980dbcdc8a6e5707c822f3953284ff76b31523789ca19988c8763
cd ~/hca/repo || exit 2

while :; do
  rem=$(curl -s -m 20 -H "Authorization: Bearer $K" \
        https://hunter.agentpit.io/api/saas/llm/quota \
        | python3 -c "import json,sys;print(json.load(sys.stdin).get(\"remaining\",0))" 2>/dev/null || echo 0)
  echo "[$(date -u +%FT%TZ)] 剩余配额 ${rem}（需要 ≥ ${NEED}）"
  if [ "${rem:-0}" -ge "$NEED" ]; then
    got=$(docker exec hca-daemon sha256sum /usr/local/bin/atomcode 2>/dev/null | cut -d" " -f1)
    if [ "$got" = "$OFFICIAL_SHA" ]; then
      echo "[$(date -u +%FT%TZ)] 被测二进制已确认是官方 5.1.0（sha256 前 12 位 ${got:0:12}），开跑 A/B"
      break
    fi
    # 配额够了但二进制不对：不开跑，也不退出 —— 容器可能正在重建，接着等。
    echo "[$(date -u +%FT%TZ)] ⚠ hca-daemon 里的二进制不是官方 5.1.0（sha256=${got:-取不到}），暂不开跑"
  fi
  sleep 600
done

# ── 等机器闲下来再开跑 ──────────────────────────────────────────────────────
# M2 实测：一次 cargo 构建把 2 核机器压到 load 15 时，9 个 MCP 里有 5 个报
# `MCP request initialize timed out after 60000ms`；timeout_ms 提到 120000 之后
# 换成另外 3 个报 120000ms 超时。也就是说**放宽超时只能缓解，压不住负载**。
# 带着缺数据源的部署跑评测，比的不是两个 agent，而是这一次恰好有几个数据源。
# 所以：① 抢那把重负载锁（我们自己的构建/镜像任务都走它，总控约定），
#       ② 再等 1 分钟负载降到阈值以下，最多等 30 分钟。
MAXLOAD=${HCA_AB_MAX_LOAD:-4}
echo "[$(date -u +%FT%TZ)] 等重负载锁 ~/.hca-heavy.lock"
exec 8>~/.hca-heavy.lock
flock 8
echo "[$(date -u +%FT%TZ)] 已拿到重负载锁"
for i in $(seq 1 30); do
  l=$(awk "{print int(\$1)}" /proc/loadavg)
  [ "$l" -lt "$MAXLOAD" ] && break
  echo "[$(date -u +%FT%TZ)] load=${l} ≥ ${MAXLOAD}，等 60s（第 ${i}/30 次）"
  sleep 60
done
echo "[$(date -u +%FT%TZ)] load=$(cut -d" " -f1 /proc/loadavg)，开跑"

python3 tools/eval/run_ab.py --out docs/eval/raw --repeat 3
rc=$?
flock -u 8
echo "[$(date -u +%FT%TZ)] run_ab 退出 rc=${rc}"
# 不管跑完还是被配额截停，都把能自动算的分先算出来 —— 人工项的工作表也一并生成好
python3 tools/eval/score.py prepare --raw docs/eval/raw --out docs/eval/scores.json
echo "[$(date -u +%FT%TZ)] 自动分已算，人工工作表已生成 → docs/eval/scores.json"
exit $rc
