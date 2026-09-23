#!/usr/bin/env bash
# I3 性能收尾轮的栈起停 —— 就是 i2-up.sh，换一套目录与 compose 项目名。
#
#     bash i3-up.sh opt-fork          # B 线：发布形态（fork 二进制 · 全部 MCP）
#     bash i3-up.sh opt-fork lite     # A 线：MCP 压成与社区版**相同的 6 个**
#
# 与 I1 那份同样的道理：I2/I1 的栈（项目 `hca-i2` / `hca-i1`）是那两轮的证据环境，
# I3 要反复起停、还要为 U-20 的分档探针换工具清单，各用各的目录才不会互相覆盖。
# 差异全部通过环境变量传进 i2-up.sh，**脚本逻辑一行不分叉**。
#
# **与 I2 同口径**（任务书要求「与 I2 同口径」）：
#   1. `deploy/.env` 从 `~/hca/repo-i2/deploy/.env` 原样拷过来 —— 其中
#      `KRONOS_API_KEY` 是**空的**（理由见 docs/eval/i2/README.md：M2 的 q4 考的是
#      诚实度，给 HCA 一把能用的 key 就换了一道题；社区版也没有任何预测类 MCP）。
#      ⚠️ I1 那一轮是**接上** Kronos 的，所以 I3 的 q4 与 I1 的 q4 不可比、与 I2 可比。
#   2. 题集是 M2 那五道（`run_ab.py` 的默认 `--question-set m2`）。
set -euo pipefail
export HCA_EVAL_REPO="${HCA_EVAL_REPO:-/home/support/hca/repo-i3}"
export HCA_EVAL_PROJECT="${HCA_EVAL_PROJECT:-hca-i3}"
export HCA_EVAL_TRACE="${HCA_EVAL_TRACE:-/home/support/hca/i3-trace}"
export HCA_EVAL_BASE_REPO="${HCA_EVAL_BASE_REPO:-/home/support/hca/repo-i2-base}"
mkdir -p "$HCA_EVAL_TRACE"
exec bash "${HCA_EVAL_REPO}/deploy/eval/i2-up.sh" "$@"
