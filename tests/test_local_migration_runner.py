from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch


def _load_runner():
    path = Path(__file__).resolve().parent.parent / "scripts" / "run-local-migrations.py"
    spec = importlib.util.spec_from_file_location("local_migration_runner", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_second_migration_run_changes_zero_rows():
    runner = _load_runner()
    state = {"runs": 0}

    def migrate():
        state["runs"] += 1
        return 3 if state["runs"] == 1 else 0

    tenant = {"id": "tenant-owner", "slug": "owner"}
    with (
        patch.object(runner.tenant_registry, "list_tenants", return_value=[tenant]),
        patch.object(runner.tenant_context, "use_tenant"),
    ):
        runner.tenant_context.use_tenant.return_value.__enter__.return_value = None
        runner.tenant_context.use_tenant.return_value.__exit__.return_value = None
        first = runner.run_local_migrations(
            "postgresql://postgres@127.0.0.1:55432/test",
            step_steps=(("migrate", migrate),),
        )
        second = runner.run_local_migrations(
            "postgresql://postgres@127.0.0.1:55432/test",
            step_steps=(("migrate", migrate),),
        )

    assert first["changedRows"] == 3
    assert first["applied"] is True
    assert second["changedRows"] == 0
    assert second["applied"] is False
