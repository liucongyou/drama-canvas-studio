import pytest

from app.context import sign_request, verify_request


def test_signature_round_trip() -> None:
    signature = sign_request(
        "secret",
        "POST",
        "/api/agent/runs",
        "1700000000",
        "t1",
        "a1",
        b"{}",
    )
    verify_request(
        "secret",
        "POST",
        "/api/agent/runs",
        "1700000000",
        "t1",
        "a1",
        b"{}",
        signature,
        now=1700000000,
    )


def test_signature_rejects_tampering() -> None:
    signature = sign_request(
        "secret",
        "POST",
        "/api/agent/runs",
        "1700000000",
        "t1",
        "a1",
        b"{}",
    )
    with pytest.raises(ValueError):
        verify_request(
            "secret",
            "POST",
            "/api/agent/runs",
            "1700000000",
            "t1",
            "a2",
            b"{}",
            signature,
            now=1700000000,
        )


def test_signature_rejects_expired_request() -> None:
    signature = sign_request(
        "secret",
        "GET",
        "/api/agent/models",
        "1700000000",
        "t1",
        "a1",
        b"",
    )
    with pytest.raises(ValueError, match="过期"):
        verify_request(
            "secret",
            "GET",
            "/api/agent/models",
            "1700000000",
            "t1",
            "a1",
            b"",
            signature,
            now=1700000001,
            window_seconds=0,
        )
