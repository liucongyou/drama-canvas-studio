"""Keep test writes out of the developer's real database and config."""
from __future__ import annotations

import atexit
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

import canvas_store
import tenant_context


_TEST_ROOT = Path(tempfile.mkdtemp(prefix="drama-canvas-tests-"))
canvas_store.DB_PATH = str(_TEST_ROOT / "canvas.db")
canvas_store._SCHEMA_READY = False
_BASE_CONFIG = _TEST_ROOT / "config.json"
_LOCAL_CONFIG = _TEST_ROOT / "config.local.json"
_LOCAL_CONFIG_DATA = {
    "providers": [
        {"id": "ark", "api_key": "test-ark-key"},
        {"id": "teamorouter", "api_key": "test-router-key"},
        {"id": "agnes-ai", "api_key": "test-agnes-key"},
        {"id": "4stoken", "api_key": "test-4stoken-key"},
        {"id": "aliyun_bailian", "api_key": "test-dashscope-key"},
    ],
}
_ORIGINAL_DATA_ROOT = tenant_context.data_root
_ORIGINAL_TENANT_BASE = tenant_context.BASE_DIR
_TENANT_ENV_KEYS = (
    "CANVAS_MULTI_TENANT",
    "CANVAS_TENANT_DATA_ROOT",
    "CANVAS_BASE_DOMAIN",
    "CANVAS_MASTER_KEY",
    "CANVAS_COOKIE_SECURE",
    "CANVAS_OWNER_SLUG",
)


def _reset_config_files() -> None:
    _BASE_CONFIG.write_text("{}", encoding="utf-8")
    _LOCAL_CONFIG.write_text(
        json.dumps(_LOCAL_CONFIG_DATA),
        encoding="utf-8",
    )


_reset_config_files()
os.environ["CANVAS_CONFIG_PATH"] = str(_BASE_CONFIG)
os.environ["CANVAS_LOCAL_CONFIG_PATH"] = str(_LOCAL_CONFIG)


def _reset_mutable_state() -> None:
    _reset_config_files()
    tenant_context.data_root = _ORIGINAL_DATA_ROOT
    tenant_context.BASE_DIR = _ORIGINAL_TENANT_BASE
    canvas_store.DB_PATH = str(_TEST_ROOT / "canvas.db")
    canvas_store._SCHEMA_READY = False
    canvas_store._SCHEMA_READY_PATHS.clear()
    for key in _TENANT_ENV_KEYS:
        os.environ.pop(key, None)
    tenant_context.set_tenant(None)
    try:
        import provider_catalog

        provider_catalog.CONFIG_PATH = str(_BASE_CONFIG)
        provider_catalog.LOCAL_CONFIG_PATH = str(_LOCAL_CONFIG)
        provider_catalog._MODEL_PROFILES = None
    except Exception:
        pass
    try:
        import tenant_registry

        tenant_registry._SCHEMA_READY.clear()
    except Exception:
        pass
    try:
        import server

        with server.GENERATION_QUEUE_CONDITION:
            server.GENERATION_QUEUE_ACTIVE_GLOBAL = 0
            server.GENERATION_QUEUE_ACTIVE_PROVIDERS.clear()
            server.GENERATION_QUEUE_ACTIVE_TENANTS.clear()
            server.GENERATION_QUEUE_ACTIVE_MODELS.clear()
            server.GENERATION_QUEUE_PROVIDER_COOLDOWN_UNTIL.clear()
        server.RESUMED_VIDEO_TASKS.clear()
    except Exception:
        pass


_reset_mutable_state()


@pytest.fixture(autouse=True)
def _isolate_mutable_globals():
    _reset_mutable_state()
    yield
    _reset_mutable_state()


def _cleanup() -> None:
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)


atexit.register(_cleanup)
