#!/bin/sh
# HCA 语言守卫（提示词侧）· UserPromptSubmit · 同 guard.sh / context.sh 的两文件结构。
# stdout 会被上游追加到用户这一条消息正文后面（cc_hooks.rs:680-684）。
set -u
DIR=$(dirname "$0")
PY="${HCA_PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
exec "$PY" "$DIR/lang.py"
