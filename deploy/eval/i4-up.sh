#!/usr/bin/env bash
# I4 响应时间专项评测的栈起停 —— 就是 i2-up.sh，换一套目录与 compose 项目名。
#
#     bash i4-up.sh opt-fork          # B 线：发布形态（v0.2.1 · fork 二进制 · 全部 MCP）
#     bash i4-up.sh opt-fork lite     # A 线：MCP 压成与社区版**相同的 6 个**
#
# 与 I1 / I3 同样的道理：各轮的栈是各轮的证据环境，各用各的目录与项目名才不会互相覆盖。
# 差异全部通过环境变量传进 i2-up.sh，**脚本逻辑一行不分叉**。
#
# I4 的口径（与 I1 对齐，因为题集就是 I1 那十道）：
#   1. `deploy/.env` 从 `~/hca/repo-i1/deploy/.env` 拷来 —— 其中 **Kronos key 是接上的**。
#      后果与 I1 一样：B 线 q4 是「HCA 真跑 GPU 推理 vs 社区版如实说没有」，
#      **这一题的 B 线数字与 I2/I3 不可比**；A 线把 kronos 关掉，两边同样没有预测能力。
#   2. 题集 `--question-set all`（10 道）+ `idle`（纯引擎空载题）。
#   3. 代码是 **main（v0.2.1）**，不是 I1 当时的 v0.2.0 —— 人设里那张「按结构卡」
#      是 I3 改对后的版本。所以 I4 的绝对值不能直接和 I1 的绝对值比，
#      能比的是**同一批次内两边的相对关系**。
set -euo pipefail
export HCA_EVAL_REPO="${HCA_EVAL_REPO:-/home/support/hca/repo-i4}"
export HCA_EVAL_PROJECT="${HCA_EVAL_PROJECT:-hca-i4}"
export HCA_EVAL_TRACE="${HCA_EVAL_TRACE:-/home/support/hca/i4-trace}"
export HCA_EVAL_BASE_REPO="${HCA_EVAL_BASE_REPO:-/home/support/hca/repo-i2-base}"
mkdir -p "$HCA_EVAL_TRACE"
exec bash "${HCA_EVAL_REPO}/deploy/eval/i2-up.sh" "$@"
