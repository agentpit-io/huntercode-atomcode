#!/usr/bin/env bash
# 见 run_hook_cases.py 的文件头。默认在 daemon 容器里跑**部署好的**那份 hook。
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${HCA_HOOK_CASE_OUT:-${REPO}/docs/evidence/M4/hook-cases.json}"
mkdir -p "$(dirname "$OUT")"
exec python3 "${REPO}/tools/hooks/run_hook_cases.py" --in-container --json-out "$OUT" "$@"
