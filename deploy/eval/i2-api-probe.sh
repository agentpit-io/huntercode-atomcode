#!/usr/bin/env bash
# 一次**真调用**的探活：hunter 系 MCP 打基线 api 的那条路到底通不通。
#
#     bash deploy/eval/i2-api-probe.sh [容器名]
#
# 为什么要单独探一次：key 对不上时 `/mcp/status` 仍然是 9/9 connected、工具照样挂着，
# 只是每次调用回一句 {"detail":"internal auth failed"}。2026-09-23 评测机上第一次跑
# 基线批次就是这样 —— q3 从「1 次 market_screen 搞定」变成 29 次调用、286 秒、
# 撞满 30 轮上限，而所有状态面板都显示正常。
#
# 退出码：0 = 通；5 = 不通（调用方应当拒绝开跑）。
set -u
C="${1:-hca-i2-daemon}"
code=$(docker exec "$C" sh -c 'curl -s -o /tmp/probe.out -w "%{http_code}" -m 25 \
  -X POST -H "Content-Type: application/json" \
  -H "X-Hunter-Internal-Key: ${HUNTER_INTERNAL_KEY}" \
  -d "{\"limit\":1}" "${HERMES_API_URL}/api/internal/quant/market_screen"' 2>&1)
body=$(docker exec "$C" sh -c 'head -c 200 /tmp/probe.out' 2>/dev/null)
echo "[api-probe] POST /api/internal/quant/market_screen → HTTP ${code}"
if [ "$code" = "200" ]; then
  echo "[api-probe] 回包前 120 字：$(printf '%s' "$body" | head -c 120)"
  exit 0
fi
echo "[api-probe] ✗ 不通。回包：${body}" >&2
echo "[api-probe]   最常见的原因是 deploy/.env 的 HUNTER_INTERNAL_KEY 与基线 api 不是同一个。" >&2
exit 5
