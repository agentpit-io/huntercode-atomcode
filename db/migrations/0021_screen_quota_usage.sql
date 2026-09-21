-- 魔法筛选器 · 会员每日次数(2026-09-14)
--
-- ⚠ 已有部署不会执行这个文件(db/migrations 只在数据卷第一次初始化时跑,见仓内 CLAUDE.md 那条铁律)。
-- 真正生效的 DDL 在 apps/api/app/services/quant/screen_quota.py 的 _DDL,两处保持一致。
-- 这里给全新安装用 + 留档。

CREATE TABLE IF NOT EXISTS screen_quota_usage (
  user_id    TEXT NOT NULL,
  day        DATE NOT NULL,
  kind       TEXT NOT NULL,
  used       INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, day, kind)
);
