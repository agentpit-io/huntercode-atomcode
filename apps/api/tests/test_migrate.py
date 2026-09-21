"""app/migrate.py 的测试 · 见 docs/setup-wizard/design.md 3.4 与 M1 测试用例 4

分两类：

1. **纯函数测试**（不需要数据库，任何环境都跑）：文件排序、checksum、目录定位、
   advisory lock 锁号的推导。
2. **真库测试**（需要一个能建库的 postgres）：只在设置了环境变量 ``TEST_DATABASE_URL``
   时跑，否则整体 skip。覆盖四种情况：

   | 情况 | 期望 |
   |---|---|
   | 空库 | 全部迁移被执行并记录，关键表建出来 |
   | 老库（8 月状态） | 缺的补齐，已有的不报错 |
   | 重复启动 | 第二遍 0 个待执行，账本行数不变 |
   | 并发 | advisory lock 生效，不重复执行，两个进程都 0 退出 |

   每个用例都会 ``CREATE DATABASE`` 一个临时库、跑完 ``DROP``，互不干扰。
   跑法见 ``tests/manual_migrate_check.sh``（那个脚本会自己起 postgres 容器）。
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import migrate  # noqa: E402

API_DIR = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

# 8 月版本的老库跑到哪个迁移为止（0014 是 2026-08-31 加进来的）
OLD_DB_LAST_MIGRATION = "0014_daily_close_v2.sql"


# ════════════════════════════════════════════════════════════════════════
# 一、纯函数测试（不需要数据库）
# ════════════════════════════════════════════════════════════════════════
def test_advisory_lock_id_可复现():
    """锁号必须能从那句 sha256 重新算出来。

    这条测试的真正作用是**防止有人随手改这个常量**：改了之后，新旧版本的 api
    同时启动时会各自拿到不同的锁、互相不排斥，等于没加锁。
    """
    digest = hashlib.sha256(migrate.ADVISORY_LOCK_NAMESPACE.encode()).digest()
    expected = int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)
    assert migrate.ADVISORY_LOCK_ID == expected
    assert 0 < migrate.ADVISORY_LOCK_ID < (1 << 63)  # 必须是正的 bigint


def test_迁移文件按文件名排序且不重名():
    files = migrate.list_migration_files(migrate.resolve_migrations_dir())
    names = [p.name for p in files]
    assert names, "一个迁移文件都没找到"
    assert names == sorted(names)
    assert len(names) == len(set(names))
    # 账本表自己的迁移必须在（否则 db/migrations 就不是一份完整 schema 了）
    assert "0022_schema_migrations.sql" in names


def test_账本表两处_DDL_保持一致():
    """migrate.py 里的 SCHEMA_MIGRATIONS_DDL 与 0022 迁移文件必须是同一张表。"""
    sql = (migrate.resolve_migrations_dir() / "0022_schema_migrations.sql").read_text(encoding="utf-8")
    normalized = " ".join(sql.split()).lower()
    for fragment in (
        "create table if not exists schema_migrations",
        "filename text primary key",
        "checksum text not null",
        "applied_at timestamptz not null default now()",
    ):
        assert fragment in normalized, f"0022 里缺少 {fragment}"
    inline = " ".join(migrate.SCHEMA_MIGRATIONS_DDL.split()).lower()
    assert "create table if not exists schema_migrations" in inline


def test_checksum_是文件内容的_sha256(tmp_path):
    f = tmp_path / "0001_x.sql"
    f.write_bytes(b"SELECT 1;\n")
    assert migrate.file_checksum(f) == hashlib.sha256(b"SELECT 1;\n").hexdigest()


def test_环境变量指向不存在的目录时报清楚的错(monkeypatch, tmp_path):
    monkeypatch.setenv("HUNTER_MIGRATIONS_DIR", str(tmp_path / "并不存在"))
    with pytest.raises(RuntimeError, match="HUNTER_MIGRATIONS_DIR"):
        migrate.resolve_migrations_dir()


def test_环境变量优先于默认路径(monkeypatch, tmp_path):
    (tmp_path / "0001_x.sql").write_text("SELECT 1;")
    monkeypatch.setenv("HUNTER_MIGRATIONS_DIR", str(tmp_path))
    assert migrate.resolve_migrations_dir() == tmp_path


def test_skip_列表解析(monkeypatch, tmp_path):
    for name in ("0001_a.sql", "0002_b.sql"):
        (tmp_path / name).write_text("SELECT 1;")
    files = migrate.list_migration_files(tmp_path)

    monkeypatch.delenv(migrate.ENV_SKIP, raising=False)
    assert migrate.parse_skip_list(files) == set()

    monkeypatch.setenv(migrate.ENV_SKIP, "0001_a.sql, 0002_b.sql")
    assert migrate.parse_skip_list(files) == {"0001_a.sql", "0002_b.sql"}

    # 写错文件名必须当场报错，而不是默默什么都不跳过
    monkeypatch.setenv(migrate.ENV_SKIP, "0001_a.sql,0009_不存在.sql")
    with pytest.raises(RuntimeError, match="不存在的文件名"):
        migrate.parse_skip_list(files)


def test_checksum_变化只告警不重跑(tmp_path, caplog):
    f = tmp_path / "0001_x.sql"
    f.write_bytes(b"SELECT 1;\n")
    applied = {"0001_x.sql": "旧的对不上的checksum"}
    with caplog.at_level("WARNING", logger="migrate"):
        migrate.warn_checksum_drift(applied, [f])
    assert "迁移文件被改过" in caplog.text
    assert "不会重跑" in caplog.text


# ════════════════════════════════════════════════════════════════════════
# 二、真库测试
# ════════════════════════════════════════════════════════════════════════
pytestmark_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="没有 TEST_DATABASE_URL，跳过真库测试（手工跑法见 tests/manual_migrate_check.sh）",
)


def _with_dbname(url: str, dbname: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + dbname, parts.query, parts.fragment))


@pytest.fixture()
def temp_db():
    """建一个一次性数据库，用完删掉。返回它的 DATABASE_URL。"""
    import psycopg2

    name = "m1b_" + uuid.uuid4().hex[:12]
    admin = psycopg2.connect(TEST_DATABASE_URL)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    admin.close()
    try:
        yield _with_dbname(TEST_DATABASE_URL, name)
    finally:
        admin = psycopg2.connect(TEST_DATABASE_URL)
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (name,)
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


def _env(url: str, **extra: str) -> dict:
    env = dict(os.environ)
    env.pop(migrate.ENV_SKIP, None)
    env["DATABASE_URL"] = url
    env["HUNTER_MIGRATIONS_DIR"] = str(migrate.resolve_migrations_dir())
    env["PYTHONPATH"] = str(API_DIR)
    env.update(extra)
    return env


def _run_migrate(url: str, *args: str, check: bool = True, **extra: str) -> subprocess.CompletedProcess:
    """按 boot.sh 的方式真跑一遍 `python -m app.migrate`（顺带验退出码）。"""
    proc = subprocess.run(
        [sys.executable, "-m", "app.migrate", *args],
        cwd=str(API_DIR), env=_env(url, **extra),
        capture_output=True, text=True, timeout=300,
    )
    if check:
        assert proc.returncode == 0, f"迁移失败（退出码 {proc.returncode}）：\n{proc.stdout}\n{proc.stderr}"
    return proc


def _query(url: str, sql: str, params=None):
    import psycopg2

    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
    finally:
        conn.close()


def _all_migration_names() -> list[str]:
    return [p.name for p in migrate.list_migration_files(migrate.resolve_migrations_dir())]


@pytestmark_db
def test_空库_全部迁移被执行并记录(temp_db):
    files = _all_migration_names()
    proc = _run_migrate(temp_db)

    assert f"本次待执行 {len(files)} 个" in proc.stdout
    recorded = {row[0] for row in _query(temp_db, "SELECT filename FROM schema_migrations")}
    assert recorded == set(files)

    # 关键表 / 关键列都得在：
    # users.compliance_ack_at 正是本机踩过的那个坑（0006 没跑 → compliance-status 500）
    tables = {row[0] for row in _query(
        temp_db, "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
    for expect in ("users", "stocks", "klines", "backtest_result", "backtest_trade",
                   "screen_quota_usage", "schema_migrations"):
        assert expect in tables, f"空库跑完还是缺表 {expect}"
    assert _query(temp_db, """
        SELECT 1 FROM information_schema.columns
        WHERE table_name='users' AND column_name='compliance_ack_at'
    """), "0006 的 compliance_ack_at 列没补上"
    # 视图也要建出来（0010/0014 依赖 klines）
    assert _query(temp_db, "SELECT 1 FROM information_schema.views WHERE table_name='daily_close'")


@pytestmark_db
def test_老库_缺的补齐已有的不报错(temp_db):
    """模拟真实的 8 月老库：initdb 灌过 0001~0014（其中几个当场失败被静默吞掉），
    api 又跑过 init_db()。这正是「compliance-status 500」那台机器的状态。
    """
    import psycopg2

    files = _all_migration_names()
    old_files = [n for n in files if n <= OLD_DB_LAST_MIGRATION]
    migrations_dir = migrate.resolve_migrations_dir()

    # ① 复刻 initdb 的行为：psql 不带 ON_ERROR_STOP，一个文件失败继续下一个
    failed = []
    conn = psycopg2.connect(temp_db)
    conn.autocommit = True
    for name in old_files:
        try:
            with conn.cursor() as cur:
                cur.execute((migrations_dir / name).read_text(encoding="utf-8"))
        except psycopg2.Error as exc:
            failed.append((name, str(exc).splitlines()[0]))
    conn.close()
    # 这几个当年就是失败的（基础表还不存在）——测试固定住这个事实
    assert [n for n, _ in failed], "老库构造没复现出当年的失败，说明前提变了，请检查"

    # ② 老版本的 api 起来了，跑了 init_db()
    subprocess.run(
        [sys.executable, "-c",
         "import asyncio;from app.services.database import init_db;asyncio.run(init_db())"],
        cwd=str(API_DIR), env=_env(temp_db), capture_output=True, text=True, timeout=300, check=True,
    )
    # 老库此刻没有账本表，也没有 0015 加的列
    assert not _query(temp_db, "SELECT to_regclass('schema_migrations')")[0][0]
    assert not _query(temp_db, """
        SELECT 1 FROM information_schema.columns
        WHERE table_name='stocks' AND column_name='avg_cost'
    """), "老库构造有误：avg_cost 不该存在"

    # ③ 升级到新版本 api → 自动补齐
    proc = _run_migrate(temp_db)
    assert f"本次待执行 {len(files)} 个" in proc.stdout  # 老库没有账本，全部重跑一遍（都幂等）
    recorded = {row[0] for row in _query(temp_db, "SELECT filename FROM schema_migrations")}
    assert recorded == set(files)
    assert _query(temp_db, """
        SELECT 1 FROM information_schema.columns
        WHERE table_name='stocks' AND column_name='avg_cost'
    """), "0015 的 avg_cost 没补上"
    assert _query(temp_db, "SELECT to_regclass('screen_quota_usage')")[0][0], "0021 没补上"


@pytestmark_db
def test_重复启动_第二遍零个待执行(temp_db):
    files = _all_migration_names()
    _run_migrate(temp_db)
    rows_1 = _query(temp_db, "SELECT count(*) FROM schema_migrations")[0][0]
    applied_at_1 = _query(temp_db, "SELECT max(applied_at) FROM schema_migrations")[0][0]

    proc2 = _run_migrate(temp_db)
    assert "本次待执行 0 个" in proc2.stdout
    assert "WARNING" not in proc2.stdout, f"第二遍不该有告警：\n{proc2.stdout}"

    rows_2 = _query(temp_db, "SELECT count(*) FROM schema_migrations")[0][0]
    assert rows_1 == rows_2 == len(files)
    # applied_at 也不该变（说明确实没重跑、没重写记录）
    assert _query(temp_db, "SELECT max(applied_at) FROM schema_migrations")[0][0] == applied_at_1


@pytestmark_db
def test_并发_两个进程同时跑不重复执行(temp_db):
    """advisory lock 生效：先到的跑完全部，后到的等锁、拿到后发现 0 个待执行。"""
    files = _all_migration_names()
    procs = [
        subprocess.Popen([sys.executable, "-m", "app.migrate"], cwd=str(API_DIR), env=_env(temp_db),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(2)
    ]
    outs = []
    for p in procs:
        stdout, stderr = p.communicate(timeout=300)
        outs.append(stdout + stderr)
        assert p.returncode == 0, f"并发时有进程失败（退出码 {p.returncode}）：\n{stdout}\n{stderr}"

    did_all = sum(1 for o in outs if f"本次待执行 {len(files)} 个" in o)
    did_none = sum(1 for o in outs if "本次待执行 0 个" in o)
    assert (did_all, did_none) == (1, 1), (
        "两个进程应该一个全跑、一个 0 个待执行，实际输出：\n" + "\n---\n".join(outs))

    # 账本不能有重复行（filename 是主键，重复会直接违约）
    assert _query(temp_db, "SELECT count(*) FROM schema_migrations")[0][0] == len(files)


@pytestmark_db
def test_手工补跑过的库_0010_可重复执行(temp_db):
    """回归测试 · 守住 0010 的可重复执行性。

    真实场景:有人按 0013/0014 文件头写的「apply(用户手动)」手工补跑过迁移,
    之后升级到带账本的版本 —— 账本是空的,migrate 会从 0001 重跑一遍。
    0010 与 0014 定义同一个视图 daily_close,0014 多一列 adv_20d,而
    `CREATE OR REPLACE VIEW` **不许减列**,所以重跑 0010 会报
    `cannot drop columns from view`,api 直接起不来。
    **演示站 fin-r1 正是这种库。**

    M1 合并时在 0010 开头加了 `DROP VIEW IF EXISTS daily_close;` 根治
    (当时 schema_migrations 刚引入、世上还没有任何库记录过它的 checksum,
    改的代价为零)。这条测试守住那句 DROP 不被人顺手删掉 ——
    删掉之后本地空库测试全绿,只有老库升级时才炸。
    """
    import subprocess as sp

    migrations_dir = migrate.resolve_migrations_dir()
    # 造现场:init_db + 手工跑过 0010、0014,但没有账本
    sp.run([sys.executable, "-c",
            "import asyncio;from app.services.database import init_db;asyncio.run(init_db())"],
           cwd=str(API_DIR), env=_env(temp_db), capture_output=True, text=True, timeout=300, check=True)
    import psycopg2
    conn = psycopg2.connect(temp_db)
    conn.autocommit = True
    for name in ("0010_daily_close_view.sql", "0014_daily_close_v2.sql"):
        with conn.cursor() as cur:
            cur.execute((migrations_dir / name).read_text(encoding="utf-8"))
    conn.close()
    # 现在库里是 0014 那个 9 列的视图
    cols = _query(temp_db, "SELECT count(*) FROM information_schema.columns "
                           "WHERE table_name='daily_close'")[0][0]
    assert cols == 9, f"造现场失败:daily_close 应有 9 列,实际 {cols}"

    # ① 升级必须成功(修复前这里会以 `cannot drop columns from view` 失败)
    proc = _run_migrate(temp_db, check=False)
    assert proc.returncode == 0, (proc.stdout + proc.stderr)[-2000:]
    out = proc.stdout + proc.stderr
    assert "cannot drop columns from view" not in out

    # ② 账本齐全,而且 0014 的 adv_20d 还在(0010 重跑之后 0014 又跑了一遍)
    assert _query(temp_db, "SELECT count(*) FROM schema_migrations")[0][0] == len(_all_migration_names())
    assert _query(temp_db, "SELECT count(*) FROM information_schema.columns "
                           "WHERE table_name='daily_close' AND column_name='adv_20d'")[0][0] == 1


@pytestmark_db
def test_0010_文件本身带着那句_DROP(temp_db):
    """不连库也能守住的一条:0010 的源文件里必须有 DROP VIEW。

    上面那条要真库才跑得到;这条是纯文本检查,任何环境都会红,
    删掉那句 DROP 时第一时间就能看见。
    """
    txt = (migrate.resolve_migrations_dir() / "0010_daily_close_view.sql").read_text(encoding="utf-8")
    assert "DROP VIEW IF EXISTS daily_close" in txt, (
        "0010 必须先 DROP 再建视图 —— 0014 用 CREATE OR REPLACE 给同一个视图加了一列,"
        "而 OR REPLACE 不许减列,老库升级重跑 0010 会直接失败。"
    )
    assert "CASCADE" not in txt.upper().split("DROP VIEW IF EXISTS DAILY_CLOSE")[1][:40], (
        "不要给这句 DROP 加 CASCADE —— 目前没有任何 SQL 对象依赖 daily_close,"
        "将来真有依赖时应该大声失败,而不是静默把依赖一起删掉。"
    )


@pytestmark_db
def test_dry_run_不改数据库(temp_db):
    files = _all_migration_names()
    proc = _run_migrate(temp_db, "--dry-run")
    assert f"本次待执行 {len(files)} 个（--dry-run，不执行）" in proc.stdout
    assert _query(temp_db, "SELECT to_regclass('schema_migrations')")[0][0] is None
    assert _query(temp_db, "SELECT to_regclass('users')")[0][0] is None


@pytestmark_db
def test_文件被改过只告警不重跑(temp_db, tmp_path):
    """把一个已应用的迁移内容改掉 → WARNING，但不重跑、账本不变。"""
    import shutil

    src = migrate.resolve_migrations_dir()
    work = tmp_path / "migrations"
    shutil.copytree(src, work)

    env = dict(os.environ)
    env["DATABASE_URL"] = temp_db
    env["HUNTER_MIGRATIONS_DIR"] = str(work)
    env["PYTHONPATH"] = str(API_DIR)

    def run() -> subprocess.CompletedProcess:
        p = subprocess.run([sys.executable, "-m", "app.migrate"], cwd=str(API_DIR), env=env,
                           capture_output=True, text=True, timeout=300)
        assert p.returncode == 0, p.stdout + p.stderr
        return p

    run()
    before = _query(temp_db, "SELECT checksum, applied_at FROM schema_migrations "
                             "WHERE filename='0021_screen_quota_usage.sql'")[0]

    target = work / "0021_screen_quota_usage.sql"
    target.write_text(target.read_text(encoding="utf-8") + "\n-- 有人手工改了这个文件\n", encoding="utf-8")

    proc = run()
    assert "迁移文件被改过：0021_screen_quota_usage.sql" in proc.stdout
    assert "本次待执行 0 个" in proc.stdout
    after = _query(temp_db, "SELECT checksum, applied_at FROM schema_migrations "
                            "WHERE filename='0021_screen_quota_usage.sql'")[0]
    assert after == before, "checksum 变化不该导致账本被改写"
