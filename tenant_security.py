# -*- coding: utf-8 -*-
"""Secret encryption and token helpers for tenant isolation."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

import tenant_context


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MASTER_KEY_PATH = DATA_DIR / "master.key"
ENCRYPTED_PREFIX = "enc:v1:"


def _master_key_path() -> Path:
    root = tenant_context.data_root()
    return root / "master.key"


def _decode_master_key(value: str) -> bytes:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("master key is empty")
    if raw.startswith("base64:"):
        encoded = raw[len("base64:"):].strip()
        padded = encoded + "=" * ((4 - len(encoded) % 4) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        if len(decoded) == 32:
            return base64.urlsafe_b64encode(decoded)
        return decoded
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())


def master_key() -> bytes:
    """Load CANVAS_MASTER_KEY or create a local 0600 key file."""
    configured = os.environ.get("CANVAS_MASTER_KEY", "").strip()
    if configured:
        return _decode_master_key(configured)

    path = _master_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        value = path.read_text(encoding="ascii").strip()
        if not value:
            raise RuntimeError(f"主密钥文件为空：{path}")
        return value.encode("ascii")

    value = Fernet.generate_key()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(str(path), flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


def _fernet() -> Fernet:
    try:
        return Fernet(master_key())
    except Exception as exc:
        raise RuntimeError(
            "无法初始化租户密钥加密。请检查 CANVAS_MASTER_KEY 或 data/master.key。"
        ) from exc


def encrypt_secret(value: str) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    if is_encrypted_secret(raw):
        return raw
    token = _fernet().encrypt(raw.encode("utf-8")).decode("ascii")
    return ENCRYPTED_PREFIX + token


def decrypt_secret(value: str) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    if not is_encrypted_secret(raw):
        return raw
    token = raw[len(ENCRYPTED_PREFIX):]
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError(
            "密钥解密失败：CANVAS_MASTER_KEY 与 data/master.key 不匹配，"
            "或密文来自另一台服务器。"
        ) from exc


def is_encrypted_secret(value: str) -> bool:
    return str(value or "").startswith(ENCRYPTED_PREFIX)


def secret_hint(value: str) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    if is_encrypted_secret(raw):
        return "已加密"
    if len(raw) <= 8:
        return "••••••"
    if len(raw) <= 12:
        return f"{raw[:2]}…{raw[-2:]}"
    return f"{raw[:6]}…{raw[-4:]}"


def new_opaque_token(prefix: str) -> str:
    return f"{prefix}{secrets.token_urlsafe(32)}"


def token_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    raw = str(password or "")
    if len(raw) < 8:
        raise ValueError("密码至少需要 8 位")
    salt = os.urandom(16)
    n, r, p = 2 ** 14, 8, 1
    digest = hashlib.scrypt(
        raw.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=32,
    )
    return (
        f"scrypt${n}${r}${p}$"
        f"{base64.urlsafe_b64encode(salt).decode('ascii')}$"
        f"{base64.urlsafe_b64encode(digest).decode('ascii')}"
    )


def verify_password(password: str, encoded: str) -> bool:
    raw = str(password or "")
    value = str(encoded or "")
    try:
        scheme, n_raw, r_raw, p_raw, salt_raw, digest_raw = value.split("$", 5)
        if scheme != "scrypt":
            return False
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        salt = base64.urlsafe_b64decode(salt_raw.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_raw.encode("ascii"))
        actual = hashlib.scrypt(
            raw.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, binascii.Error):
        return False

