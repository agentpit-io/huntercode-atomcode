#!/bin/bash
# M0 细项 13 · 真实模型（hunter-chat）复测。
# 额度紧张（300k token/日，单轮固定开销 16.3k），所以：
#   · 每项只跑一轮，提示词写死、尽量短
#   · 每项跑完立刻查一次配额，低于阈值就停，把已完成的部分留下来
# 用法: ./reverify_real_model.sh [剩余额度下限,默认 40000]
set -u
cd ~/hca/probe
source tok.env
A="Authorization: Bearer $HCA_TOK"
FLOOR=${1:-40000}
KEY=$(cat ~/hca/secrets/llm-test-key)
OUT=~/hca/probe/out/rv; mkdir -p "$OUT"

quota() { curl -s -m 20 -H "Authorization: Bearer $KEY" https://hunter.agentpit.io/api/saas/llm/quota; }
remain() { quota | python3 -c 'import sys,json;print(json.load(sys.stdin).get("remaining",0))'; }

gate() {  # 够不够跑下一项
  local r; r=$(remain)
  echo "[配额] 剩余 $r token"
  if [ "$r" -lt "$FLOOR" ]; then echo "[配额] 低于下限 $FLOOR，停止后续用例"; return 1; fi
  return 0
}

dump() {  # dump <文件> <标题>
  echo "── $2 ──"
  python3 - "$1" <<'PY'
import json,sys
for line in open(sys.argv[1]):
    if not line.startswith('data: '): continue
    try: e=json.loads(line[6:])
    except: continue
    t=e.get('type')
    if t=='tool_start':   print(f"  发起 {e['name']} {e['arguments'][:100]}")
    elif t=='tool_result':print(f"  结果 {e['name']} ok={e['success']} {e['output'][:200]!r}")
    elif t=='permission_request': print(f"  ⚠ 弹权限 {e['tool_name']} {e.get('arguments','')[:80]}")
    elif t=='text':       print(f"  文本 {e.get('content','')[:120]!r}")
    elif t=='reasoning':  print(f"  推理 {e.get('content','')[:120]!r}")
    elif t=='tokens':     print(f"  tokens {e}")
    elif t=='done':       print(f"  完成 stop_reason={e.get('stop_reason')} tool_calls={e['tool_calls']} stats={e.get('stats')}")
    elif t=='error':      print(f"  错误 {e.get('message','')[:200]}")
    elif t=='warning':    print(f"  提示 {e.get('message','')[:150]}")
PY
}

PROMPT='请依次做三件事，做完就停：1) 用 read_file 读 probe-read.txt；2) 用 bash 跑 python3 -c "print(1+1)"；3) 用 write_file 把「已验证」写进 rv-write.txt。'

echo "=== 起点配额 ==="; quota; echo

# ── 用例 1-4：四档权限（真实模型） ──
for M in plan build accept_edits bypass; do
  gate || break
  rm -f ws1/rv-write.txt
  f="$OUT/perm-$M.sse"
  ( python3 answer_perm.py "$f" deny 150 & echo $! > /tmp/rv_ap ) >/dev/null 2>&1
  curl -sN --max-time 150 -X POST http://127.0.0.1:13456/chat -H "$A" -H "Content-Type: application/json" \
    -d "$(python3 -c "
import json,sys
print(json.dumps({'message':'''$PROMPT''','working_dir':'/home/support/hca/probe/ws1','approval_mode':'$M'},ensure_ascii=False))")" > "$f" 2>&1
  kill "$(cat /tmp/rv_ap)" 2>/dev/null
  dump "$f" "真实模型 · 权限档 $M"
  echo "  rv-write.txt: $([ -f ws1/rv-write.txt ] && echo 已写 || echo 未写)"
  echo
done

# ── 用例 5：/live 上 MCP 端到端（模型自主 search → signature → call） ──
if gate; then
  f="$OUT/live-mcp.sse"
  curl -sN --max-time 200 -H "$A" http://127.0.0.1:13456/live > "$f" 2>&1 &
  LP=$!; sleep 3
  curl -s -X POST -H "$A" -H "Content-Type: application/json" -d '{"message":"用 akshare 相关 MCP 工具查 600519 最近 5 个交易日收盘价。提示：用 stock_zh_a_daily，symbol 写 sh600519（东方财富源在本机不通）。按日期列出，不要写文件。","provider":"hunter"}' http://127.0.0.1:13456/live/message; echo
  sleep 120; kill $LP 2>/dev/null
  dump "$f" "真实模型 · /live MCP 端到端"
  echo
fi

# ── 用例 6：技能斜杠调用 ──
if gate; then
  f="$OUT/skill.sse"
  curl -sN --max-time 120 -X POST http://127.0.0.1:13456/chat -H "$A" -H "Content-Type: application/json" \
    -d '{"message":"/risk_profile 我风险偏保守 · 现金还有 5 万 · 单票别超过 20%","working_dir":"/home/support/hca/probe/ws1","approval_mode":"plan"}' > "$f" 2>&1
  dump "$f" "真实模型 · 技能斜杠调用"
  echo
fi

# ── 用例 7：hook 在真实模型下拦 write_file ──
if gate; then
  python3 - <<'PY'
import json
p='/home/support/hca/probe/ws1/.hooks.json'
d=json.load(open(p))
for k in d['hooks']: d['hooks'][k]['disabled']=False
json.dump(d,open(p,'w'),ensure_ascii=False,indent=2)
PY
  : > logs/hook-input.jsonl
  rm -f ws1/rv-hook.txt
  f="$OUT/hook.sse"
  curl -sN --max-time 120 -X POST http://127.0.0.1:13456/chat -H "$A" -H "Content-Type: application/json" \
    -d '{"message":"用 write_file 把「不该写进来」写进 rv-hook.txt。","working_dir":"/home/support/hca/probe/ws1","approval_mode":"bypass"}' > "$f" 2>&1
  dump "$f" "真实模型 · hook 拦截（bypass 档）"
  echo "  rv-hook.txt: $([ -f ws1/rv-hook.txt ] && echo 已写 || echo 未写)"
  echo "  hook 收到的输入:"; cat logs/hook-input.jsonl
  python3 - <<'PY'
import json
p='/home/support/hca/probe/ws1/.hooks.json'
d=json.load(open(p))
for k in d['hooks']: d['hooks'][k]['disabled']=True
json.dump(d,open(p,'w'),ensure_ascii=False,indent=2)
PY
  echo
fi

# ── 用例 8：双会话并发 ──
if gate; then
  for W in ws1 ws2; do
    curl -sN --max-time 120 -X POST http://127.0.0.1:13456/chat -H "$A" -H "Content-Type: application/json" \
      -d "{\"message\":\"用 read_file 读 probe-read.txt，把里面的专属标记原样告诉我，不要做别的。\",\"working_dir\":\"/home/support/hca/probe/$W\",\"approval_mode\":\"plan\"}" > "$OUT/ms-$W.sse" 2>&1 &
  done
  wait
  dump "$OUT/ms-ws1.sse" "真实模型 · 并发会话 ws1"
  dump "$OUT/ms-ws2.sse" "真实模型 · 并发会话 ws2"
  echo
fi

echo "=== 终点配额 ==="; quota; echo
