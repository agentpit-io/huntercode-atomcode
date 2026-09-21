#!/usr/bin/env bash
# M1 技能验收：6 个技能各用一个真实问题走 /live 跑一遍。
#
# 问题取自各技能自己的 `hunter.prompt_tpl`（把 `{股票}` 换成真实标的）——
# 这样测的是「用户照着前端的提示语问，模型会不会走到这个技能」，
# 而不是「我明示让它用技能，它照做了吗」。后者测不出技能的召回。
#
# 在**宿主机**上跑：bash tools/probe/skill_e2e_all.sh [输出目录]
# 结果：每个用例一行 JSON 汇总（*.json）+ 完整 SSE（容器内 /workspace/.e2e/*.sse）
set -euo pipefail

OUT="${1:-$HOME/hca/e2e}"
CONTAINER="${HCA_CONTAINER:-hca-daemon}"
mkdir -p "$OUT"

run() {
  local id="$1" msg="$2"
  echo "── $id ──────────────────────────────────────────"
  echo "问：$msg"
  docker exec "$CONTAINER" python3 /opt/hca/tools/skill_e2e.py \
      --fresh --id "$id" --message "$msg" --timeout 300 \
      > "${OUT}/${id}.json" 2>"${OUT}/${id}.err" || echo "（退出码非 0，详见 ${id}.err）"
  python3 - "${OUT}/${id}.json" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as e:
    print("  汇总解析失败：", e); raise SystemExit(0)
if "error" in d:
    print("  ✗", d["error"]); raise SystemExit(0)
print(f"  技能：{d['skills_used'] or '（没走技能）'}")
print(f"  工具：{d['all_tools'] or '（一个没调）'}")
for c in d["mcp_calls"]:
    flag = "✅" if c["success"] else "✗"
    print(f"    {flag} {c['tool']}#{c['seq']}  {c['duration_ms']} ms  {c['output_head'][:150]}")
print(f"  耗时：{d['wall_ms']} ms（daemon 记 {d['duration_ms']} ms）  轮次 {d['rounds']}  "
      f"stop_reason={d['stop_reason']}")
print(f"  会话：{d.get('session_switched_to') or '（没能换新会话：' + str(d.get('session_switch_error')) + '）'}")
print(f"  MCP：{d.get('mcp_connected')}/{d.get('mcp_total')} connected（发消息前那一刻）")
perms = d.get("permissions") or []
print(f"  权限：弹了 {len(perms)} 次" + ("".join(f"\n    · {x['tool_name']}  {str(x['arguments'])[:110]}" for x in perms) if perms else ""))
q = d.get("quota_delta")
print(f"  tokens：网关配额差值 {q if q is not None else '—（查不到）'}  ·  "
      f"SSE 里的 stats：prompt {d['prompt_tokens']} completion {d['completion_tokens']} "
      f"cached {d['cached_tokens']}")
print(f"  正文 {d['text_len']} 字：{d['text_head'][:200]}")
PY
  echo
}

run s1-deep_analysis  "帮我写一份 600519 贵州茅台的深度投研报告"
run s2-investor_panel "看看 66 位大佬对 000001 平安银行的投票结果"
run s3-lhb_analyzer   "分析 002594 比亚迪的龙虎榜，看看是哪家游资在买"
run s4-risk_profile   "我风险偏保守 · 现金还有 5 万 · 单票别超过 20%"
run s5-trap_detector  "帮我测一下 300750 宁德时代是不是杀猪盘"
run s6-uzi            "对 600036 招商银行进行 UZI 全方位深度扫描"

echo "全部完成，结果在 ${OUT}"
