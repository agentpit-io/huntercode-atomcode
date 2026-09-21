#!/bin/bash
# M0 追测 · A/B:AtomCode 直连网关 vs 经 llm-shim(补 tool_call index)
# 指标:三步工具链是否完整跑完、是否出现脏参数(别的工具的字段混进来)、是否死循环/报错
set -u
cd ~/hca/probe
source tok.env
A="Authorization: Bearer $HCA_TOK"
OUT=~/hca/probe/out/ab; mkdir -p "$OUT"
KEY=$(cat ~/hca/secrets/llm-test-key)
N=${1:-8}

echo "=== 注册经 shim 的 provider（hunterviashim）==="
curl -s -X POST http://127.0.0.1:13456/providers -H "$A" -H "Content-Type: application/json" \
  -d "$(python3 -c "
import json,sys
print(json.dumps({'name':'hunterviashim','type':'openai','api_key':sys.argv[1],'model':'hunter-chat',
 'base_url':'http://127.0.0.1:13999/v1','context_window':1000000}))" "$KEY")" | head -c 200
echo; echo

P='请依次做三件事，做完就停：1) 用 read_file 读 probe-read.txt；2) 用 bash 跑 python3 -c "print(1+1)"；3) 用 write_file 把「已验证」写进 rv-write.txt。'

run() {
  local tag=$1 prov=$2
  local f="$OUT/$tag.sse"
  rm -f ws1/rv-write.txt
  curl -sN --max-time 240 -X POST http://127.0.0.1:13456/chat -H "$A" -H "Content-Type: application/json" \
    -d "$(python3 -c "
import json,sys
print(json.dumps({'message':sys.argv[1],'working_dir':'/home/support/hca/probe/ws1','approval_mode':'build','provider':sys.argv[2]},ensure_ascii=False))" "$P" "$prov")" > "$f" 2>&1
}

python3 - "$OUT" "$N" <<'PY' &
PY
wait 2>/dev/null

for i in $(seq 1 $N); do run "direct-$i" hunter;      echo "  直连 $i 跑完"; done
for i in $(seq 1 $N); do run "shim-$i"   hunterviashim; echo "  经shim $i 跑完"; done

echo
python3 - "$OUT" "$N" <<'PY'
import json,os,sys
OUT,N=sys.argv[1],int(sys.argv[2])
# 每个工具「合法」的参数字段;出现别的字段就是并行调用被合并的脏参数
LEGAL={"read_file":{"file_path","offset","limit"},
       "bash":{"command","timeout_ms","description","cwd","timeout"},
       "write_file":{"file_path","content"}}
def analyze(f):
    calls=[];dirty=[];stop=None;rounds=0;loop=False
    if not os.path.exists(f): return None
    for line in open(f):
        if not line.startswith("data: "): continue
        try: e=json.loads(line[6:])
        except: continue
        t=e.get("type")
        if t=="tool_start":
            nm=e["name"]; calls.append(nm)
            try:
                d=json.loads(e.get("arguments") or "{}")
                extra=set(d)-LEGAL.get(nm,set(d))
                if extra: dirty.append((nm,sorted(extra)))
            except Exception: dirty.append((nm,["JSON解析失败"]))
        elif t=="warning" and "loop" in (e.get("message") or "").lower(): loop=True
        elif t=="done":
            stop=e.get("stop_reason"); rounds=e.get("stats",{}).get("rounds") or 0
    full = calls[:3]==["read_file","bash","write_file"]
    return dict(calls=calls,dirty=dirty,stop=stop,rounds=rounds,loop=loop,full=full)

for grp,label in (("direct","直连网关"),("shim","经 llm-shim")):
    rows=[analyze("%s/%s-%d.sse"%(OUT,grp,i)) for i in range(1,N+1)]
    rows=[r for r in rows if r]
    okn=sum(1 for r in rows if r["full"])
    dn =sum(1 for r in rows if r["dirty"])
    en =sum(1 for r in rows if r["stop"] in ("provider_error","tool_loop_detected"))
    print("【%s】样本 %d" % (label,len(rows)))
    print("  三步链完整跑完: %d/%d" % (okn,len(rows)))
    print("  出现脏参数(别的工具字段混入): %d/%d" % (dn,len(rows)))
    print("  provider_error / tool_loop: %d/%d" % (en,len(rows)))
    for i,r in enumerate(rows,1):
        d = ("  脏参数="+str(r["dirty"])) if r["dirty"] else ""
        print("    #%d 工具=%s 轮=%s stop=%s%s" % (i,r["calls"],r["rounds"],r["stop"],d))
    print()
PY
echo "=== 配额 ==="; curl -s -m 20 -H "Authorization: Bearer $KEY" https://hunter.agentpit.io/api/saas/llm/quota
