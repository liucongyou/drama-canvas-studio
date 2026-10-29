# -*- coding: utf-8 -*-
"""Control-plane registry for tenants, one-time invites and sessions."""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import tenant_context
import tenant_security


_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY: set[str] = set()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    current = value or _now()
    return current.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def _parse_iso(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _db_path() -> Path:
    return tenant_context.data_root() / "control.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA foreign_keys=ON")
    path_key = str(path)
    if path_key not in _SCHEMA_READY:
        with _SCHEMA_LOCK:
            if path_key not in _SCHEMA_READY:
                _create_schema(con)
                _SCHEMA_READY.add(path_key)
    return con


def _create_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS tenants(
            id TEXT PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            name TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_seen_at TEXT,
            legacy_source TEXT
        );

        CREATE TABLE IF NOT EXISTS invites(
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            revoked_at TEXT,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_invites_tenant
            ON invites(tenant_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS accounts(
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            status TEXT NOT NULL DEFAULT 'active',
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            locked_until TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_login_at TEXT,
            password_changed_at TEXT NOT NULL,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_accounts_tenant
            ON accounts(tenant_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS sessions(
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            account_id TEXT,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            revoked_at TEXT,
            user_agent TEXT,
            ip TEXT,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_tenant
            ON sessions(tenant_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS tenant_storage_bindings(
            tenant_id TEXT PRIMARY KEY,
            bucket TEXT NOT NULL,
            region TEXT NOT NULL,
            prefix TEXT NOT NULL,
            access_key_fingerprint TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_storage_bucket
            ON tenant_storage_bindings(bucket, region);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_storage_access_key
            ON tenant_storage_bindings(access_key_fingerprint);
        """
    )
    session_columns = {
        row[1] for row in con.execute("PRAGMA table_info(sessions)")
    }
    if "account_id" not in session_columns:
        con.execute("ALTER TABLE sessions ADD COLUMN account_id TEXT")
    account_columns = {
        row[1] for row in con.execute("PRAGMA table_info(accounts)")
    }
    if "role" not in account_columns:
        con.execute("ALTER TABLE accounts ADD COLUMN role TEXT NOT NULL DEFAULT 'member'")
    con.commit()


def _tenant_dict(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "slug": row["slug"],
        "name": row["name"] or row["slug"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_seen_at": row["last_seen_at"] or "",
        "legacy_source": row["legacy_source"] or "",
    }


def normalize_username(value: str) -> str:
    username = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", username):
        raise ValueError("账号只能使用 3~64 位小写字母、数字、点、下划线或连字符")
    return username


def _account_dict(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "username": row["username"],
        "role": row["role"] or "member",
        "status": row["status"],
        "failed_attempts": int(row["failed_attempts"] or 0),
        "locked_until": row["locked_until"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_login_at": row["last_login_at"] or "",
        "password_changed_at": row["password_changed_at"],
    }


def create_account(
    tenant_id: str,
    username: str,
    password: str,
    *,
    role: str = "member",
) -> dict[str, Any]:
    tenant = get_tenant_by_id(tenant_id)
    if not tenant:
        raise ValueError("租户不存在")
    if tenant["status"] != "active":
        raise ValueError("租户已停用")
    normalized = normalize_username(username)
    now = _iso()
    con = _connect()
    try:
        con.execute(
            "INSERT INTO accounts("
            "id,tenant_id,username,password_hash,role,status,failed_attempts,locked_until,"
            "created_at,updated_at,last_login_at,password_changed_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                tenant_security.new_opaque_token("acc_"),
                tenant_id,
                normalized,
                tenant_security.hash_password(password),
                "admin" if role == "admin" else "member",
                "active",
                0,
                "",
                now,
                now,
                "",
                now,
            ),
        )
        con.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"账号已存在：{normalized}") from exc
    finally:
        con.close()
    account = get_account_by_username(normalized)
    if not account:
        raise RuntimeError("账号创建后读取失败")
    return account


def ensure_owner_admin(username: str = "admin") -> dict[str, Any] | None:
    """Promote the configured owner account once, without opening public signup."""
    owner = get_tenant_by_id("owner")
    if not owner:
        return None
    account = get_account_by_username(username)
    if not account or account["tenant_id"] != owner["id"]:
        return None
    con = _connect()
    con.execute(
        "UPDATE accounts SET role='admin',updated_at=? WHERE id=?",
        (_iso(), account["id"]),
    )
    con.commit()
    con.close()
    return get_account_by_id(account["id"])


def get_account_by_username(username: str) -> dict[str, Any] | None:
    normalized = normalize_username(username)
    con = _connect()
    row = con.execute(
        "SELECT * FROM accounts WHERE username=?",
        (normalized,),
    ).fetchone()
    con.close()
    return _account_dict(row)


def get_account_by_id(account_id: str) -> dict[str, Any] | None:
    con = _connect()
    row = con.execute("SELECT * FROM accounts WHERE id=?", (str(account_id),)).fetchone()
    con.close()
    return _account_dict(row)


def list_accounts(*, tenant_id: str = "") -> list[dict[str, Any]]:
    con = _connect()
    if tenant_id:
        rows = con.execute(
            "SELECT * FROM accounts WHERE tenant_id=? ORDER BY created_at ASC",
            (tenant_id,),
        ).fetchall()
    else:
        rows = con.execute("SELECT * FROM accounts ORDER BY created_at ASC").fetchall()
    con.close()
    return [_account_dict(row) for row in rows if row is not None]


def set_account_password(account_id: str, password: str) -> dict[str, Any]:
    account = get_account_by_id(account_id)
    if not account:
        raise ValueError("账号不存在")
    now = _iso()
    con = _connect()
    con.execute(
        "UPDATE accounts SET password_hash=?,password_changed_at=?,updated_at=?,"
        "failed_attempts=0,locked_until='' WHERE id=?",
        (tenant_security.hash_password(password), now, now, account_id),
    )
    con.execute("UPDATE sessions SET revoked_at=? WHERE account_id=? AND revoked_at=''", (now, account_id))
    con.commit()
    con.close()
    updated = get_account_by_id(account_id)
    if not updated:
        raise RuntimeError("账号更新后读取失败")
    return updated


def set_account_status(account_id: str, status: str) -> dict[str, Any]:
    normalized = str(status or "").strip().lower()
    if normalized not in {"active", "disabled"}:
        raise ValueError("账号状态只能是 active 或 disabled")
    account = get_account_by_id(account_id)
    if not account:
        raise ValueError("账号不存在")
    con = _connect()
    now = _iso()
    con.execute(
        "UPDATE accounts SET status=?,updated_at=?,failed_attempts=?,locked_until=? WHERE id=?",
        (
            normalized,
            now,
            0 if normalized == "active" else account["failed_attempts"],
            "" if normalized == "active" else account["locked_until"],
            account_id,
        ),
    )
    if normalized == "disabled":
        con.execute("UPDATE sessions SET revoked_at=? WHERE account_id=? AND revoked_at=''", (now, account_id))
    con.commit()
    con.close()
    updated = get_account_by_id(account_id)
    if not updated:
        raise RuntimeError("账号状态更新后读取失败")
    return updated


def authenticate_account(username: str, password: str) -> dict[str, Any] | None:
    normalized = normalize_username(username)
    con = _connect()
    row = con.execute("SELECT * FROM accounts WHERE username=?", (normalized,)).fetchone()
    if not row:
        con.close()
        return None
    account = _account_dict(row)
    if not account or account["status"] != "active":
        con.close()
        return None
    now = _now()
    locked_until = _parse_iso(account["locked_until"])
    if locked_until and locked_until > now:
        con.close()
        return None
    if not tenant_security.verify_password(password, str(row["password_hash"] or "")):
        failed = int(account["failed_attempts"] or 0) + 1
        lock_until = ""
        if failed >= 5:
            lock_until = _iso(now + timedelta(minutes=15))
            failed = 0
        con.execute(
            "UPDATE accounts SET failed_attempts=?,locked_until=?,updated_at=? WHERE id=?",
            (failed, lock_until, _iso(now), account["id"]),
        )
        con.commit()
        con.close()
        return None
    con.execute(
        "UPDATE accounts SET failed_attempts=0,locked_until='',last_login_at=?,updated_at=? WHERE id=?",
        (_iso(now), _iso(now), account["id"]),
    )
    con.commit()
    con.close()
    account["failed_attempts"] = 0
    account["locked_until"] = ""
    account["last_login_at"] = _iso(now)
    return account


def verify_account_password(account_id: str, password: str) -> bool:
    con = _connect()
    row = con.execute(
        "SELECT password_hash,status FROM accounts WHERE id=?",
        (str(account_id),),
    ).fetchone()
    con.close()
    if not row or row["status"] != "active":
        return False
    return tenant_security.verify_password(password, str(row["password_hash"] or ""))


def create_tenant(
    slug: str,
    *,
    name: str = "",
    tenant_id: str = "",
    legacy_source: str = "",
) -> dict[str, Any]:
    safe_slug = tenant_context.normalize_slug(slug)
    safe_id = str(tenant_id or safe_slug).strip()
    # Validate the ID through the same path builder used by request routing.
    tenant_context.make_context(safe_id, safe_slug)
    now = _iso()
    con = _connect()
    try:
        con.execute(
            "INSERT INTO tenants(id,slug,name,status,created_at,updated_at,last_seen_at,legacy_source)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (safe_id, safe_slug, str(name or safe_slug)[:120], "active", now, now, "", legacy_source),
        )
        con.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"租户 slug 或 ID 已存在：{safe_slug}") from exc
    finally:
        con.close()
    tenant = get_tenant_by_slug(safe_slug)
    if not tenant:
        raise RuntimeError("租户创建后读取失败")
    return tenant


def get_tenant_by_slug(slug: str) -> dict[str, Any] | None:
    safe_slug = tenant_context.normalize_slug(slug)
    con = _connect()
    row = con.execute("SELECT * FROM tenants WHERE slug=?", (safe_slug,)).fetchone()
    con.close()
    return _tenant_dict(row)


def get_tenant_by_id(tenant_id: str) -> dict[str, Any] | None:
    con = _connect()
    row = con.execute("SELECT * FROM tenants WHERE id=?", (str(tenant_id),)).fetchone()
    con.close()
    return _tenant_dict(row)


def list_tenants(*, include_disabled: bool = True) -> list[dict[str, Any]]:
    con = _connect()
    if include_disabled:
        rows = con.execute("SELECT * FROM tenants ORDER BY created_at ASC").fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM tenants WHERE status='active' ORDER BY created_at ASC"
        ).fetchall()
    con.close()
    return [_tenant_dict(row) for row in rows if row is not None]


def set_tenant_status(slug: str, status: str) -> dict[str, Any]:
    normalized = str(status or "").strip().lower()
    if normalized not in {"active", "disabled"}:
        raise ValueError("租户状态只能是 active 或 disabled")
    tenant = get_tenant_by_slug(slug)
    if not tenant:
        raise ValueError("租户不存在")
    con = _connect()
    con.execute(
        "UPDATE tenants SET status=?,updated_at=? WHERE id=?",
        (normalized, _iso(), tenant["id"]),
    )
    con.commit()
    con.close()
    updated = get_tenant_by_id(tenant["id"])
    if not updated:
        raise RuntimeError("租户状态更新后读取失败")
    return updated


def touch_tenant(tenant_id: str) -> None:
    con = _connect()
    con.execute(
        "UPDATE tenants SET last_seen_at=?,updated_at=? WHERE id=?",
        (_iso(), _iso(), str(tenant_id)),
    )
    con.commit()
    con.close()


def create_invite(
    tenant_id: str,
    *,
    ttl_seconds: int = 24 * 3600,
) -> dict[str, Any]:
    tenant = get_tenant_by_id(tenant_id)
    if not tenant:
        raise ValueError("租户不存在")
    if tenant["status"] != "active":
        raise ValueError("租户已停用，不能生成邀请")
    ttl = max(60, min(int(ttl_seconds or 24 * 3600), 30 * 24 * 3600))
    raw_token = tenant_security.new_opaque_token("inv_")
    invite_id = tenant_security.new_opaque_token("iv_")
    now = _now()
    con = _connect()
    con.execute(
        "INSERT INTO invites(id,tenant_id,token_hash,created_at,expires_at,used_at,revoked_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (
            invite_id,
            tenant_id,
            tenant_security.token_hash(raw_token),
            _iso(now),
            _iso(now + timedelta(seconds=ttl)),
            "",
            "",
        ),
    )
    con.commit()
    con.close()
    return {
        "id": invite_id,
        "tenant_id": tenant_id,
        "slug": tenant["slug"],
        "token": raw_token,
        "expires_at": _iso(now + timedelta(seconds=ttl)),
    }


def revoke_invite(invite_id: str) -> bool:
    con = _connect()
    cursor = con.execute(
        "UPDATE invites SET revoked_at=? WHERE id=? AND used_at='' AND revoked_at=''",
        (_iso(), str(invite_id)),
    )
    con.commit()
    changed = cursor.rowcount > 0
    con.close()
    return changed


def revoke_tenant_invites(tenant_id: str) -> int:
    con = _connect()
    cursor = con.execute(
        "UPDATE invites SET revoked_at=? WHERE tenant_id=? AND used_at='' AND revoked_at=''",
        (_iso(), str(tenant_id)),
    )
    con.commit()
    changed = cursor.rowcount
    con.close()
    return changed


def rotate_invite(tenant_id: str, *, ttl_seconds: int = 24 * 3600) -> dict[str, Any]:
    revoke_tenant_invites(tenant_id)
    return create_invite(tenant_id, ttl_seconds=ttl_seconds)


def consume_invite(slug: str, raw_token: str) -> dict[str, Any] | None:
    safe_slug = tenant_context.normalize_slug(slug)
    digest = tenant_security.token_hash(raw_token)
    now = _iso()
    con = _connect()
    try:
        row = con.execute(
            "SELECT i.*, t.status AS tenant_status "
            "FROM invites i JOIN tenants t ON t.id=i.tenant_id "
            "WHERE t.slug=? AND i.token_hash=? "
            "AND i.used_at='' AND i.revoked_at='' AND i.expires_at>? LIMIT 1",
            (safe_slug, digest, now),
        ).fetchone()
        if not row or row["tenant_status"] != "active":
            return None
        cursor = con.execute(
            "UPDATE invites SET used_at=? WHERE id=? AND used_at='' AND revoked_at=''",
            (now, row["id"]),
        )
        if cursor.rowcount != 1:
            con.rollback()
            return None
        con.commit()
        return get_tenant_by_id(row["tenant_id"])
    finally:
        con.close()


def create_session(
    tenant_id: str,
    *,
    account_id: str = "",
    ttl_seconds: int = 30 * 24 * 3600,
    user_agent: str = "",
    ip: str = "",
) -> dict[str, Any]:
    tenant = get_tenant_by_id(tenant_id)
    if not tenant:
        raise ValueError("租户不存在")
    if tenant["status"] != "active":
        raise ValueError("租户已停用")
    ttl = max(300, min(int(ttl_seconds or 30 * 24 * 3600), 180 * 24 * 3600))
    raw_token = tenant_security.new_opaque_token("ses_")
    session_id = tenant_security.new_opaque_token("sid_")
    now = _now()
    expires = now + timedelta(seconds=ttl)
    con = _connect()
    con.execute(
        "INSERT INTO sessions(id,tenant_id,account_id,token_hash,created_at,expires_at,last_seen_at,"
        "revoked_at,user_agent,ip) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            session_id,
            tenant_id,
            str(account_id or ""),
            tenant_security.token_hash(raw_token),
            _iso(now),
            _iso(expires),
            _iso(now),
            "",
            str(user_agent or "")[:500],
            str(ip or "")[:128],
        ),
    )
    con.commit()
    con.close()
    return {
        "id": session_id,
        "tenant_id": tenant_id,
        "account_id": str(account_id or ""),
        "token": raw_token,
        "expires_at": _iso(expires),
        "max_age": ttl,
    }


def get_session(raw_token: str) -> dict[str, Any] | None:
    digest = tenant_security.token_hash(raw_token)
    now = _iso()
    con = _connect()
    row = con.execute(
        "SELECT s.*, t.slug AS tenant_slug, t.status AS tenant_status, "
        "a.username AS account_username, a.role AS account_role, a.status AS account_status "
        "FROM sessions s JOIN tenants t ON t.id=s.tenant_id "
        "LEFT JOIN accounts a ON a.id=s.account_id "
        "WHERE s.token_hash=? AND s.revoked_at='' AND s.expires_at>? LIMIT 1",
        (digest, now),
    ).fetchone()
    if row:
        last_seen = _parse_iso(row["last_seen_at"])
        if last_seen is None or (_now() - last_seen).total_seconds() >= 60:
            con.execute(
                "UPDATE sessions SET last_seen_at=? WHERE id=?",
                (now, row["id"]),
            )
            con.execute(
                "UPDATE tenants SET last_seen_at=?,updated_at=? WHERE id=?",
                (now, now, row["tenant_id"]),
            )
            con.commit()
    con.close()
    if not row or row["tenant_status"] != "active":
        return None
    if row["account_id"] and row["account_status"] not in {None, "", "active"}:
        return None
    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "account_id": row["account_id"] or "",
        "account_username": row["account_username"] or "",
        "account_role": row["account_role"] or "member",
        "tenant_slug": row["tenant_slug"],
        "expires_at": row["expires_at"],
        "last_seen_at": row["last_seen_at"],
    }


def revoke_session(raw_token: str) -> bool:
    con = _connect()
    cursor = con.execute(
        "UPDATE sessions SET revoked_at=? WHERE token_hash=? AND revoked_at=''",
        (_iso(), tenant_security.token_hash(raw_token)),
    )
    con.commit()
    changed = cursor.rowcount > 0
    con.close()
    return changed


def purge_expired(*, older_than_seconds: int = 7 * 24 * 3600) -> dict[str, int]:
    cutoff = _iso(_now() - timedelta(seconds=max(0, int(older_than_seconds))))
    con = _connect()
    invites = con.execute(
        "DELETE FROM invites WHERE expires_at<? OR (used_at!='' AND used_at<?)"
        " OR (revoked_at!='' AND revoked_at<?)",
        (cutoff, cutoff, cutoff),
    ).rowcount
    sessions = con.execute(
        "DELETE FROM sessions WHERE expires_at<? OR (revoked_at!='' AND revoked_at<?)",
        (cutoff, cutoff),
    ).rowcount
    con.commit()
    con.close()
    return {"invites": max(0, invites), "sessions": max(0, sessions)}


def claim_storage_binding(
    tenant_id: str,
    *,
    bucket: str,
    region: str,
    prefix: str,
    access_key: str,
) -> None:
    tenant = get_tenant_by_id(tenant_id)
    if not tenant:
        raise ValueError("租户不存在")
    bucket_value = str(bucket or "").strip()
    region_value = str(region or "").strip()
    prefix_value = str(prefix or "").strip().strip("/")
    fingerprint = tenant_security.token_hash(str(access_key or "").strip())
    if not bucket_value or not region_value or not access_key:
        raise ValueError("TOS Bucket、Region 和 Access Key 不能为空")
    con = _connect()
    try:
        conflict = con.execute(
            "SELECT tenant_id,bucket,region FROM tenant_storage_bindings "
            "WHERE tenant_id<>? AND (bucket=? AND region=? OR access_key_fingerprint=?) LIMIT 1",
            (tenant_id, bucket_value, region_value, fingerprint),
        ).fetchone()
        if conflict:
            if conflict["bucket"] == bucket_value and conflict["region"] == region_value:
                raise ValueError("该 TOS Bucket 已被其他租户绑定，不能复用。")
            raise ValueError("该 TOS Access Key 已被其他租户绑定，不能复用。")
        con.execute(
            "INSERT INTO tenant_storage_bindings("
            "tenant_id,bucket,region,prefix,access_key_fingerprint,updated_at"
            ") VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(tenant_id) DO UPDATE SET "
            "bucket=excluded.bucket,region=excluded.region,prefix=excluded.prefix,"
            "access_key_fingerprint=excluded.access_key_fingerprint,updated_at=excluded.updated_at",
            (tenant_id, bucket_value, region_value, prefix_value, fingerprint, _iso()),
        )
        con.commit()
    except sqlite3.IntegrityError as exc:
        con.rollback()
        raise ValueError("该 TOS Bucket 或 Access Key 已被其他租户绑定，不能复用。") from exc
    finally:
        con.close()


def release_storage_binding(tenant_id: str) -> None:
    con = _connect()
    con.execute("DELETE FROM tenant_storage_bindings WHERE tenant_id=?", (str(tenant_id),))
    con.commit()
    con.close()

