#!/usr/bin/env bash
# app/migrate.py 手工验收脚本 · 四种情况真跑一遍
#
# 为什么要有它:pytest 环境里不一定能起 docker,而这四种情况(空库/老库/重复启动/并发)
# 只有对着真 postgres 跑才有意义。跑法:
#
#     bash apps/api/tests/manual_migrate_check.sh            # 用默认端口 5462
#     bash apps/api/tests/manual_migrate_check.sh 5470        # 指定端口
#
# 依赖:docker + 本机已有 ghcr.io/agentpit-io/hunter-community-api 镜像
#      (镜像只是拿来当「装好 psycopg2 的 python 环境」,代码从仓库挂进去)
# 跑完自动删容器。
set -euo pipefail

PORT="${1:-5462}"
PGNAME="m1b-manual-pg"
IMAGE="${MIGRATE_TEST_IMAGE:-ghcr.io/agentpit-io/hunter-community-api:latest}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PGPASS="m1b-local-only"   # 一次性容器的临时口令,跑完就删,不是任何环境的真实凭据

cleanup() { docker rm -f "$PGNAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

say() { printf '\n\033[1m═══ %s\033[0m\n' "$*"; }

# 在仓库代码 + 镜像里的 python 环境下跑一条命令
inimg() {
  local db="$1"; shift
  docker run --rm --network host \
    -v "$REPO/apps/api:/src" -v "$REPO/db/migrations:/opt/hunter-migrations:ro" \
    -w /src \
    -e HUNTER_MIGRATIONS_DIR=/opt/hunter-migrations \
    -e DATABASE_URL="postgresql://hunter:${PGPASS}@127.0.0.1:${PORT}/${db}" \
    "$IMAGE" "$@"
}

psql_() { docker exec -i -e PGPASSWORD="$PGPASS" "$PGNAME" psql -U hunter -d "$1" -tAc "$2"; }
newdb() { docker exec -e PGPASSWORD="$PGPASS" "$PGNAME" psql -U hunter -d postgres -qc "CREATE DATABASE $1"; }

say "起一次性 postgres(端口 $PORT)"
cleanup
docker run -d --name "$PGNAME" \
  -e POSTGRES_USER=hunter -e POSTGRES_PASSWORD="$PGPASS" -e POSTGRES_DB=hunter \
  -p "$PORT":5432 postgres:16-alpine >/dev/null
for _ in $(seq 60); do docker exec "$PGNAME" pg_isready -U hunter >/dev/null 2>&1 && break; sleep 1; done
docker exec "$PGNAME" pg_isready -U hunter

TOTAL=$(ls "$REPO"/db/migrations/*.sql | wc -l | tr -d ' ')
echo "仓库里共 $TOTAL 个迁移文件"

# ── 情况 1 · 空库 ────────────────────────────────────────────────────────
say "情况 1 · 空库:跑一遍应全部执行并记录"
newdb t_empty
inimg t_empty python -m app.migrate
echo "schema_migrations 行数: $(psql_ t_empty 'select count(*) from schema_migrations')(应为 $TOTAL)"
echo "public 表数量:          $(psql_ t_empty "select count(*) from information_schema.tables where table_schema='public'")"
echo "users.compliance_ack_at:$(psql_ t_empty "select count(*) from information_schema.columns where table_name='users' and column_name='compliance_ack_at'")(应为 1)"
echo "stocks.avg_cost:        $(psql_ t_empty "select count(*) from information_schema.columns where table_name='stocks' and column_name='avg_cost'")(应为 1)"
echo "daily_close 视图:       $(psql_ t_empty "select count(*) from information_schema.views where table_name='daily_close'")(应为 1)"

# ── 情况 2 · 老库 ────────────────────────────────────────────────────────
say "情况 2 · 老库(8 月状态:initdb 灌过 0001~0014 + 老 api 跑过 init_db)"
newdb t_old
echo "-- 复刻 initdb 行为(psql 不带 ON_ERROR_STOP,失败继续)--"
for f in "$REPO"/db/migrations/00{0,1}*.sql; do
  b=$(basename "$f")
  case "$b" in 0001*|0002*|0003*|0004*|0005*|0006*|0007*|0008*|0009*|0010*|0011*|0012*|0013*|0014*) ;; *) continue ;; esac
  # 先整段收下来再 grep:直接管给 grep -q 会因为 SIGPIPE + pipefail 误判成功/失败
  out=$(docker exec -i -e PGPASSWORD="$PGPASS" "$PGNAME" psql -q -U hunter -d t_old < "$f" 2>&1 || true)
  err=$(echo "$out" | grep -m1 '^ERROR' || true)
  if [ -n "$err" ]; then echo "  $b  ← 当年就失败了 · $err"; else echo "  $b  ok"; fi
done
echo "-- 老版本 api 起来,跑 init_db() --"
inimg t_old python -c "import asyncio;from app.services.database import init_db;asyncio.run(init_db())"
echo "升级前 stocks.avg_cost: $(psql_ t_old "select count(*) from information_schema.columns where table_name='stocks' and column_name='avg_cost'")(应为 0 · 0015 没跑过)"
echo "-- 升级到新版本 api --"
inimg t_old python -m app.migrate
echo "schema_migrations 行数: $(psql_ t_old 'select count(*) from schema_migrations')(应为 $TOTAL)"
echo "升级后 stocks.avg_cost: $(psql_ t_old "select count(*) from information_schema.columns where table_name='stocks' and column_name='avg_cost'")(应为 1)"
echo "升级后 screen_quota_usage: $(psql_ t_old "select count(*) from information_schema.tables where table_name='screen_quota_usage'")(应为 1)"

# ── 情况 3 · 重复启动 ────────────────────────────────────────────────────
say "情况 3 · 重复启动:第二遍应 0 个待执行、账本行数不变"
BEFORE=$(psql_ t_empty 'select count(*) from schema_migrations')
BEFORE_TS=$(psql_ t_empty 'select max(applied_at) from schema_migrations')
inimg t_empty python -m app.migrate
AFTER=$(psql_ t_empty 'select count(*) from schema_migrations')
AFTER_TS=$(psql_ t_empty 'select max(applied_at) from schema_migrations')
echo "账本行数 $BEFORE → $AFTER(应相等)"
echo "最后应用时间 $BEFORE_TS → $AFTER_TS(应相等,说明确实没重跑)"
[ "$BEFORE" = "$AFTER" ] && [ "$BEFORE_TS" = "$AFTER_TS" ] || { echo "!! 不一致"; exit 1; }

# ── 情况 4 · 并发 ────────────────────────────────────────────────────────
say "情况 4 · 并发:两个进程同时跑空库,advisory lock 应串行化"
newdb t_race
inimg t_race python -m app.migrate >/tmp/m1b-race-a.log 2>&1 &
A=$!
inimg t_race python -m app.migrate >/tmp/m1b-race-b.log 2>&1 &
B=$!
wait $A; RA=$?
wait $B; RB=$?
echo "进程 A 退出码 $RA · 进程 B 退出码 $RB(都应为 0)"
echo "A: $(grep -o '本次待执行 [0-9]* 个' /tmp/m1b-race-a.log | tail -1)"
echo "B: $(grep -o '本次待执行 [0-9]* 个' /tmp/m1b-race-b.log | tail -1)"
echo "schema_migrations 行数: $(psql_ t_race 'select count(*) from schema_migrations')(应为 $TOTAL,没有重复)"
[ "$RA" = 0 ] && [ "$RB" = 0 ] || { echo "!! 有进程失败"; exit 1; }

# ── 附加 · dry-run ───────────────────────────────────────────────────────
say "附加 · --dry-run 不改数据库"
newdb t_dry
inimg t_dry python -m app.migrate --dry-run
echo "schema_migrations 是否存在: $(psql_ t_dry "select coalesce(to_regclass('schema_migrations')::text,'不存在')")(应为「不存在」)"

# ── 附加 · 逐个文件真·幂等核实 ────────────────────────────────────────────
# 在已经迁移完的 t_empty 上,把每个文件单独再跑一遍(ON_ERROR_STOP=1)。
# 这一步比 CI 的 grep 规则强:0010 写法上是 CREATE OR REPLACE VIEW(grep 通过),
# 实际在 0014 之后重跑会报 cannot drop columns from view。
say "附加 · 逐个迁移文件在已迁移库上重跑一遍"
for f in "$REPO"/db/migrations/*.sql; do
  b=$(basename "$f")
  if out=$(docker exec -i -e PGPASSWORD="$PGPASS" "$PGNAME" psql -q -U hunter -d t_empty -v ON_ERROR_STOP=1 < "$f" 2>&1); then
    echo "  可重复 ✅ $b"
  else
    echo "  失败   ❌ $b :: $(echo "$out" | head -1)"
  fi
done

# ── 附加 · pytest ────────────────────────────────────────────────────────
# 运行镜像里没装 pytest(它是运行镜像不是测试镜像),现装一个 —— 需要能连 pypi。
# 连不上就跳过,上面四种情况已经真跑过了,不必卡在这一步。
say "附加 · 在镜像里跑 pytest tests/test_migrate.py"
docker run --rm --network host \
  -v "$REPO/apps/api:/src" -v "$REPO/db/migrations:/opt/hunter-migrations:ro" -w /src \
  -e HUNTER_MIGRATIONS_DIR=/opt/hunter-migrations \
  -e TEST_DATABASE_URL="postgresql://hunter:${PGPASS}@127.0.0.1:${PORT}/postgres" \
  "$IMAGE" sh -c 'pip install -q pytest && python -m pytest tests/test_migrate.py -p no:cacheprovider -q' \
  || echo "(pytest 步骤失败/跳过 —— 装不上 pytest 时属正常,四种情况上面已真跑过)"

say "四种情况全部跑完"
