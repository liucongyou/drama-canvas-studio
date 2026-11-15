from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone


_SECRET_KEY = re.compile(
    r"(?i)(authorization|cookie|token|api[_-]?key|password|secret)"
)
_URL_QUERY = re.compile(r"(https?://[^\s?#]+)\?[^\s)]*")


def log_event(
    service: str,
    event: str,
    *,
    severity: str = "info",
    **fields,
):
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "severity": str(severity or "info"),
        "service": str(service or ""),
        "event": str(event or ""),
        **{
            str(key): _sanitize(value, str(key))
            for key, value in fields.items()
            if value not in (None, "")
        },
    }
    stream = sys.stderr if payload["severity"] in {"error", "critical"} else sys.stdout
    print(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        file=stream,
        flush=True,
    )


def _sanitize(value, key=""):
    if isinstance(value, dict):
        return {
            str(child_key): (
                "***"
                if _SECRET_KEY.search(str(child_key))
                else _sanitize(child_value, str(child_key))
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, key) for item in value]
    text = str(value)
    if _SECRET_KEY.search(str(key)):
        return "***"
    text = _URL_QUERY.sub(r"\1", text)
    return text
