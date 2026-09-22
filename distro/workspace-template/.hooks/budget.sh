#!/bin/sh
# HCA 预算 hook · UserPromptSubmit（拦）+ Stop / StopFailure（记账）· 默认关闭。
# 开关是 HCA_BUDGET_ENABLED=1；关着的时候 budget.py 读完 stdin 立刻 exit 0、什么都不做。
set -u
DIR=$(dirname "$0")
PY="${HCA_PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
exec "$PY" "$DIR/budget.py"
