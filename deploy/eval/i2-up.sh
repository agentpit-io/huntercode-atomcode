#!/usr/bin/env bash
# I2 评测栈的起停 —— 两个阶段（baseline / opt）只差挂进去的模板与几个开关。
#
#     bash i2-up.sh baseline        # I2 之前那版行为
#     bash i2-up.sh opt             # 全部优化打开
#     bash i2-up.sh opt-fork        # 优化 + fork 二进制（人设整体替换、工具挂载黑名单）
#     bash i2-up.sh baseline lite   # 再加 A 线：MCP 压成与社区版**相同的 6 个**
#
# ⚠️ 是 6 个不是 4 个。M2 §4.3 写的「社区版只注册 4 个 MCP」是错的 ——
# 那次三处交叉核对看的都是 /opt/opencode-workspace/.opencode/opencode.jsonc，
# 而工作区根下还有一份 /opt/opencode-workspace/opencode.json 又注册了
# hunter_cap 与 screener。opencode 合并后的权威口径是 GET /config：
# ["hunter_cap","hunter_user","portfolio","screener","uzi","watchlist"]。
# 旁证：M2 正式批次里基线侧 q3 真的调到了 screener_market_screen。
# 取证见 docs/evidence/I2/社区版MCP清单-实测更正.md。
set -euo pipefail
PHASE="${1:?baseline|opt}"
LINE="${2:-full}"
REPO=/home/support/hca/repo-i2
cd "$REPO"

export HCA_I2_REPO="$REPO"
export HCA_I2_TRACE=/home/support/hca/i2-trace

case "$PHASE" in
  baseline)
    export HCA_I2_TEMPLATE=/home/support/hca/repo-i2-base/distro/workspace-template
    export HCA_LLM_TOOL_DENY=""              # 上游行为：59 个工具一个不摘
    export ATOMCODE_AI_SESSION_NAMING=1      # 上游行为：每轮结束再发一次模型请求起标题
    export HCA_HOOKD=0                       # 上游行为：每次 hook 起一个 python
    DISABLE_LITE="akshare,kronos,truesource"
    ;;
  opt|opt-fork)
    export HCA_I2_TEMPLATE="$REPO/distro/workspace-template"
    unset HCA_LLM_TOOL_DENY                  # 用 compose 里的默认白名单
    export ATOMCODE_AI_SESSION_NAMING=0
    export HCA_HOOKD=1
    DISABLE_LITE="hcapack,akshare,kronos,truesource"
    ;;
  *) echo "未知阶段 $PHASE" >&2; exit 2 ;;
esac

# fork 阶段多叠一个 compose 覆盖：换镜像 + 那三个只有 fork 认的 config.toml 参数。
# 其余（模板、MCP 清单、hook、shim 白名单）与 opt 完全相同 —— 这样 opt vs opt-fork
# 比的就只是 fork 补丁本身。
FORK_OVERLAY=()
if [ "$PHASE" = "opt-fork" ]; then
  FORK_OVERLAY=(-f deploy/eval/docker-compose.i2-fork.yml)
  if ! docker image inspect "hca-daemon:${HCA_IMAGE_TAG:-i2}-fork" >/dev/null 2>&1; then
    echo "[i2-up] ✗ 镜像 hca-daemon:${HCA_IMAGE_TAG:-i2}-fork 不存在 —— 先跑 bash ~/hca/i2-fork-image.sh" >&2
    exit 3
  fi
fi

if [ "$LINE" = "lite" ]; then
  export HCA_MCP_DISABLE="$DISABLE_LITE"
else
  export HCA_MCP_DISABLE=""
fi

echo "[i2-up] 阶段=$PHASE 线=$LINE 模板=$HCA_I2_TEMPLATE"
echo "[i2-up] TOOL_DENY=${HCA_LLM_TOOL_DENY-（compose 默认）} NAMING=$ATOMCODE_AI_SESSION_NAMING HOOKD=$HCA_HOOKD MCP_DISABLE=${HCA_MCP_DISABLE:-（全挂）}"

docker compose -p hca-i2 \
  -f deploy/docker-compose.yml \
  -f deploy/eval/docker-compose.hca-api.yml \
  -f deploy/eval/docker-compose.i2.yml \
  "${FORK_OVERLAY[@]}" \
  --env-file deploy/.env up -d --wait --force-recreate daemon llm-shim
# ⚠️ llm-shim 也要一起重建：工具白名单（LLM_TOOL_DENY）是**它的**环境变量，
# 两个阶段的值不一样。只重建 daemon 的话基线阶段会带着优化阶段的白名单跑，
# 而这件事在 daemon 侧看不出任何异常 —— 只有 shim 追踪里的 n_tools 会露馅。

TOK=$(docker exec hca-i2-daemon cat /run/hca/daemon-token)
docker exec hca-i2-daemon sh -c "curl -s -H 'Authorization: Bearer $TOK' http://127.0.0.1:13456/mcp/status" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("[i2-up] MCP", sum(1 for s in d["servers"] if s["status"]=="connected"), "/", len(d["servers"]), [s["name"] for s in d["servers"]])'
docker logs hca-i2-daemon 2>&1 | grep -i "hook 常驻\|hookd\|MCP_DISABLE\|去掉" | tail -5 || true
if [ "$PHASE" = "opt-fork" ]; then
  echo "[i2-up] fork 二进制：$(docker exec hca-i2-daemon atomcode --version 2>&1)"
  echo "[i2-up] config.toml 里的 fork 参数："
  docker exec hca-i2-daemon sh -c 'grep -E "system_prompt_file|^\[tools\]|^allow|^deny" /data/atomcode/config.toml' || \
    echo "[i2-up] ✗ config.toml 里一行都没有 —— 参数没渲染进去"
fi
