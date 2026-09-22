#!/usr/bin/env bash
# 重建 daemon 镜像并带评测覆盖拉起，然后在低负载下确认 9 个 MCP 全连上。
#
# 为什么要重建：.mcp.json 是**烤进镜像**的模板（Dockerfile.daemon COPY
# distro/workspace-template → /opt/hca/workspace-template），容器起来时由
# hca-init.py 重铺到 /workspace。M2 把 5 个 server 的 timeout_ms 从 60000
# 提到 120000，不重建镜像就只在容器里改了一份、重启即失。
#
# ⚠️ 必须带 deploy/eval/docker-compose.hca-api.yml 覆盖拉起，否则
# HERMES_API_URL / HUNTER_USER_ID 丢掉、也不再接在 hca-eval-net 上，
# 6 个 hunter 系 MCP 会悄无声息变回"连得上但调不通"（待办池 P0-8）。
set -uo pipefail
cd ~/hca/repo || exit 2
L=~/hca/logs; mkdir -p "$L"
say(){ echo "[$(date -u +%FT%TZ)] $*"; }

say "等重负载锁（cargo test 可能还在跑）"
flock ~/.hca-heavy.lock docker compose -p hca \
  -f deploy/docker-compose.yml --env-file deploy/.env build daemon || exit 3
say "镜像重建完成"

docker compose -p hca \
  -f deploy/docker-compose.yml \
  -f deploy/eval/docker-compose.hca-api.yml \
  --env-file deploy/.env up -d --wait || exit 4
say "容器已拉起"

got=$(docker exec hca-daemon sha256sum /usr/local/bin/atomcode | cut -d" " -f1)
say "daemon 二进制 sha256=${got}"

# 等负载降下来再确认 MCP —— 2 核机器上负载高时 initialize 会超时（P2-7）
for i in $(seq 1 30); do
  l=$(awk '{print int($1)}' /proc/loadavg)
  [ "$l" -lt 4 ] && break
  say "load=${l}，等 60s（${i}/30）"
  sleep 60
done
say "load=$(cut -d' ' -f1 /proc/loadavg)"

T=$(docker exec hca-daemon cat /run/hca/daemon-token)
for round in 1 2 3 4 5; do
  docker exec hca-daemon curl -s -X POST -H "Authorization: Bearer $T" \
    -H "Content-Type: application/json" -d '{}' \
    http://127.0.0.1:13456/mcp/reload >/dev/null
  for i in $(seq 1 30); do
    sleep 10
    st=$(docker exec hca-daemon curl -s -H "Authorization: Bearer $T" \
         http://127.0.0.1:13456/mcp/status)
    grep -q connecting <<<"$st" || break
  done
  say "第 ${round} 轮：$(python3 -c "
import json,sys
d=json.loads(sys.argv[1])
print(' '.join(f\"{x['name']}={x['status']}\" for x in d['servers']))" "$st")"
  grep -q '"status":"error"' <<<"${st//\", \"/\",\"}" || { say "✓ 9/9 全连上"; exit 0; }
  python3 - "$st" <<'PY' || { echo "✓ 全连上"; exit 0; }
import json,sys
d=json.loads(sys.argv[1])
bad=[x["name"] for x in d["servers"] if x["status"]!="connected"]
sys.exit(1 if not bad else 0)
PY
  sleep 20
done
say "✗ 5 轮 reload 后仍未全连上"
exit 5
