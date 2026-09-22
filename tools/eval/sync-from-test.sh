#!/usr/bin/env bash
# 从测试机取回评测产物（只取 docs/eval，不带 --delete）。
set -euo pipefail
HOST="${HCA_TEST_HOST:-support@34.133.8.3}"
KEY="${HCA_TEST_KEY:-$HOME/.ssh/id_rsa_google_longterm}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mkdir -p "${REPO}/docs/eval"
rsync -az -e "ssh -i ${KEY} -o StrictHostKeyChecking=no" \
  "${HOST}:~/hca/repo/docs/eval/" "${REPO}/docs/eval/"
echo "[sync] 已取回 docs/eval："
du -sh "${REPO}/docs/eval"
