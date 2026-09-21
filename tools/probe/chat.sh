#!/bin/bash
# 用法: chat.sh <输出文件> <JSON 请求体> [超时秒,默认180]
source ~/hca/probe/tok.env
OUT=$1; BODY=$2; TMO=${3:-180}
START=$(date +%s.%N)
curl -sN --max-time "$TMO" -X POST http://127.0.0.1:13456/chat \
  -H "Authorization: Bearer $HCA_TOK" -H "Content-Type: application/json" \
  -d "$BODY" > "$OUT" 2>&1
RC=$?
END=$(date +%s.%N)
echo "[curl rc=$RC · 耗时 $(echo "$END - $START" | bc)s]"
