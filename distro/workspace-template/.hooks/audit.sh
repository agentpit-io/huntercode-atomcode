#!/bin/sh
# HCA 审计 hook · PostToolUse · 只是选一个 python 然后把 stdin 原样交给 audit.py。
# 恒 exit 0：审计不参与权限判定，写不进日志也不能拖垮对话。
set -u
DIR=$(dirname "$0")
PY="${HCA_PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
exec "$PY" "$DIR/audit.py" "${HCA_AUDIT_LOG:-}"
