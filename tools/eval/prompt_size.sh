#!/usr/bin/env bash
# 实测「关掉那几个编码向开关」到底省下多少上下文。
#
#     bash tools/eval/prompt_size.sh <输出目录>
#
# 做法：同一个 daemon、同一句**不需要任何工具**的最短提问，只改环境变量，
# 各跑一次，记两个数：
#
#   · `/live` 的 `state.stats.prompt_tokens` —— 拿得到就是最准的
#     （待办池 P1-12：多数轮次是 0，所以不能只靠它）
#   · 网关配额差值 `used_today` 前后差 —— 这是真实计量，任何时候都拿得到；
#     因为回答被限定成一两个字，差值基本就是 prompt 的大小
#
# 两个数都写进证据文件，报告里如实说明各自的口径。
set -euo pipefail

OUT="${1:?用法: prompt_size.sh <输出目录>}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mkdir -p "$OUT"

MSG='请只回答两个字：收到。不要调用任何工具。'

run_case() {
  local name="$1"; shift
  echo "[prompt-size] ── ${name} ──"
  # 用环境变量覆盖 compose 里的默认值再重建 daemon
  env "$@" docker compose -p hca \
      -f "${REPO}/deploy/docker-compose.yml" \
      -f "${REPO}/deploy/eval/docker-compose.hca-api.yml" \
      --env-file "${REPO}/deploy/.env" up -d daemon >/dev/null 2>&1
  for _ in $(seq 1 60); do
    [ "$(docker inspect -f '{{.State.Health.Status}}' hca-daemon 2>/dev/null || echo none)" = healthy ] && break
    sleep 5
  done
  echo "[prompt-size]   生效的开关："
  docker exec hca-daemon env | grep -E '^ATOMCODE_(TODO|SUBAGENT|MEMORY_TOOL|REQUEST_USER_INPUT)=' | sort | sed 's/^/[prompt-size]     /'
  docker exec hca-daemon python3 /opt/hca/tools/eval_atomcode.py \
      --id "promptsize-${name}" --message "$MSG" --out /workspace/.eval --timeout 180
  docker cp "hca-daemon:/workspace/.eval/promptsize-${name}.json" "${OUT}/promptsize-${name}.json"
  docker cp "hca-daemon:/workspace/.eval/promptsize-${name}.sse"  "${OUT}/promptsize-${name}.sse" 2>/dev/null || true
}

# 上游默认：四个开关全开
run_case default \
  ATOMCODE_TODO=1 ATOMCODE_SUBAGENT=1 ATOMCODE_MEMORY_TOOL=1 ATOMCODE_REQUEST_USER_INPUT=1
# 本发行版：四个全关
run_case hca \
  ATOMCODE_TODO=0 ATOMCODE_SUBAGENT=0 ATOMCODE_MEMORY_TOOL=0 ATOMCODE_REQUEST_USER_INPUT=0

# 恢复到发行版设定（compose 的默认值就是全关，重建一次即可）
docker compose -p hca -f "${REPO}/deploy/docker-compose.yml" \
    -f "${REPO}/deploy/eval/docker-compose.hca-api.yml" \
    --env-file "${REPO}/deploy/.env" up -d daemon >/dev/null 2>&1

python3 - "$OUT" <<'PY'
import json, sys, pathlib
out = pathlib.Path(sys.argv[1])
rows = []
for name in ("default", "hca"):
    f = out / f"promptsize-{name}.json"
    d = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    rows.append((name, d.get("prompt_tokens"), d.get("quota_delta"),
                 d.get("rounds"), d.get("text_len")))
print(f"\n{'组合':10s} {'stats.prompt_tokens':>20s} {'配额差值':>10s} {'轮数':>5s} {'正文字数':>8s}")
for n, p, q, r, t in rows:
    print(f"{n:10s} {str(p):>20s} {str(q):>10s} {str(r):>5s} {str(t):>8s}")
(out / "summary.json").write_text(json.dumps(
    [{"case": n, "prompt_tokens": p, "quota_delta": q, "rounds": r, "text_len": t}
     for n, p, q, r, t in rows], ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n已写 {out/'summary.json'}")
PY
