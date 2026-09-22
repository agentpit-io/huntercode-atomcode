#!/usr/bin/env bash
# I2 · 「一轮模型到底差多少」的对照实验（不计分）。
#
#     bash deploy/eval/i2-ttft-probe.sh            # 三臂各 5 次
#
# 要回答的问题很具体：q4 两边都是 **1 轮、0 次工具调用**，而 HCA 7.9 s、社区版 4.7 s
# （`opt2-fork-b`）。§1.10a / §1.10b 已经把出字（2.3 ms/字）与 daemon 那一层（60 ms）
# 量掉了，剩下的差只能落在**上游那一次请求**上。可是两边打的是同一个网关、同一个
# 模型名，唯一的差别是**请求本身**（HCA 的系统提示大、工具多）。
#
# 所以用一个**两边完全一样、答案极短、不需要任何工具**的问题去打：
#
#   A 臂  HCA 现状（fork + 10 个 MCP + 投研人设）
#   B 臂  社区版（同一个网关）
#   C 臂  HCA 但把工具全关掉、系统提示换成一行 —— 看 A 与 C 的差，
#         就是「请求大」值多少墙钟；看 C 与 B 的差，就是剩下的引擎差
#
# C 臂靠 fork 的两个参数实现（`[tools] deny` + `system_prompt_file`），
# 这正是那个补丁能做到、官方二进制做不到的事 —— 顺手也是补丁的一次真实用法。
#
# ⚠️ 这个脚本会**重启 hca-i2-daemon 两次**（C 臂换配置、跑完换回来），
# 所以只能在两个评测批次之间跑。开跑前自己查一遍有没有 run_ab.py 在跑。
set -uo pipefail
REPO=/home/support/hca/repo-i2
C=hca-i2-daemon
OUT=/home/support/hca/i2-logs/ttft-probe
N="${N:-5}"
Q='只回四个字「收到，好的」，不要调任何工具，不要加任何别的内容、标题、表格或免责声明。'

# 判据要能认出**真的批次进程**，不能被别的命令行里出现的 "run_ab.py" 这几个字骗到
# —— 待办池 R-19 就是这个形状（等待壳自己的命令行里有 cargo，于是永远等不到安静）。
# 这一条第一次跑就中招：一个 `while pgrep -f run_ab.py` 的等待壳让探针直接拒绝开跑。
if pgrep -f "tools/eval/run_ab.py" >/dev/null 2>&1; then
  echo "✗ 有 run_ab.py 在跑，拒绝开跑（两个批次一起跑，两边的计时都不能用）" >&2
  exit 3
fi
mkdir -p "$OUT"
say(){ printf '[ttft %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

hca_arm() {   # $1 = 标签
  local tag="$1" i
  for i in $(seq "$N"); do
    docker exec "$C" python3 /opt/hca/tools/eval_atomcode.py \
      --id "ttft-${tag}-r${i}" --message "$Q" \
      --out /workspace/.eval --timeout 180 --permission deny >/dev/null 2>&1
    docker cp "$C:/workspace/.eval/ttft-${tag}-r${i}.json" "$OUT/" >/dev/null 2>&1
    sleep 3
  done
}

oc_arm() {
  local i
  cd "$REPO"
  for i in $(seq "$N"); do
    python3 tools/eval/eval_opencode.py --id "ttft-oc-r${i}" --message "$Q" \
      --account /home/support/hca/secrets/eval-account.json \
      --out "$OUT" --timeout 180 >/dev/null 2>&1
    sleep 3
  done
}

report() {    # $1..$n = 标签
  python3 - "$OUT" "$@" <<'PY'
import glob, json, statistics as st, sys
out, tags = sys.argv[1], sys.argv[2:]
print("| 臂 | n | 墙钟中位 | 模型段中位 | 出字/收尾中位 | 正文字数中位 | 提示 token |")
print("|---|---|---|---|---|---|---|")
for tag in tags:
    rows = []
    for f in sorted(glob.glob(f"{out}/ttft-{tag}-r*.json")):
        if f.endswith(".raw.json"):
            continue
        d = json.load(open(f))
        t = (d.get("waterfall") or {}).get("totals") or {}
        rows.append((d.get("wall_ms"), t.get("model_ms"), t.get("tail_ms"),
                     d.get("text_len"), d.get("prompt_tokens")))
    if not rows:
        print(f"| {tag} | 0 | — | — | — | — | — |")
        continue
    def m(i):
        xs = [r[i] for r in rows if isinstance(r[i], (int, float))]
        return round(st.median(xs)) if xs else None
    print(f"| {tag} | {len(rows)} | {m(0)} ms | {m(1)} | {m(2)} | {m(3)} | {m(4)} |")
    print(f"|   逐次墙钟 | | {[r[0] for r in rows]} | | | | |")
PY
}

say "A 臂：HCA 现状"
hca_arm a

say "B 臂：社区版（同一个网关）"
oc_arm

say "C 臂：HCA 把工具全关、系统提示换成一行"
# 一行人设要写进**挂载进容器的那个目录**（$REPO/distro/personas → /opt/hca/personas-src）。
# 不进仓库：它是探针夹具，跑完就删。
MIN="$REPO/distro/personas/ttft-minimal.md"
printf '你是一个中文助手。按用户说的做，不要多写。\n' > "$MIN"
cd "$REPO"
export HCA_TOOLS_DENY='group:coding,group:codeintel,group:atomgit,group:subagent,group:skills,group:mcp'
export HCA_PERSONA_FILE=/opt/hca/personas-src/ttft-minimal.md
bash ~/hca/i2-up.sh opt-fork full >/dev/null 2>&1 || say "✗ C 臂起栈失败"
say "C 臂的人设（hca-init 渲染到 /data/atomcode/persona.md）字符数：$(docker exec $C sh -c 'wc -c < /data/atomcode/persona.md' 2>/dev/null || echo '?')；工具数与系统提示字符另看 shim 追踪"
hca_arm c

say "还原到 A 臂的配置"
unset HCA_TOOLS_DENY HCA_PERSONA_FILE
rm -f "$MIN"
bash ~/hca/i2-up.sh opt-fork full >/dev/null 2>&1 || say "⚠ 还原起栈失败，后面的批次不要跑"

say "结果（原始记录在 $OUT）"
report a oc c
