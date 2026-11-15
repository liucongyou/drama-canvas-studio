import atexit
import base64
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest


TEST_ROOT = Path(tempfile.mkdtemp(prefix="drama-canvas-pytest-"))
TEST_MASTER_KEY = "base64:" + base64.urlsafe_b64encode(
    b"drama-canvas-test-master-key-001"
).decode("ascii")
TEST_LOCAL_CONFIG_PATH = TEST_ROOT / "config.local.json"

os.environ["CANVAS_DB_BACKEND"] = "sqlite"
os.environ["CANVAS_ALLOW_SQLITE"] = "1"
os.environ["CANVAS_MULTI_TENANT"] = "0"
os.environ["CANVAS_TENANT_DATA_ROOT"] = str(TEST_ROOT)
os.environ["CANVAS_UPLOAD_DIR"] = str(TEST_ROOT / "uploads")
os.environ["CANVAS_LOCAL_CONFIG_PATH"] = str(TEST_LOCAL_CONFIG_PATH)
os.environ["CANVAS_MASTER_KEY"] = TEST_MASTER_KEY

_repo_config = json.loads(
    (Path(__file__).resolve().parent / "config.json").read_text(encoding="utf-8")
)
for _provider in _repo_config.get("providers", []):
    if isinstance(_provider, dict):
        _provider["api_key"] = "test-key"
for _model in _repo_config.get("models", []):
    if not isinstance(_model, dict):
        continue
    if _model.get("id") == "cosyvoice-v3-5-flash":
        _model.setdefault("defaultParams", {})["outputFormat"] = "mp3"
    if _model.get("id") == "doubao-seedance-2-0":
        _model["pricing"] = {
            "video": {
                "rates": {
                    "480P": 0.5,
                    "720P": 1.239,
                    "1080P": 2.478,
                }
            }
        }


def _reset_local_config():
    TEST_LOCAL_CONFIG_PATH.write_text(
        json.dumps(_repo_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


_reset_local_config()

import canvas_store  # noqa: E402
import tenant_context  # noqa: E402
import tenant_registry  # noqa: E402


canvas_store.DB_PATH = str(TEST_ROOT / "canvas.db")


@pytest.fixture(autouse=True)
def reset_isolated_test_state():
    os.environ["CANVAS_DB_BACKEND"] = "sqlite"
    os.environ["CANVAS_ALLOW_SQLITE"] = "1"
    os.environ["CANVAS_MULTI_TENANT"] = "0"
    os.environ["CANVAS_TENANT_DATA_ROOT"] = str(TEST_ROOT)
    os.environ["CANVAS_UPLOAD_DIR"] = str(TEST_ROOT / "uploads")
    os.environ["CANVAS_LOCAL_CONFIG_PATH"] = str(TEST_LOCAL_CONFIG_PATH)
    os.environ["CANVAS_MASTER_KEY"] = TEST_MASTER_KEY
    _reset_local_config()
    tenant_context.set_tenant(None)
    canvas_store._SCHEMA_READY = False
    canvas_store._SCHEMA_READY_PATHS.clear()
    tenant_registry._SCHEMA_READY.clear()
    try:
        yield
    finally:
        tenant_context.set_tenant(None)
        _reset_local_config()


@atexit.register
def _cleanup_test_root():
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
