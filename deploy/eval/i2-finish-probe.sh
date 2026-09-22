#!/usr/bin/env bash
# I2 · 「收尾」那一段到底是什么（§1.9 量到的约 1.7 秒）。
#
#     bash deploy/eval/i2-finish-probe.sh            # 现状 + 去掉 Stop hook 各 5 次
#
# 背景：把墙钟拆成「模型 / 工具 / 出字 / 收尾」之后，**收尾**是唯一一块
# 与正文长度无关、又确定落在 HCA 这一侧墙钟里的固定开销（q4 上约 1.7 s）。
# 追平社区版差的正是这个量级，所以必须知道它是什么才知道能不能动。
#
# 手上能关的只有一项：`Stop` / `StopFailure` 上挂的 hca-budget（默认不记账，
# 但事件照样触发）。关掉前后各打 5 次同一个**不需要工具、正文极短**的问题
# （这样出字那一块小到可以忽略，`finish_ms` 就是收尾本身）。
# 关掉之后仍然剩下的那部分，只能是引擎侧的收尾路径 —— 那就是 fork 的候选，
# 不是我们在四个外部面里能动的东西。
set -uo pipefail
REPO=/home/support/hca/repo-i2
C=hca-i2-daemon
OUT=/home/support/hca/i2-logs/finish-probe
N="${N:-5}"
Q='只回四个字「收到，好的」，不要调任何工具，不要加任何别的内容、标题、表格或免责声明。'

mkdir -p "$OUT"
say(){ printf '[finish %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

run_phase() {   # $1 = 标签
  local tag="$1" i
  for i in $(seq "$N"); do
    docker exec "$C" python3 /opt/hca/tools/eval_atomcode.py \
      --id "finish-${tag}-r${i}" --message "$Q" \
      --out /workspace/.eval --timeout 180 --permission deny >/dev/null 2>&1
    docker cp "$C:/workspace/.eval/finish-${tag}-r${i}.json" "$OUT/" >/dev/null 2>&1
    sleep 3
  done
  python3 - "$OUT" "$tag" <<'PY'
import glob, json, statistics as st, sys
out, tag = sys.argv[1], sys.argv[2]
rows = []
for f in sorted(glob.glob(f"{out}/finish-{tag}-r*.json")):
    d = json.load(open(f))
    t = (d.get("waterfall") or {}).get("totals") or {}
    rows.append((d.get("wall_ms"), t.get("model_ms"), t.get("stream_ms"),
                 t.get("finish_ms"), d.get("text_len"), d.get("tool_calls_stat")))
if not rows:
    print(f"  {tag}: 一条记录都没拿到"); raise SystemExit
def m(i):
    xs = [r[i] for r in rows if isinstance(r[i], (int, float))]
    return round(st.median(xs)) if xs else None
print(f"  {tag}: n={len(rows)} 墙钟中位 {m(0)} ms | 模型 {m(1)} | 出字 {m(2)} "
      f"| **收尾 {m(3)}** | 字数 {m(4)} | 调用 {m(5)}")
print(f"        逐次收尾：{[r[3] for r in rows]}")
PY
}

say "A 相：现状（Stop 上挂着 hca-budget）"
run_phase now

say "B 相：把 Stop / StopFailure 两条 hook 摘掉，重启 daemon"
docker exec "$C" python3 - <<'PY'
import json
p = "/workspace/.hooks.json"
d = json.load(open(p))
json.dump(d, open(p + ".bak", "w"), ensure_ascii=False, indent=2)
for k in ("hca-budget-stop", "hca-budget-stopfail"):
    d["hooks"].pop(k, None)
json.dump(d, open(p, "w"), ensure_ascii=False, indent=2)
print("[finish] 摘掉后剩下的 hook：", sorted(d["hooks"]))
PY
docker restart "$C" >/dev/null
for _ in $(seq 60); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$C" 2>/dev/null)" = healthy ] && break
  sleep 2
done
docker exec "$C" sh -c 'atomcode hooks list 2>/dev/null | head -20' || true
run_phase nostop

say "还原 .hooks.json 并重启"
docker exec "$C" sh -c 'cp /workspace/.hooks.json.bak /workspace/.hooks.json'
docker restart "$C" >/dev/null
for _ in $(seq 60); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$C" 2>/dev/null)" = healthy ] && break
  sleep 2
done
say "完成；原始记录在 $OUT"
