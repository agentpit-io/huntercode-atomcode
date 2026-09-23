#!/usr/bin/env bash
# I3 · U-20 分档实测：**工具 schema 体积**到底值多少毫秒？（不计分，单独出报告）
#
#     bash deploy/eval/i3-schema-probe.sh          # 五臂 × 2 轮 × 5 次
#     N=8 CYCLES=3 bash deploy/eval/i3-schema-probe.sh
#
# ── 要回答的问题 ────────────────────────────────────────────────────────────
# I2 §4.5 的 C 臂把**提示正文**和 **45 个工具 schema** 一起摘了，请求从 18 911
# token 砍到 6 389 token、墙钟少 685 ms。所以那一格只能说「请求小了 685 ms」，
# **不能说是 schema 的功劳** —— 待办池 U-20 就是这条。
# 而 §2.7b 直接打网关、只改正文、没有工具，量到的是「1.35 万 → 2.7 万 token
# 首字延迟不变」。两次合起来的读法是「吃时间的是 schema」，但没有单独证过。
#
# 这个探针把两个变量拆开，按 2 × N 的设计打：
#
#   臂        人设        工具挂载                         预期工具数
#   t-full    投研人设    上游行为（不 deny、shim 不摘）    ~59
#   t-rel     投研人设    发行版默认（fork deny + shim 白名单）  ~45
#   t-min     投研人设    HCA_TOOLS_ALLOW 只留本场景要的     ~5
#   t-zero    投研人设    全部工具族 deny                    0
#   p-min     一行人设    发行版默认                         ~45
#
#   · t-full / t-rel / t-min / t-zero 四点**只动 schema**，人设一个字没变
#     → 这四点连起来就是「schema 体积 → 模型段耗时」的响应曲线；
#   · t-rel vs p-min **只动正文**，工具清单一模一样
#     → 这一格单独量「提示正文」值多少；
#   · t-zero vs p-min 的差再减一次，能和 I2 §4.5 的 C 臂对上（C 臂 = 两个都动）。
#
# ── 为什么用这道题 ──────────────────────────────────────────────────────────
# 与 i2-ttft-probe.sh 同一道：答案只有几个字、不需要任何工具，所以**一次运行
# 正好一次上游请求、零次工具调用**，模型段没有第二轮来稀释。
#
# ── 计时取哪一个 ────────────────────────────────────────────────────────────
# 两个都取，互为旁证：
#   · `wall_ms` / `waterfall.totals.model_ms` —— 评测脚本在 daemon 外面看到的；
#   · llm-shim 追踪里的 `ttfb_ms` / `total_ms` —— **上游那一跳**自己的耗时，
#     同一行里还记着这次请求的 `n_tools` / `tools_bytes` / `system_chars`。
#     自变量与因变量在同一行里，这是这个探针比 §4.5 那次硬的地方。
#
# ── 怎么防「时间漂移冒充效应」 ───────────────────────────────────────────────
# 五臂各跑 CYCLES 轮、轮与轮之间把顺序整个再走一遍（不是把一臂打完再换）。
# 每臂每轮先打 2 次**热身**不计入（换配置后第一次请求在网关那边多半没有前缀缓存）。
#
# **换臂之后必须先静置 `SETTLE` 秒**（第一次跑这个探针实测出来的教训）：换一臂要
# `--force-recreate` daemon，而 daemon 一起来就**并发冷启 10 个 MCP server**，
# 评测机的 1 分钟负载被顶到 6～7（8 核）。那一段 CPU 抢占正好压在头几次运行上 ——
# 量出来的就不是「请求大不大」而是「这台机器刚才忙不忙」。静置到负载回落再打。
#
# ⚠️ 这个脚本会反复重启 hca-i3-daemon，只能在两个评测批次之间跑。
set -uo pipefail
REPO="${HCA_EVAL_REPO:-/home/support/hca/repo-i3}"
PROJECT="${HCA_EVAL_PROJECT:-hca-i3}"
C="${PROJECT}-daemon"
TRACE="${HCA_EVAL_TRACE:-/home/support/hca/i3-trace}"
OUT="${OUT:-${REPO}/docs/eval/i3/schema-probe}"
N="${N:-5}"            # 每臂每轮计入的次数
WARM="${WARM:-2}"      # 每臂每轮热身次数（不计入）
CYCLES="${CYCLES:-2}"
CYC0="${CYC0:-1}"       # 起始轮号；补跑时给 CYC0=3 就不会覆盖前两轮的文件
SETTLE="${SETTLE:-90}"   # 换臂后静置秒数；负载回到阈值以下就提前结束
SETTLE_LOAD="${SETTLE_LOAD:-1.5}"
Q='只回四个字「收到，好的」，不要调任何工具，不要加任何别的内容、标题、表格或免责声明。'

# 判据要认**真的批次进程**，不能被别的命令行里出现的 run_ab.py 骗到（I2 踩过）。
if pgrep -f "tools/eval/run_ab.py" >/dev/null 2>&1; then
  echo "✗ 有 run_ab.py 在跑，拒绝开跑（两件事一起跑，两边的计时都不能用）" >&2
  exit 3
fi
mkdir -p "$OUT" "$TRACE"
say(){ printf '[schema %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

# 一行人设只给 p-min 用。不进仓库：它是探针夹具，跑完就删。
MIN_PERSONA="$REPO/distro/personas/i3-min.md"

# t-min 的白名单：本场景真正要的那几个（三个组合工具 + 选股 + 读工作区文件）。
# 这正是任务书说的「只留本题需要的 3–5 个工具」。
ALLOW_MIN='mcp__hcapack__*,mcp__screener__market_screen,read_file'

arm_env() {   # $1 = 臂名；只导出这一臂要改的东西，其余 unset
  # ⚠️ 两个坑，都是第一次写这个探针时踩到的：
  #   · i2-up.sh 在 opt/opt-fork 阶段会 `unset HCA_LLM_TOOL_DENY`（用 compose 默认
  #     白名单）—— 要让 shim 那层也不摘工具，得同时给 HCA_KEEP_SHIM_DENY=1；
  #   · fork 覆盖里那行原本写的是 `${HCA_TOOLS_DENY:-默认}`，**空值会掉回默认清单**，
  #     于是 t-full 会悄悄变成 t-rel、两臂量出同一个数。已改成 `${VAR-默认}`。
  unset HCA_TOOLS_ALLOW HCA_TOOLS_DENY HCA_LLM_TOOL_DENY HCA_PERSONA_FILE HCA_KEEP_SHIM_DENY
  case "$1" in
    t-full)
      export HCA_TOOLS_DENY=""          # fork 侧不摘
      export HCA_LLM_TOOL_DENY="" HCA_KEEP_SHIM_DENY=1   # shim 侧也不摘 → 上游本来的 59 个
      ;;
    t-rel)  : ;;                        # 发行版默认：两层各自用自己的默认值
    t-min)
      export HCA_TOOLS_ALLOW="$ALLOW_MIN"
      # 已经在 daemon 侧挂不出来了，shim 不用再摘（摘不摘都一样，但要让 n_tools 只由
      # 白名单决定，免得「到底是哪一层摘的」说不清）
      export HCA_LLM_TOOL_DENY="" HCA_KEEP_SHIM_DENY=1
      ;;
    t-zero)
      export HCA_TOOLS_DENY='group:coding,group:codeintel,group:atomgit,group:subagent,group:skills,group:mcp'
      ;;
    p-min)
      export HCA_PERSONA_FILE=/opt/hca/personas-src/i3-min.md
      ;;
    *) echo "未知臂 $1" >&2; return 2 ;;
  esac
}

run_arm() {   # $1 = 臂名  $2 = 轮号
  local arm="$1" cyc="$2" i
  arm_env "$arm" || return 2
  ( cd "$REPO" && bash deploy/eval/i3-up.sh opt-fork full ) >"$OUT/up-${arm}-c${cyc}.log" 2>&1 \
    || { say "✗ 臂 $arm 起栈失败，看 $OUT/up-${arm}-c${cyc}.log"; return 1; }

  # 静置：等 10 个 MCP server 冷启动那一波 CPU 过去，否则量的是机器忙不忙。
  local waited=0
  while [ "$waited" -lt "$SETTLE" ]; do
    local l1; l1=$(cut -d' ' -f1 /proc/loadavg)
    awk -v l="$l1" -v t="$SETTLE_LOAD" 'BEGIN{exit !(l<=t)}' && break
    sleep 10; waited=$((waited + 10))
  done
  say "臂 ${arm} 轮 ${cyc} 静置 ${waited}s，开打时负载 $(cut -d' ' -f1-3 /proc/loadavg)"

  # 追踪文件按臂切开：自变量（n_tools / tools_bytes）与因变量（ttfb_ms）同在一行，
  # 但要能把「这一臂的那几行」摘出来，所以每臂之前先把旧的挪走。
  rm -f "$TRACE/requests.jsonl"

  for i in $(seq $((WARM + N))); do
    local id="u20-${arm}-c${cyc}-r${i}"
    docker exec "$C" python3 /opt/hca/tools/eval_atomcode.py \
      --id "$id" --message "$Q" --out /workspace/.eval --timeout 180 \
      --permission deny >/dev/null 2>&1
    docker cp "$C:/workspace/.eval/${id}.json" "$OUT/" >/dev/null 2>&1
    sleep 3
  done
  cp -f "$TRACE/requests.jsonl" "$OUT/trace-${arm}-c${cyc}.jsonl" 2>/dev/null || true
  say "臂 ${arm} 轮 ${cyc} 完成：$(python3 - "$OUT/trace-${arm}-c${cyc}.jsonl" <<'PY'
import json,sys
try: rows=[json.loads(l) for l in open(sys.argv[1]) if l.strip()]
except Exception: rows=[]
if rows: print(f"{len(rows)} 次请求 · n_tools={rows[-1]['n_tools']} · tools_bytes={rows[-1]['tools_bytes']} · system_chars={rows[-1]['system_chars']}")
else: print("追踪为空")
PY
)"
}

printf '你是一个中文助手。按用户说的做，不要多写。\n' > "$MIN_PERSONA"
ARMS=(t-full t-rel t-min t-zero p-min)
for cyc in $(seq "$CYC0" $((CYC0 + CYCLES - 1))); do
  for arm in "${ARMS[@]}"; do
    say "=== 轮 ${cyc} · 臂 ${arm}（热身 ${WARM} + 计入 ${N}）负载 $(cut -d' ' -f1-3 /proc/loadavg) ==="
    run_arm "$arm" "$cyc" || say "⚠ 臂 ${arm} 轮 ${cyc} 出错，继续下一臂（手里有多少数据算多少）"
  done
done

say "还原到发行版配置"
unset HCA_TOOLS_ALLOW HCA_TOOLS_DENY HCA_LLM_TOOL_DENY HCA_PERSONA_FILE
rm -f "$MIN_PERSONA"
( cd "$REPO" && bash deploy/eval/i3-up.sh opt-fork full ) >"$OUT/up-restore.log" 2>&1 \
  || say "⚠ 还原起栈失败 —— 后面的批次不要跑，先看 $OUT/up-restore.log"

say "出表（原始记录在 $OUT）"
python3 "$REPO/tools/eval/i3_schema_report.py" --dir "$OUT" --warm "$WARM" \
  | tee "$OUT/报告.md"
