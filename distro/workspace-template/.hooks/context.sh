#!/bin/sh
# HCA 上下文注入 hook · UserPromptSubmit · 同 guard.sh 的两文件结构。
#
# stdout 会被上游**追加到用户这一条消息的正文后面**（cc_hooks.rs:680-684），
# 所以这里输出什么，模型就多看到什么。拿不到的数据一律不输出（总控红线 1）。
set -u
DIR=$(dirname "$0")
PY="${HCA_PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
exec "$PY" "$DIR/context.py"
