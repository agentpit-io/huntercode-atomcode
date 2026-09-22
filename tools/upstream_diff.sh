#!/usr/bin/env bash
# 上游版本差异报告 —— 只看本发行版真正依赖的**四个外部面**（总控规则 技术约束）。
#
#   tools/upstream_diff.sh --to 5.0.9                 # 与 pins.lock 当前版本比
#   tools/upstream_diff.sh --from v5.1.0 --to v5.0.9  # 两个 tag 直接比
#   tools/upstream_diff.sh --to 5.2.0 --no-npm        # 离线，只比源码
#
# 为什么不是 `git diff v5.0.9..v5.1.0`：上游一次发布动几万行，直接 diff 看不出
# **我们依赖的接口有没有变**。这里把四个面各抽成一份排序过的事实清单再 diff：
#   面 1 daemon HTTP 路由 + `/live` 的 SSE 事件（type 取值与载荷字段）
#   面 2 skill 解析（frontmatter 字段、目录约定、GET /skills 回包）
#   面 3 hook（事件枚举、cc_name、`.hooks.json` 结构、输入输出 JSON 键）
#   面 4 MCP 配置（`.mcp.json` 字段、信任门、超时常量）
# 抽不到就写 `!! 抽取失败` 并让整次 rc=3 —— 抽取失败本身是信号（文件挪了/结构大改）。
#
# 另外两件事：
#   · npm 侧核对（`@atomgit.com/atomcode@<ver>-linux-x64` 的 dist.integrity /
#     shasum，以及上游 latest.json 里该版本的 sha256 / size）
#   · 若存在 fork 补丁（docs/fork-patch/apply.py），在目标版本的干净工作树上
#     试打一遍，输出 rebase 结果（哪几个锚点还在、哪几个断了）
#
# 退出码：0 = 四个面全无变化且补丁能打上；3 = 有变化或抽取失败或补丁打不上；2 = 用法/环境错误。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPSTREAM="${HCA_UPSTREAM_DIR:-$(cd "$REPO/.." && pwd)/atomcode-upstream}"
NPM_PKG="@atomgit.com/atomcode"
FROM=""; TO=""; OUTDIR="$REPO/docs/upstream-diff"; DO_NPM=1; DO_PATCH=1; KEEP=0

die() { echo "错误：$*" >&2; exit 2; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from) FROM="${2:-}"; shift 2 ;;
    --to) TO="${2:-}"; shift 2 ;;
    --upstream) UPSTREAM="${2:-}"; shift 2 ;;
    --out) OUTDIR="${2:-}"; shift 2 ;;
    --no-npm) DO_NPM=0; shift ;;
    --no-patch) DO_PATCH=0; shift ;;
    --keep-work) KEEP=1; shift ;;
    -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "不认识的参数：$1" ;;
  esac
done

[[ -n "$TO" ]] || die "必须给 --to <版本号或 tag>"
[[ -d "$UPSTREAM/.git" ]] || die "上游克隆不在：$UPSTREAM（用 --upstream 指定）"
command -v python3 >/dev/null || die "需要 python3"

# ── pins.lock 当前版本 ────────────────────────────────────────────────────
pins_version() {
  python3 - "$REPO/pins.lock" <<'PY'
import re, sys
txt = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r'^\s*\[atomcode\]\s*$(.*?)(?=^\s*\[|\Z)', txt, re.M | re.S)
sec = m.group(1) if m else txt
v = re.search(r'^\s*version\s*=\s*"([^"]+)"', sec, re.M)
print(v.group(1) if v else "")
PY
}
PINS_VER="$(pins_version)"
[[ -n "$FROM" ]] || FROM="v${PINS_VER}"
[[ -n "$PINS_VER" ]] || echo "提示：pins.lock 里没读到 [atomcode] version" >&2

# ── 把「版本号或 tag」解析成 git rev ───────────────────────────────────────
resolve_rev() {
  local want="$1" r
  for r in "$want" "v$want" "${want#v}"; do
    if git -C "$UPSTREAM" rev-parse --verify --quiet "${r}^{commit}" >/dev/null; then
      echo "$r"; return 0
    fi
  done
  return 1
}
FROM_REV="$(resolve_rev "$FROM")" || die "上游克隆里找不到 $FROM（先在 $UPSTREAM 里 git fetch --tags）"
TO_REV="$(resolve_rev "$TO")"   || die "上游克隆里找不到 $TO（先在 $UPSTREAM 里 git fetch --tags）"

ver_of() { local x="${1#v}"; echo "$x"; }
FROM_VER="$(ver_of "$FROM")"; TO_VER="$(ver_of "$TO")"

sha_of()  { git -C "$UPSTREAM" rev-parse --short "$1"; }
date_of() { git -C "$UPSTREAM" log -1 --format=%ci "$1"; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/hca-upstream-diff.XXXXXX")"
cleanup() { [[ "$KEEP" == 1 ]] || rm -rf "$WORK"; }
trap cleanup EXIT

mkdir -p "$OUTDIR"
REPORT="$OUTDIR/${FROM_VER}__${TO_VER}.md"
STAMP="$(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S')"
RC=0

echo "上游差异报告：$FROM_REV（$(sha_of "$FROM_REV")） → $TO_REV（$(sha_of "$TO_REV")）"

# ── 四个面：抽取 + diff ───────────────────────────────────────────────────
EX="$REPO/tools/upstream_diff_extract.py"
[[ -f "$EX" ]] || die "抽取器不在：$EX"
set +e
python3 "$EX" "$UPSTREAM" "$FROM_REV" "$WORK/from"; EX_FROM=$?
python3 "$EX" "$UPSTREAM" "$TO_REV"   "$WORK/to";   EX_TO=$?
set -e
[[ $EX_FROM -eq 2 || $EX_TO -eq 2 ]] && die "抽取器用法错误"
[[ $EX_FROM -eq 3 || $EX_TO -eq 3 ]] && RC=3

SURFACES=("1-daemon-routes-sse|面 1 · daemon 路由与 SSE 事件" \
          "2-skill|面 2 · skill 解析" \
          "3-hook|面 3 · hook 事件与格式" \
          "4-mcp|面 4 · MCP 配置")

declare -A CHANGED
for entry in "${SURFACES[@]}"; do
  key="${entry%%|*}"
  # 抬头行（`### <面名> @ <rev>`）两边必然不同，diff 前先剥掉，只比事实本身
  tail -n +2 "$WORK/from/$key.txt" > "$WORK/from/$key.body"
  tail -n +2 "$WORK/to/$key.txt"   > "$WORK/to/$key.body"
  diff -u --label "$key @ $FROM_REV" --label "$key @ $TO_REV" \
    "$WORK/from/$key.body" "$WORK/to/$key.body" > "$WORK/$key.diff" || true
  n=$(grep -cE '^[-+][^-+]' "$WORK/$key.diff" || true)
  CHANGED[$key]=$n
  [[ "$n" -gt 0 ]] && RC=3
done

# ── npm / latest.json 核对 ───────────────────────────────────────────────
npm_json() {  # $1=版本号 → 一行 JSON（取不到就 {}）
  local v="$1"
  curl -fsS -m 30 "https://registry.npmjs.org/${NPM_PKG}/${v}-linux-x64" 2>/dev/null \
    | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: print("{}"); raise SystemExit
print(json.dumps({"version":d.get("version"),
                  "integrity":(d.get("dist") or {}).get("integrity"),
                  "shasum":(d.get("dist") or {}).get("shasum"),
                  "tarball":(d.get("dist") or {}).get("tarball")},ensure_ascii=False))' \
    || echo '{}'
}

# latest.json 的坑：**tag 上那份写的是上一版**（v5.1.0 的 tag 里 version=v5.0.9），
# 同步 commit 落在 tag 之后。所以按内容找同步 commit，而不是读 tag 上的文件。
latest_for() {  # $1=版本号 → "commit|sha256|size"（找不到给 "|—|—"）
  local want="v${1#v}" c v
  for c in $(git -C "$UPSTREAM" log --format=%H -60 -- latest.json); do
    v=$(git -C "$UPSTREAM" show "$c:latest.json" 2>/dev/null \
        | python3 -c 'import json,sys;print(json.load(sys.stdin).get("version",""))' 2>/dev/null || true)
    if [[ "$v" == "$want" ]]; then
      git -C "$UPSTREAM" show "$c:latest.json" | python3 -c '
import json, sys
d = json.load(sys.stdin).get("binaries", {}).get("linux-x64", {})
print(str(d.get("sha256") or "—") + "|" + str(d.get("size") or "—"))' \
        | sed "s|^|$(git -C "$UPSTREAM" rev-parse --short "$c")\||"
      return 0
    fi
  done
  echo "—|—|—"
}

FROM_NPM='{}'; TO_NPM='{}'
if [[ "$DO_NPM" == 1 ]]; then
  FROM_NPM="$(npm_json "$FROM_VER")"; TO_NPM="$(npm_json "$TO_VER")"
fi
FROM_LATEST="$(latest_for "$FROM_VER")"; TO_LATEST="$(latest_for "$TO_VER")"

# ── fork 补丁 rebase ──────────────────────────────────────────────────────
PATCH_RESULT="（本仓库没有 fork 补丁，跳过）"
PATCH_LOG=""
APPLY="$REPO/docs/fork-patch/apply.py"
if [[ "$DO_PATCH" == 1 && -f "$APPLY" ]]; then
  WT="$WORK/wt-$TO_VER"
  if git -C "$UPSTREAM" worktree add --detach "$WT" "$TO_REV" >/dev/null 2>&1; then
    set +e
    PATCH_LOG="$(cd "$WT" && python3 "$APPLY" "$WT" 2>&1)"; prc=$?
    set -e
    if [[ $prc -eq 0 ]]; then
      changed=$(git -C "$WT" status --porcelain | wc -l | tr -d ' ')
      PATCH_RESULT="✅ 能打上（rc=0，改动 ${changed} 个文件）"
    else
      PATCH_RESULT="❌ **打不上**（rc=$prc）—— 锚点已被上游改掉，补丁要重做"
      RC=3
    fi
    git -C "$UPSTREAM" worktree remove --force "$WT" >/dev/null 2>&1 || true
  else
    PATCH_RESULT="⚠️ 建不出 worktree，未验"
  fi
fi

# ── 出报告 ───────────────────────────────────────────────────────────────
emit_diff() {  # $1=key $2=标题
  local key="$1" title="$2" n="${CHANGED[$1]}"
  echo "### $title"
  echo
  if [[ "$n" -eq 0 ]]; then
    echo "**无变化**（抽出的事实清单逐行一致）。"
  else
    echo "**有 $n 行变化**："
    echo
    echo '```diff'
    head -200 "$WORK/$key.diff"
    [[ $(wc -l < "$WORK/$key.diff") -gt 200 ]] && echo "…（还有 $(( $(wc -l < "$WORK/$key.diff") - 200 )) 行，全文见 $OUTDIR/${FROM_VER}__${TO_VER}.$key.diff）"
    echo '```'
  fi
  echo
  cp "$WORK/$key.diff" "$OUTDIR/${FROM_VER}__${TO_VER}.$key.diff"
}

{
  echo "# 上游差异报告 · AtomCode $FROM_VER → $TO_VER"
  echo
  echo "> 由 \`tools/upstream_diff.sh\` 生成于 $STAMP（上海时间）。"
  echo "> 只比本发行版依赖的**四个外部面**；上游其余改动（webui、CLI、TUI、内部重构）不在范围内。"
  echo
  echo "| 项 | 起点 | 终点 |"
  echo "|---|---|---|"
  echo "| 版本 | \`$FROM_VER\`${PINS_VER:+$([[ "$FROM_VER" == "$PINS_VER" ]] && echo "（pins.lock 当前版本）")} | \`$TO_VER\` |"
  echo "| git rev | \`$FROM_REV\` @ \`$(sha_of "$FROM_REV")\` | \`$TO_REV\` @ \`$(sha_of "$TO_REV")\` |"
  echo "| 提交时间 | $(date_of "$FROM_REV") | $(date_of "$TO_REV") |"
  echo
  echo "## 0. 结论一行"
  echo
  total=$(( ${CHANGED[1-daemon-routes-sse]} + ${CHANGED[2-skill]} + ${CHANGED[3-hook]} + ${CHANGED[4-mcp]} ))
  if [[ "$total" -eq 0 ]]; then
    echo "四个外部面**一处未变**。升级到 \`$TO_VER\` 不需要改本发行版的适配层 —— 但仍要按第 4 节实跑一遍冒烟。"
  else
    echo "四个外部面共 **$total 行变化**，逐面见第 2 节。**变化 ≠ 一定要改代码**，但每一处都要人看过再决定。"
  fi
  echo
  echo "| 面 | 变化行数 |"
  echo "|---|---|"
  echo "| 面 1 · daemon 路由与 SSE 事件 | ${CHANGED[1-daemon-routes-sse]} |"
  echo "| 面 2 · skill 解析 | ${CHANGED[2-skill]} |"
  echo "| 面 3 · hook 事件与格式 | ${CHANGED[3-hook]} |"
  echo "| 面 4 · MCP 配置 | ${CHANGED[4-mcp]} |"
  echo
  echo "## 1. 二进制与来源"
  echo
  echo "| 项 | $FROM_VER | $TO_VER |"
  echo "|---|---|---|"
  fl_c="${FROM_LATEST%%|*}"; fl_rest="${FROM_LATEST#*|}"; fl_sha="${fl_rest%%|*}"; fl_size="${fl_rest#*|}"
  tl_c="${TO_LATEST%%|*}";   tl_rest="${TO_LATEST#*|}";   tl_sha="${tl_rest%%|*}"; tl_size="${tl_rest#*|}"
  echo "| latest.json 同步 commit | \`$fl_c\` | \`$tl_c\` |"
  echo "| linux-x64 sha256 | \`$fl_sha\` | \`$tl_sha\` |"
  echo "| linux-x64 size | $fl_size | $tl_size |"
  if [[ "$DO_NPM" == 1 ]]; then
    python3 - "$FROM_NPM" "$TO_NPM" "$FROM_VER" "$TO_VER" <<'PY'
import json, sys
a, b = json.loads(sys.argv[1] or "{}"), json.loads(sys.argv[2] or "{}")
def g(d, k): return f"`{d[k]}`" if d.get(k) else "—（registry 没回）"
print(f"| npm 包 | `{sys.argv[3]}-linux-x64` | `{sys.argv[4]}-linux-x64` |")
print(f"| npm dist.integrity | {g(a,'integrity')} | {g(b,'integrity')} |")
print(f"| npm dist.shasum | {g(a,'shasum')} | {g(b,'shasum')} |")
PY
  else
    echo "| npm | —（\`--no-npm\`，本次没查） | —（同左） |"
  fi
  echo
  echo "> ⚠️ **latest.json 的坑**：tag 上的那份 \`latest.json\` 写的是**上一版**"
  echo "> （\`v5.1.0\` 的 tag 里 \`version\` 是 \`v5.0.9\`），同步 commit 落在打 tag 之后。"
  echo "> 所以这里是按**文件内容**回溯同步 commit，不是读 tag 上的文件。照着 tag 读会锁错校验值。"
  echo
  echo "## 2. 四个外部面逐项"
  echo
  emit_diff "1-daemon-routes-sse" "面 1 · daemon 路由与 SSE 事件"
  emit_diff "2-skill"             "面 2 · skill 解析"
  emit_diff "3-hook"              "面 3 · hook 事件与格式"
  emit_diff "4-mcp"               "面 4 · MCP 配置"
  echo "## 3. fork 补丁 rebase"
  echo
  echo "补丁脚本：\`docs/fork-patch/apply.py\`（M2 写的 \`system_prompt\` / \`system_prompt_file\` 两项）"
  echo
  echo "结果：$PATCH_RESULT"
  if [[ -n "$PATCH_LOG" ]]; then
    echo
    echo '```'
    echo "$PATCH_LOG" | head -60
    echo '```'
  fi
  echo
  echo "## 4. 升级前必须实跑的冒烟（本脚本不做）"
  echo
  echo "源码比对只能证明「接口没动」，不能证明「行为没变」。换底座前至少跑："
  echo
  echo "1. \`deploy/up.sh\` 从零拉起 → \`GET /health\` 回显的 \`binary_hash\` 等于 pins.lock 里的 sha256"
  echo "2. \`GET /mcp/status\` 9/9 connected、工具数 28（少一个就查哪个 server 没起）"
  echo "3. \`GET /skills\` 6 个技能齐、\`atomcode hooks list\` 8 条"
  echo "4. \`python3 tools/e2e/web_turn.py --cases skills\` 6 题全过，且每题命中正确技能"
  echo "5. \`node tools/e2e/m4-regression.mjs\` 真浏览器走一遍"
  echo
  echo "---"
  echo
  echo "退出码 \`$RC\`（0 = 四面无变化且补丁能打上；3 = 有变化 / 抽取失败 / 补丁打不上）。"
} > "$REPORT"

echo "报告已写到：$REPORT"
for entry in "${SURFACES[@]}"; do
  key="${entry%%|*}"; title="${entry#*|}"
  n="${CHANGED[$key]}"
  if [[ "$n" -eq 0 ]]; then echo "  ✅ $title：无变化"; else echo "  ⚠️  $title：$n 行变化"; fi
done
echo "  fork 补丁：$PATCH_RESULT"
exit $RC
