#!/bin/bash
# 用法: live.sh <输出文件> <消息JSON> [等待秒数,默认120]
source ~/hca/probe/tok.env
OUT=$1; BODY=$2; WAIT=${3:-120}
curl -sN --max-time $((WAIT+10)) -H "Authorization: Bearer $HCA_TOK" "http://127.0.0.1:13456/live" > "$OUT" 2>&1 &
P=$!
sleep 4
T0=$(date +%s.%N)
curl -s -X POST -H "Authorization: Bearer $HCA_TOK" -H "Content-Type: application/json" -d "$BODY" http://127.0.0.1:13456/live/message
echo
# 轮询到出现 idle/done 状态或超时
for i in $(seq 1 $WAIT); do
  sleep 1
  grep -q '"type":"state".*"idle"' "$OUT" 2>/dev/null && [ $i -gt 6 ] && break
done
T1=$(date +%s.%N)
kill $P 2>/dev/null
echo "[等待 $(echo "$T1 - $T0" | bc)s]"
