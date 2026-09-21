#!/bin/bash
# M0 追测 · hunter-deep(Gemini 3.1 Pro) 的多步工具链可靠性 + reasoning 事件
set -u
cd ~/hca/probe
source tok.env
A="Authorization: Bearer $HCA_TOK"
OUT=~/hca/probe/out/diag; mkdir -p "$OUT"
KEY=$(cat ~/hca/secrets/llm-test-key)

echo "=== 1. 注册 hunter-deep provider ==="
curl -s -X POST http://127.0.0.1:13456/providers -H "$A" -H "Content-Type: application/json" \
  -d "$(python3 -c "
import json,sys
print(json.dumps({'name':'hunterdeep','type':'openai','api_key':sys.argv[1],
 'model':'hunter-deep','base_url':'https://hunter.agentpit.io/api/saas/llm/v1','context_window':1000000}))" "$KEY")"
echo
echo "=== 2. 开 thinking（试 PATCH /providers/hunterdeep/thinking）==="
curl -s -X PATCH http://127.0.0.1:13456/providers/hunterdeep/thinking -H "$A" -H "Content-Type: application/json" \
  -d '{"enabled":true,"reasoning_effort":"high"}'
echo
curl -s -H "$A" http://127.0.0.1:13456/models
echo

P='请依次做三件事，做完就停：1) 用 read_file 读 probe-read.txt；2) 用 bash 跑 python3 -c "print(1+1)"；3) 用 write_file 把「已验证」写进 rv-write.txt。'

run() {
  local tag=$1 prov=$2
  local f="$OUT/$tag.sse"
  rm -f ws1/rv-write.txt
  curl -sN --max-time 240 -X POST http://127.0.0.1:13456/chat -H "$A" -H "Content-Type: application/json" \
    -d "$(python3 -c "
import json,sys
print(json.dumps({'message':sys.argv[1],'working_dir':'/home/support/hca/probe/ws1','approval_mode':'build','provider':sys.argv[2]},ensure_ascii=False))" "$P" "$prov")" > "$f" 2>&1
  python3 - "$f" "$tag" <<'PY'
import json,sys,os
f,tag=sys.argv[1],sys.argv[2]
calls=[];stop=None;rounds=None;warn=[];reasoning=0;err=[]
for line in open(f):
    if not line.startswith('data: '): continue
    try: e=json.loads(line[6:])
    except: continue
    t=e.get('type')
    if t=='tool_start': calls.append(e['name'])
    elif t=='reasoning': reasoning+=1
    elif t=='warning': warn.append(e.get('message','')[:70])
    elif t=='error': err.append(e.get('message','')[:150])
    elif t=='done': stop=e.get('stop_reason'); rounds=e.get('stats',{}).get('rounds')
print(f"[{tag}] 工具={calls} 轮数={rounds} stop={stop} reasoning事件={reasoning} 写文件={'是' if os.path.exists('/home/support/hca/probe/ws1/rv-write.txt') else '否'}")
for w in warn: print(f"    提示: {w}")
for e in err: print(f"    错误: {e}")
PY
}

echo "=== 3. hunter-deep 三步链 × 3 ==="
for i in 1 2 3; do run "deep-$i" hunterdeep; done
echo
echo "=== 4. hunter-chat 再补 3 次（凑样本）==="
for i in 4 5 6; do run "chat-$i" hunter; done
echo
echo "=== 配额 ==="; curl -s -m 20 -H "Authorization: Bearer $KEY" https://hunter.agentpit.io/api/saas/llm/quota
