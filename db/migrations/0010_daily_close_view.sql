-- 修 daily_close.amount · klines.volume 单位是手(100 股)· 成交额需 × 100
--
-- ⚠️ 开头这句 DROP 是 M1(2026-09-18)加的,不是历史写法。原因:
-- 0014_daily_close_v2.sql 用 `CREATE OR REPLACE VIEW` 给同一个视图**加了一列**
-- (adv_20d)。而 OR REPLACE **不许减列** —— 所以在「已经跑过 0014」的库上单独重跑
-- 本文件会报 `ERROR: cannot drop columns from view`,迁移执行器当场失败、api 起不来。
--
-- 谁会中招:**手工补跑过全部迁移、但 schema_migrations 账本是空的**那类库
-- (演示站 fin-r1 就是这种)。它们升级到自动迁移之后,执行器看到账本里没有 0010
-- 就会从头跑一遍,正好撞上。
--
-- 为什么现在加代价为零:schema_migrations 这张表是本版本才引入的,世上还没有任何库
-- 记录过本文件的 checksum;过了这个版本再改,所有已部署的库都会刷一条
-- 「迁移文件被改过」的 WARNING。
--
-- 为什么**不带 CASCADE**:已 grep 确认只有 Python 查询引用 daily_close,没有任何
-- SQL 对象依赖它。将来真长出依赖时,应该在这里大声失败,而不是静默把依赖一起删掉。
DROP VIEW IF EXISTS daily_close;

CREATE OR REPLACE VIEW daily_close AS
SELECT
    code || CASE WHEN code LIKE '6%' THEN '.SH' ELSE '.SZ' END AS symbol,
    ts AS trade_date,
    open, high, low, close, volume,
    close * volume * 100 AS amount  -- A股 volume 单位是手 · 成交额 = 均价 × 手数 × 100
FROM klines
WHERE period='daily';
