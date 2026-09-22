-- Kronos 预测报告的持久化表 · 2026-09-22（M3）
--
-- 为什么本仓库要补这一条：`apps/api/app/routers/chat_kpred.py` 从一开始就往
-- `chat_kpred.reports` 里写报告、并用它做「刷新页面后把 Kronos 图恢复到消息流」
-- （前端 `listSessionKpreds` → `ChatWorkspace` 的 kpred 恢复分支），**但
-- hunter-community 的 db/migrations 里没有建这张表的迁移**（0001–0022 逐个查过）。
--
-- 后果不是报错而是静默降级：写入与读取都在 try/except 里，只打一条 WARNING
--   [chat_kpred] 报告持久化失败(非致命): relation "chat_kpred.reports" does not exist
-- 接口照样 200、`{"items":[],"count":0}`，于是 Kronos 图**在当前页面上有、一刷新就没了**。
-- M3 的 Playwright 用例「刷新后会话恢复」正是这么抓到的（见 M3 报告 §3.2）。
--
-- 字段与类型按 chat_kpred.py 的 INSERT / SELECT 逐列对出来：
--   INSERT … (task_id, user_id, session_id, stock_code, stock_name, days,
--             composite_score, rating, adj_return_pct, content_html, summary_md,
--             question, elapsed_sec)  ON CONFLICT (task_id) DO NOTHING
--   SELECT  … WHERE user_id = %s AND session_id = %s ORDER BY created_at ASC
-- 所以 task_id 是主键、(user_id, session_id, created_at) 是查询路径。
-- 幂等：全部 IF NOT EXISTS，重复执行无副作用。

CREATE SCHEMA IF NOT EXISTS chat_kpred;

CREATE TABLE IF NOT EXISTS chat_kpred.reports (
  task_id         TEXT PRIMARY KEY,               -- kpd_xxxxxxxx，ON CONFLICT 去重靠它
  user_id         TEXT        NOT NULL,
  session_id      TEXT,                           -- 允许为空：能力库/独立页面发起的预测不挂会话
  stock_code      TEXT        NOT NULL,
  stock_name      TEXT        NOT NULL DEFAULT '',
  days            INTEGER     NOT NULL DEFAULT 5,
  composite_score INTEGER,                        -- 综合评分，区间 [-100, 100]
  rating          TEXT,                           -- 偏多 / 中性 / 偏空
  adj_return_pct  NUMERIC(10, 2),                 -- 写入前已 round(…, 2)
  content_html    TEXT,                           -- 完整 HTML 报告，ArtifactPanel 的 iframe 直接吃
  summary_md      TEXT,
  question        TEXT,
  elapsed_sec     INTEGER,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- session 加载时的那条查询：WHERE user_id = ? AND session_id = ? ORDER BY created_at
CREATE INDEX IF NOT EXISTS idx_chat_kpred_reports_user_session
  ON chat_kpred.reports (user_id, session_id, created_at);
