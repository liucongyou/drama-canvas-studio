"""Backend-authoritative media durability and delivery readiness contract."""
from __future__ import annotations

import os
import tempfile
import urllib.parse
from typing import Any

import media_storage
import provider_catalog
import tenant_context


VALID_MODES = {"auto", "local", "object"}


class MediaDeliveryUnavailable(RuntimeError):
    code = "MEDIA_DELIVERY_UNAVAILABLE"


def mode() -> str:
    value = os.environ.get("CANVAS_MEDIA_DELIVERY_MODE", "auto").strip().lower()
    return value if value in VALID_MODES else "auto"


def _local_upload_ready() -> tuple[bool, str]:
    try:
        root = tenant_context.current_upload_dir()
        os.makedirs(root, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=".delivery-probe-",
            dir=root,
            delete=True,
        ):
            pass
        return True, ""
    except Exception as exc:
        return False, str(exc)


def storage_status() -> dict[str, Any]:
    try:
        cfg = provider_catalog.storage_config()
    except Exception as exc:
        return {
            "provider": "",
            "enabled": False,
            "ready": False,
            "verified": False,
            "error": str(exc),
        }
    if not isinstance(cfg, dict):
        cfg = {}
    ready = False
    error = ""
    try:
        ready = bool(media_storage.is_ready(cfg))
    except Exception as exc:
        error = str(exc)
    verified = bool(
        ready
        and (
            cfg.get("last_test_ok") is True
            or (
                isinstance(cfg.get("providers"), dict)
                and isinstance(
                    cfg["providers"].get(
                        str(cfg.get("active_provider") or cfg.get("provider") or "")
                    ),
                    dict,
                )
                and cfg["providers"][
                    str(cfg.get("active_provider") or cfg.get("provider") or "")
                ].get("last_test_ok") is True
            )
        )
    )
    return {
        "provider": str(
            cfg.get("active_provider") or cfg.get("provider") or ""
        ).strip().lower(),
        "enabled": bool(cfg.get("enabled")),
        "ready": ready,
        "verified": verified,
        "error": error,
    }


def readiness() -> dict[str, Any]:
    current_mode = mode()
    local_ok, local_error = _local_upload_ready()
    storage = storage_status()
    requires_object = current_mode == "object"
    object_ok = storage["ready"] and storage["verified"]
    ok = local_ok and (not requires_object or object_ok)
    return {
        "ok": ok,
        "mode": current_mode,
        "local": {
            "ok": local_ok,
            "error": local_error,
        },
        "object": storage,
        "required": requires_object,
    }


def assert_submission_ready(kind: str) -> None:
    if kind not in {"image", "audio", "video"}:
        return
    if mode() != "object":
        return
    status = readiness()
    if status["ok"]:
        return
    object_error = str(
        ((status.get("object") or {}).get("error") or "")
    ).strip()
    message = (
        "当前为多主机对象存储模式，但素材存储尚未通过可用性验证。"
        "本次未提交供应商，不会产生费用。"
    )
    if object_error:
        message += f" 原因：{object_error}"
    raise MediaDeliveryUnavailable(message)


def _local_file_exists(content: str) -> bool:
    raw = str(content or "").strip()
    if not raw.startswith("/uploads/"):
        return False
    name = urllib.parse.unquote(raw[len("/uploads/"):])
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return False
    return os.path.isfile(os.path.join(str(tenant_context.current_upload_dir()), name))


def delivery_state(meta: dict[str, Any] | None) -> str:
    """Classify one node/version without performing network I/O."""
    source = meta if isinstance(meta, dict) else {}
    storage = source.get("storage")
    content = str(
        source.get("content")
        or source.get("url")
        or ""
    ).strip()
    provider_result_url = str(source.get("providerResultUrl") or "").strip()
    has_storage = isinstance(storage, dict) and bool(storage)
    if not content and not provider_result_url and not has_storage:
        # Imported placeholders and never-generated nodes have no media
        # expectation. They must not be reported as broken media.
        return "empty"
    if (
        isinstance(storage, dict)
        and storage.get("status") == "stored"
        and (storage.get("key") or storage.get("url"))
    ):
        return "object"
    if _local_file_exists(content):
        return "local"
    if (
        content.startswith(("http://", "https://"))
        or provider_result_url.startswith(("http://", "https://"))
    ):
        return "temporary"
    return "missing"


def durable(delivery_state_value: str, current_mode: str | None = None) -> bool:
    state = str(delivery_state_value or "")
    selected_mode = current_mode or mode()
    if selected_mode == "object":
        return state == "object"
    return state in {"object", "local"}
