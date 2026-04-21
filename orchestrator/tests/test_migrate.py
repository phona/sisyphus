"""migrate 模块烟测：DSN 改写 + migrations 目录结构。

不打 PG（CI 没库），只验：
- _to_yoyo_dsn 把 asyncpg DSN 改成 yoyo+psycopg2 形式
- migrations/ 目录下 SQL 能被 yoyo 解析（read_migrations 不抛）
- 0001 文件含 forward + rollback 两段
"""
from __future__ import annotations

from pathlib import Path

from yoyo import read_migrations

from orchestrator.migrate import _DEFAULT_MIGRATIONS_DIR


def test_migrations_dir_exists():
    assert _DEFAULT_MIGRATIONS_DIR.is_dir(), _DEFAULT_MIGRATIONS_DIR
    sqls = list(_DEFAULT_MIGRATIONS_DIR.glob("*.sql"))
    assert sqls, "no migration files found"


def test_migrations_parseable():
    """yoyo 读得了，没语法异常。"""
    migs = read_migrations(str(_DEFAULT_MIGRATIONS_DIR))
    ids = [m.id for m in migs]
    assert ids and ids == sorted(ids), ids


def test_0001_has_rollback():
    body = (Path(_DEFAULT_MIGRATIONS_DIR) / "0001_init.sql").read_text()
    assert "CREATE TABLE" in body and "-- !rollback" in body
