#!/usr/bin/env bash
# 把仓库同步到测试机 —— **排除 docs/eval**。
#
# 为什么单独写个脚本：A/B 评测的原始记录是测试机上生成的，写在
# `~/hca/repo/docs/eval/raw/`。用带 --delete 的 rsync 从开发机推过去，
# 会把还没取回来的评测结果删掉 —— 那是几百万 token 换来的东西。
# 取回结果用反方向的 rsync（tools/eval/sync-from-test.sh）。
set -euo pipefail
HOST="${HCA_TEST_HOST:-support@34.133.8.3}"
KEY="${HCA_TEST_KEY:-$HOME/.ssh/id_rsa_google_longterm}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
rsync -az --delete \
  --exclude node_modules --exclude .next --exclude target --exclude .git \
  --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude 'deploy/.env' --exclude 'deploy/eval/.env' \
  --exclude 'docs/eval' \
  -e "ssh -i ${KEY} -o StrictHostKeyChecking=no" \
  "${REPO}/" "${HOST}:~/hca/repo/"
echo "[sync] 已推送（docs/eval 未动）"
