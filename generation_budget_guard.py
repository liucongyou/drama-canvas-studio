"""Cross-process serialization for tenant generation-budget reservations."""
from __future__ import annotations

import hashlib
import os
import threading
import time
from contextlib import contextmanager

import psycopg

import pg_compat
import tenant_context


class BudgetGuardTimeout(TimeoutError):
    code = "BUDGET_GUARD_TIMEOUT"


_LOCAL_GUARDS: dict[str, threading.Lock] = {}
_LOCAL_GUARDS_GUARD = threading.Lock()
_GUARD_STACKS = threading.local()
_PG_GUARD_SLOTS = threading.BoundedSemaphore(
    max(
        1,
        int(os.environ.get("CANVAS_BUDGET_GUARD_MAX_WAITERS", "4")),
    )
)


def _tenant_key() -> str:
    context = tenant_context.current()
    if context is not None:
        return f"{context.tenant_id}:{context.slug}"
    return "local:local"


def _local_guard(key: str) -> threading.Lock:
    with _LOCAL_GUARDS_GUARD:
        return _LOCAL_GUARDS.setdefault(key, threading.Lock())


def _advisory_key(tenant_key: str) -> int:
    digest = hashlib.sha256(
        f"generation-budget:{tenant_key}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


@contextmanager
def acquire(timeout_seconds: float | None = None):
    """Hold one tenant budget lock through check + task reservation.

    PostgreSQL uses a session advisory lock, so the guarantee is shared by
    every API process using the same database. SQLite development mode keeps a
    process-local lock because it is not a production backend.
    """
    key = _tenant_key()
    if not pg_compat.postgres_enabled():
        lock = _local_guard(key)
        if not lock.acquire(timeout=float(timeout_seconds or 10.0)):
            raise BudgetGuardTimeout("生成预算锁等待超时，请稍后重试")
        try:
            yield
        finally:
            lock.release()
        return

    timeout = max(
        0.1,
        float(
            timeout_seconds
            if timeout_seconds is not None
            else os.environ.get("CANVAS_BUDGET_GUARD_TIMEOUT_SECONDS", "10")
        ),
    )
    if not _PG_GUARD_SLOTS.acquire(timeout=timeout):
        raise BudgetGuardTimeout("生成预算锁等待超时，请稍后重试")
    deadline = time.monotonic() + timeout
    advisory_key = _advisory_key(key)
    try:
        with psycopg.connect(
            pg_compat.database_url(),
            autocommit=True,
            connect_timeout=10,
            application_name="drama_canvas_budget_guard",
        ) as connection:
            acquired = False
            while True:
                row = connection.execute(
                    "SELECT pg_try_advisory_lock(%s)",
                    (advisory_key,),
                ).fetchone()
                if row and bool(row[0]):
                    acquired = True
                    break
                if time.monotonic() >= deadline:
                    raise BudgetGuardTimeout(
                        "生成预算锁等待超时，请稍后重试"
                    )
                time.sleep(0.02)
            try:
                yield
            finally:
                if acquired:
                    try:
                        connection.execute(
                            "SELECT pg_advisory_unlock(%s)",
                            (advisory_key,),
                        )
                    except Exception:
                        # Closing the session also releases all session locks.
                        pass
    finally:
        _PG_GUARD_SLOTS.release()


class Guard:
    """Thread-local context-manager facade used by the submit handler."""

    def __enter__(self):
        stack = getattr(_GUARD_STACKS, "stack", None)
        if stack is None:
            stack = []
            _GUARD_STACKS.stack = stack
        manager = acquire()
        manager.__enter__()
        stack.append(manager)
        return self

    def __exit__(self, exc_type, exc, traceback):
        stack = getattr(_GUARD_STACKS, "stack", None)
        if not stack:
            return False
        manager = stack.pop()
        return manager.__exit__(exc_type, exc, traceback)
