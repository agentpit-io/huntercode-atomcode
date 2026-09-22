#!/usr/bin/env bash
# 从测试机取回**测试机上生成**的产物（不带 --delete）：
#   docs/eval        A/B 评测原始记录
#   docs/evidence    hook 用例输出、MCP 冒烟结果、SSE 抓包（M4 起）
#   docs/screenshots Playwright 截图（M4 起）
set -euo pipefail
HOST="${HCA_TEST_HOST:-support@34.133.8.3}"
KEY="${HCA_TEST_KEY:-$HOME/.ssh/id_rsa_google_longterm}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# I2：源目录可换（I2 的评测跑在 ~/hca/repo-i2，M5 的浸泡在 ~/hca/repo）
SRC="${HCA_TEST_SRC:-~/hca/repo}"
for d in eval evidence screenshots; do
  mkdir -p "${REPO}/docs/${d}"
  rsync -az -e "ssh -i ${KEY} -o StrictHostKeyChecking=no" \
    "${HOST}:${SRC}/docs/${d}/" "${REPO}/docs/${d}/" || true
  printf '[sync] 已取回 docs/%s：%s\n' "$d" "$(du -sh "${REPO}/docs/${d}" | cut -f1)"
done
