#!/usr/bin/env bash
# I1 对比测试的栈起停 —— 就是 i2-up.sh，换一套目录与 compose 项目名。
#
#     bash i1-up.sh opt-fork          # B 线：发布形态（v0.2.0 · fork 二进制 · 全部 MCP）
#     bash i1-up.sh opt-fork lite     # A 线：MCP 压成与社区版**相同的 6 个**
#
# 为什么不直接用 i2-up.sh：I2 的栈（项目 `hca-i2`、目录 `~/hca/repo-i2`）是那一轮
# 的证据环境，I1 要改题集、要反复起停，两轮各用各的目录才不会互相覆盖。
# 差异全部通过环境变量传进 i2-up.sh，**脚本逻辑一行不分叉** ——
# 分叉出第二份就一定会出现「两份起栈脚本做的事悄悄不一样」。
#
# 与 I2 的两处刻意不同，都写在 docs/eval/c1/README.md 里：
#   1. **Kronos key 接上**（I2 那十二个批次是清空的）。I1 任务书明写「两边都接上
#      Kronos key，让 q4 这类题两边都能真跑」。后果要说清楚：社区版没有任何预测类
#      MCP，所以 B 线的 q4 变成「HCA 真跑 GPU 推理 vs 社区版如实说没有」——
#      **这一题的 B 线数字与 M2 / I2 不可比**，报告里单独标。A 线把 kronos 关掉，
#      两边同样没有预测能力，仍与历史可比。
#   2. 题集是 10 道（M2 五道 + I1 新增五道），`run_ab.py --question-set all`。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export HCA_EVAL_REPO="${HCA_EVAL_REPO:-/home/support/hca/repo-i1}"
export HCA_EVAL_PROJECT="${HCA_EVAL_PROJECT:-hca-i1}"
export HCA_EVAL_TRACE="${HCA_EVAL_TRACE:-/home/support/hca/i1-trace}"
export HCA_EVAL_BASE_REPO="${HCA_EVAL_BASE_REPO:-/home/support/hca/repo-i2-base}"
mkdir -p "$HCA_EVAL_TRACE"
exec bash "${HCA_EVAL_REPO}/deploy/eval/i2-up.sh" "$@"
