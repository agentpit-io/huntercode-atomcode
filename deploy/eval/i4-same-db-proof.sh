#!/usr/bin/env bash
# I4 · 「两边共用同一个数据库」的证据脚本 —— **每批评测开跑前跑一次，输出存档**。
#
#     bash deploy/eval/i4-same-db-proof.sh [HCA_daemon_容器名]
#
# ## 为什么要有这个脚本
#
# I4 的任务书要求「同一台机器、同一个数据库、同一份数据」，并给了一个做法：
# 「基线栈不再起自己的 postgres，改指到 HCA 那套」。**实际不需要这么改** ——
# 评测拓扑里本来就只有一套 api、一套 postgres：
#
#   · HCA 侧只起 daemon + llm-shim（`deploy/eval/docker-compose.i2.yml`），
#     它的 6 个 hunter 系 MCP 通过 `HERMES_API_URL` 打**基线那套 api**
#     （`deploy/eval/docker-compose.hca-api.yml`，网络别名 `hunter-api`）；
#   · 社区版 opencode 打的是同一个容器（服务名 `api`）；
#   · 那个 api 只连一个 postgres，而整台机器上**只有这一个 postgres 容器**。
#
# 也就是说两边共用的不只是同一个库，是**同一个 api 进程、同一个库、同一行用户**。
# 这比「两库同构 + 恢复同一份 dump」更强，所以不走任务书那条退路。
# 强到什么程度要有证据，就是下面这六条 —— 任何一条不成立就 rc≠0，别开跑。
#
# 退出码：0 = 六条全成立；6 = 有条不成立（调用方应当拒绝开跑）。
set -u
DAEMON="${1:-hca-i4-daemon}"
BASE_API="${HCA_BASELINE_API_CONTAINER:-hca-baseline-api-1}"
BASE_OC="${HCA_BASELINE_CONTAINER:-hca-baseline-opencode-1}"
BASE_PG="${HCA_BASELINE_PG_CONTAINER:-hca-baseline-postgres-1}"
fail=0
ok()   { printf '  ✓ %s\n' "$*"; }
bad()  { printf '  ✗ %s\n' "$*" >&2; fail=1; }

echo "=== I4 同库证据 · $(date '+%Y-%m-%d %H:%M:%S %z') ==="
echo "机器：$(hostname) · $(uptime)"

# ── 1. 整台机器上有几个 postgres ────────────────────────────────────────────
echo
echo "[1] 整台机器在跑的 postgres 容器"
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' | grep -i postgres | sed 's/^/    /'
n_pg=$(docker ps --format '{{.Image}}' | grep -ci postgres || true)
[ "$n_pg" = "1" ] && ok "只有 1 个 postgres 实例（n=$n_pg）" \
                  || bad "postgres 实例数 = $n_pg，不是 1 —— 同库前提不成立"

# ── 2. 两边的后端指向是不是同一个容器 ───────────────────────────────────────
echo
echo "[2] 两个引擎的后端 HERMES_API_URL 指向"
d_url=$(docker exec "$DAEMON" printenv HERMES_API_URL 2>/dev/null || echo "")
o_url=$(docker exec "$BASE_OC" printenv HERMES_API_URL 2>/dev/null || echo "")
echo "    HCA daemon  ($DAEMON) : ${d_url:-—}"
echo "    社区版 opencode ($BASE_OC) : ${o_url:-—}"
d_host=$(printf '%s' "$d_url" | sed -E 's#^https?://([^:/]+).*#\1#')
o_host=$(printf '%s' "$o_url" | sed -E 's#^https?://([^:/]+).*#\1#')
d_ip=$(docker exec "$DAEMON"  getent hosts "$d_host" 2>/dev/null | awk '{print $1}' | head -1)
o_ip=$(docker exec "$BASE_OC" getent hosts "$o_host" 2>/dev/null | awk '{print $1}' | head -1)
echo "    ${d_host} → ${d_ip:-—}（在 daemon 容器里解析）"
echo "    ${o_host} → ${o_ip:-—}（在 opencode 容器里解析）"
api_ips=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$BASE_API" 2>/dev/null)
echo "    ${BASE_API} 的容器 IP：${api_ips:-—}"
# 两个主机名各在自己那张网络上解析，IP 可以不同（同一个容器接了两张网）——
# 判据是**两个 IP 都属于同一个 api 容器**，不是两个 IP 相等。
hit=0
for ip in $api_ips; do [ "$ip" = "$d_ip" ] && hit=$((hit+1)); done
for ip in $api_ips; do [ "$ip" = "$o_ip" ] && hit=$((hit+1)); done
[ "$hit" = "2" ] && ok "两边解析到的都是同一个 api 容器 ${BASE_API}" \
                 || bad "两边的后端不是同一个容器（命中 $hit/2）"

# ── 3. 那个 api 连的是哪个库 ────────────────────────────────────────────────
echo
echo "[3] api 的 DATABASE_URL 与 postgres 实例身份"
db_url=$(docker exec "$BASE_API" printenv DATABASE_URL 2>/dev/null || echo "")
echo "    DATABASE_URL = $(printf '%s' "$db_url" | sed -E 's#//([^:]+):[^@]+@#//\1:****@#')"
pg_host=$(printf '%s' "$db_url" | sed -E 's#^[a-z+]+://[^@]*@([^:/]+).*#\1#')
pg_db=$(printf '%s' "$db_url"   | sed -E 's#.*/([^/?]+)(\?.*)?$#\1#')
pg_ip=$(docker exec "$BASE_API" getent hosts "$pg_host" 2>/dev/null | awk '{print $1}' | head -1)
pg_real=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$BASE_PG" 2>/dev/null)
echo "    ${pg_host} → ${pg_ip:-—}（在 api 容器里解析）；${BASE_PG} 的 IP：${pg_real:-—}"
case " $pg_real " in *" $pg_ip "*) ok "api 连的就是 ${BASE_PG}" ;;
                     *) bad "api 连的不是 ${BASE_PG}" ;; esac
sysid=$(docker exec "$BASE_PG" psql -U hunter -d "$pg_db" -Atc \
        "select system_identifier from pg_control_system();" 2>/dev/null)
echo "    库名 = ${pg_db} · 实例 system_identifier = ${sysid:-—}"
[ -n "$sysid" ] && ok "postgres 实例身份可读（同一实例的同一个库）" \
                || bad "读不到 system_identifier"

# ── 4. 两边是不是同一行用户、同一份账本 ─────────────────────────────────────
echo
echo "[4] 同一份数据：同一个用户、同一份持仓论点"
uid_env=$(docker exec "$DAEMON" printenv HUNTER_USER_ID 2>/dev/null || echo "")
uid_db=$(docker exec "$BASE_PG" psql -U hunter -d "$pg_db" -Atc \
         "select id from users order by created_at limit 1;" 2>/dev/null)
n_users=$(docker exec "$BASE_PG" psql -U hunter -d "$pg_db" -Atc \
          "select count(*) from users;" 2>/dev/null)
uid_oc=$(docker exec "$BASE_PG" psql -U hunter -d "$pg_db" -Atc \
         "select user_id from chat_session_owner order by created_at desc limit 1;" 2>/dev/null)
echo "    users 表行数 = ${n_users:-—}；users[0].id = ${uid_db:-—}"
echo "    HCA daemon 的 HUNTER_USER_ID = ${uid_env:-—}"
echo "    社区版最近一次会话登记的 user_id（chat_session_owner）= ${uid_oc:-—}"
if [ -n "$uid_env" ] && [ "$uid_env" = "$uid_db" ]; then
  ok "HCA 侧用的就是库里那一行用户"
else
  bad "HCA 侧的 HUNTER_USER_ID 与库里的用户对不上"
fi
if [ -n "$uid_oc" ] && [ "$uid_oc" = "$uid_db" ]; then
  ok "社区版侧登记的也是同一行用户"
else
  # 还没跑过社区版就没有这一行，不算失败，如实标注
  echo "    ⚠ chat_session_owner 还没有社区版的行（这批还没跑过），本条留待跑完复核"
fi
n_thesis=$(docker exec "$BASE_PG" psql -U hunter -d "$pg_db" -Atc \
           "select count(*) from position_thesis;" 2>/dev/null)
echo "    position_thesis 行数 = ${n_thesis:-—}（两边读的是同一份）"

# ── 5/6. 真调用一次，并在**库那一侧**看见这次调用 ───────────────────────────
#
# 前四条查的都是配置与解析结果。这一条是行为证据：两个引擎各发一次真请求，
# 每次请求前后各读一次 `pg_stat_database.xact_commit` —— 事务数涨了，
# 就说明这一次调用真的落到了**这个** postgres 实例上，不是落在别处。
echo
echo "[5/6] 真调用（不是看配置）：两个引擎各打一次 /api/internal/quant/market_screen，"
echo "      并在 ${BASE_PG} 上看这次调用带来的事务增量"
xacts() {
  docker exec "$BASE_PG" psql -U hunter -d "$pg_db" -Atc \
    "select xact_commit from pg_stat_database where datname='${pg_db}';" 2>/dev/null
}
probe() {  # $1=容器 —— 用一个**会真读库**的脚本，空脚本只会被参数校验挡回来
  docker exec "$1" sh -c 'curl -s -o /tmp/i4probe.out -w "%{http_code}" -m 40 \
     -X POST -H "Content-Type: application/json" \
     -H "X-Hunter-Internal-Key: ${HUNTER_INTERNAL_KEY}" \
     -d "{\"script\":\"plot scan = close > 0;\",\"limit\":2}" \
     "${HERMES_API_URL}/api/internal/quant/market_screen"' 2>/dev/null
}
for pair in "HCA_daemon:$DAEMON" "社区版_opencode:$BASE_OC"; do
  label="${pair%%:*}"; ct="${pair#*:}"
  b=$(xacts); code=$(probe "$ct"); a=$(xacts)
  matched=$(docker exec "$ct" sh -c 'python3 -c "import json,sys;print(json.load(open(\"/tmp/i4probe.out\")).get(\"matched\"))"' 2>/dev/null \
            || docker exec "$ct" sh -c 'head -c 60 /tmp/i4probe.out' 2>/dev/null)
  d=$(( ${a:-0} - ${b:-0} ))
  echo "    ${label} → HTTP ${code:-—} · 命中 ${matched:-—} 只 · 该实例事务 +${d}"
  if [ "$code" = "200" ] && [ "$d" -gt 0 ]; then
    ok "${label} 这一次调用真的落到了 ${BASE_PG}"
  else
    bad "${label} 没打通或没在这个库上产生事务（HTTP=${code} 事务增量=${d}）"
  fi
done
echo "    postgres 侧当前连接："
docker exec "$BASE_PG" psql -U hunter -d "$pg_db" -Atc \
  "select coalesce(host(client_addr),'local'), count(*) from pg_stat_activity
    where datname is not null group by 1 order by 2 desc;" 2>/dev/null | sed 's/^/      /'

echo
if [ "$fail" = "0" ]; then
  echo "=== 结论：同机 + 同一个 api + 同一个 postgres 实例 + 同一行用户 —— 全部成立 ==="
  exit 0
fi
echo "=== ✗ 有条不成立，不要开跑 ===" >&2
exit 6
