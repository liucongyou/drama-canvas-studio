from __future__ import annotations

import json
import re
from datetime import datetime, timezone


MAX_TELEMETRY_BYTES = 16 * 1024
ALLOWED_FIELDS = {
    "message",
    "stack",
    "code",
    "projectId",
    "episodeId",
    "nodeId",
    "taskId",
    "route",
    "userAgent",
    "releaseVersion",
    "createdAt",
}
FIELD_LIMITS = {
    "message": 2_000,
    "stack": 12_000,
    "code": 160,
    "projectId": 200,
    "episodeId": 80,
    "nodeId": 200,
    "taskId": 200,
    "route": 600,
    "userAgent": 400,
    "releaseVersion": 120,
    "createdAt": 80,
}
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|cookie|token|api[_-]?key|password|secret)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_URL_QUERY = re.compile(r"(https?://[^\s?#]+)\?[^\s)]*")


def _clean_text(value, limit):
    text = str(value or "").strip()
    if not text:
        return ""
    text = _SECRET_ASSIGNMENT.sub(r"\1\2***", text)
    text = _BEARER.sub("Bearer ***", text)
    text = _URL_QUERY.sub(r"\1", text)
    return text[:limit]


def sanitize_frontend_telemetry(payload):
    if not isinstance(payload, dict):
        return {}
    clean = {}
    for key in ALLOWED_FIELDS:
        if key not in payload:
            continue
        value = _clean_text(payload.get(key), FIELD_LIMITS[key])
        if value:
            clean[key] = value
    if not clean.get("message"):
        return {}
    clean.setdefault(
        "createdAt",
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        + "Z",
    )
    return _fit_size(clean)


def _fit_size(payload):
    def encoded_size(value):
        return len(
            json.dumps(value, ensure_ascii=False).encode("utf-8")
        )

    if encoded_size(payload) <= MAX_TELEMETRY_BYTES:
        return payload
    for key in ("stack", "message", "route", "userAgent"):
        value = str(payload.get(key) or "")
        if not value:
            continue
        over = encoded_size(payload) - MAX_TELEMETRY_BYTES
        keep = max(0, len(value) - over - 32)
        payload[key] = value[:keep]
        if encoded_size(payload) <= MAX_TELEMETRY_BYTES:
            break
    return payload
