#!/bin/bash
source ~/hca/probe/tok.env; A="Authorization: Bearer $HCA_TOK"
MODE=$1; OUT=~/hca/probe/out/perm3-$MODE.sse
rm -rf /home/support/hca/outside ~/hca/probe/ws1/.env "$OUT"; mkdir -p /home/support/hca/outside
curl -sN --max-time 150 -X POST http://127.0.0.1:13456/chat -H "$A" -H "Content-Type: application/json" \
  -d "{\"message\":\"执行越界脚本\",\"provider\":\"stub\",\"working_dir\":\"/home/support/hca/probe/ws1\",\"approval_mode\":\"$MODE\"}" > "$OUT" 2>&1 &
CP=$!; python3 ~/hca/probe/answer_perm.py "$OUT" deny 150 &
AP=$!
wait $CP 2>/dev/null
kill $AP 2>/dev/null
echo "── 越界/敏感 · 模式 $MODE ──"
python3 - "$OUT" <<'PY'
import json,sys
for line in open(sys.argv[1]):
    if not line.startswith('data: '): continue
    e=json.loads(line[6:]); t=e['type']
    if t=='tool_start': print(f"  发起 {e['name']} {e['arguments'][:80]}")
    elif t=='tool_result': print(f"  结果 {e['name']} ok={e['success']} {e['output'][:130]!r}")
    elif t=='permission_request': print(f"  ⚠ 弹权限 {e['tool_name']} args={e['arguments'][:70]}")
    elif t=='done': print(f"  完成 stop_reason={e.get('stop_reason')}")
PY
echo "  escape.txt:$([ -f /home/support/hca/outside/escape.txt ] && echo 已写 || echo 未写) .env:$([ -f ~/hca/probe/ws1/.env ] && echo 已写 || echo 未写) bash-escape:$([ -f /home/support/hca/outside/bash-escape.txt ] && echo 已写 || echo 未写)"
