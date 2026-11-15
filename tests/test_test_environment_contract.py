import os

import pg_compat


def test_root_pytest_uses_explicit_sqlite_test_mode():
    assert os.environ["CANVAS_DB_BACKEND"] == "sqlite"
    assert os.environ["CANVAS_ALLOW_SQLITE"] == "1"
    assert pg_compat.postgres_enabled() is False
