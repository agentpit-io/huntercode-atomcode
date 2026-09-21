#!/bin/bash
# M0 追测 · 多步工具链截断 与 并行工具调用参数合并 的可复现性
set -u
cd ~/hca/probe
source tok.env
A="Authorization: Bearer $HCA_TOK"
OUT=~/hca/probe/out/diag; mkdir -p "$OUT"
P='请依次做三件事，做完就停：1) 用 read_file 读 probe-read.txt；2) 用 bash 跑 python3 -c "print(1+1)"；3) 用 write_file 把「已验证」写进 rv-write.txt。'

run() { # run <标签> <档> <提示词>
  local tag=$1 mode=$2 prompt=$3
  local f="$OUT/$tag.sse"
  rm -f ws1/rv-write.txt
  curl -sN --max-time 180 -X POST http://127.0.0.1:13456/chat -H "$A" -H "Content-Type: application/json" \
    -d "$(python3 -c "
import json,sys
print(json.dumps({'message':sys.argv[1],'working_dir':'/home/support/hca/probe/ws1','approval_mode':sys.argv[2]},ensure_ascii=False))" "$prompt" "$mode")" > "$f" 2>&1
  python3 - "$f" "$tag" <<'PY'
import json,sys
f,tag=sys.argv[1],sys.argv[2]
calls=[];bad=[];stop=None;rounds=None;warn=[]
for line in open(f):
    if not line.startswith('data: '): continue
    try: e=json.loads(line[6:])
    except: continue
    t=e.get('type')
    if t=='tool_start':
        args=e.get('arguments','')
        calls.append(e['name'])
        try:
            d=json.loads(args)
            # 参数里出现别的工具的字段 = 并行调用被合并
            if e['name']=='read_file' and set(d)-{'file_path','offset','limit'}: bad.append((e['name'],args[:160]))
        except Exception: bad.append((e['name'],'JSON解析失败: '+args[:160]))
    elif t=='warning': warn.append(e.get('message','')[:80])
    elif t=='done':
        stop=e.get('stop_reason'); rounds=e.get('stats',{}).get('rounds')
print(f"[{tag}] 工具={calls} 轮数={rounds} stop={stop} 写文件={'是' if __import__('os').path.exists('/home/support/hca/probe/ws1/rv-write.txt') else '否'}")
for n,a in bad: print(f"    ⚠ 参数异常 {n}: {a}")
for w in warn: print(f"    提示: {w}")
PY
}

echo "### A. build 档三步链 × 3 次"
for i in 1 2 3; do run "build-$i" build "$P"; done
echo
echo "### B. accept_edits 档三步链 × 2 次"
for i in 1 2; do run "ae-$i" accept_edits "$P"; done
echo
echo "### C. bypass 档三步链 × 2 次"
for i in 1 2; do run "bp-$i" bypass "$P"; done
echo
echo "### D. 单步（只读文件）· build，作对照"
run "single" build '用 read_file 读 probe-read.txt，告诉我内容。'
echo
echo "=== 配额 ==="; curl -s -m 20 -H "Authorization: Bearer $(cat ~/hca/secrets/llm-test-key)" https://hunter.agentpit.io/api/saas/llm/quota
