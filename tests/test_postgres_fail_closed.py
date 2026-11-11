from __future__ import annotations

import pytest

import pg_compat


def test_production_requires_postgres_config(monkeypatch):
    monkeypatch.delenv("CANVAS_DB_BACKEND", raising=False)
    monkeypatch.delenv("CANVAS_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CANVAS_ALLOW_SQLITE", raising=False)
    with pytest.raises(RuntimeError, match="必须使用 PostgreSQL"):
        pg_compat.postgres_enabled()


def test_postgres_backend_rejects_missing_url(monkeypatch):
    monkeypatch.setenv("CANVAS_DB_BACKEND", "postgres")
    monkeypatch.delenv("CANVAS_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="必须配置 DATABASE_URL"):
        pg_compat.postgres_enabled()


def test_sqlite_requires_explicit_test_opt_in(monkeypatch):
    monkeypatch.setenv("CANVAS_DB_BACKEND", "sqlite")
    monkeypatch.delenv("CANVAS_ALLOW_SQLITE", raising=False)
    with pytest.raises(RuntimeError, match="生产模式禁止 SQLite"):
        pg_compat.postgres_enabled()
    monkeypatch.setenv("CANVAS_ALLOW_SQLITE", "1")
    assert pg_compat.postgres_enabled() is False
