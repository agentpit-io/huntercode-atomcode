#!/bin/sh
# HCA 研究守卫 hook · PreToolUse · 选一个 python 然后把 stdin 原样交给 guard.py。
#
# 为什么是 .sh + .py 两个文件（和 audit.sh / audit.py 同一个理由）：
# 在 .sh 里用 heredoc 喂 python 会占掉 stdin，python 读到的是脚本自己而不是
# hook 的输入（M1 实测踩过，session_id / tool 全是 null）。
#
# 退出码恒 0：判定走 stdout 最后一行 JSON（上游 cc_hooks.rs:794 last_json_line）。
# 不用 exit 2 —— exit 2 且无输出会被上游当成"脚本坏了"而放行，语义更绕。
set -u
DIR=$(dirname "$0")
PY="${HCA_PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
exec "$PY" "$DIR/guard.py"
