-- 迁移账本表 · 2026-09-17 · 见 docs/setup-wizard/design.md 3.4
--
-- 为什么需要它：迁移从「postgres 的 docker-entrypoint-initdb.d(只在数据卷首次创建时
-- 执行一次)」改成「api 启动时执行(apps/api/app/migrate.py)」之后，必须有个地方记住
-- 「这个库跑过哪些迁移」，否则每次启动都会把 22 个文件全跑一遍。
--
-- checksum 是迁移文件内容的 sha256。已记录但 checksum 变了 → 只打 WARNING，**不重跑**：
-- 已记录意味着这条 DDL 在本库生效过了，重跑轻则白跑、重则改坏已有数据(比如带 UPDATE 的
-- 0006)。确实需要改，请新增一个迁移文件，不要改老文件。
--
-- ⚠️ migrate.py 里也有一份同样的 CREATE TABLE(它得先有表才能查有哪些迁移跑过，
--    没法靠迁移文件自己把自己建出来)。改这里的话，migrate.py 的 SCHEMA_MIGRATIONS_DDL
--    要一起改，两边必须保持一致。
--
-- 这个文件放在这里，是为了让 db/migrations/ 仍然是一份完整的 schema 记录
-- (用 psql 手工灌一遍这个目录，也能得到和 api 跑出来一样的库)。
-- 幂等 · 只建表 · 不动任何已有数据

CREATE TABLE IF NOT EXISTS schema_migrations (
  filename   TEXT PRIMARY KEY,          -- 迁移文件名，如 0006_compliance.sql
  checksum   TEXT NOT NULL,             -- 文件内容 sha256 十六进制
  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  schema_migrations             IS '迁移账本 · 由 apps/api/app/migrate.py 在 api 启动时写入';
COMMENT ON COLUMN schema_migrations.checksum    IS '迁移文件内容 sha256 · 变了只告警不重跑';
