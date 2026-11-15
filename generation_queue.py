from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

import canvas_store as store
import pg_compat
import tenant_context
import tenant_registry


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    current = value or _now()
    return current.astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"


def _task_columns(connection) -> list[str]:
    return [
        item[0]
        for item in connection.execute(
            "SELECT * FROM generation_tasks LIMIT 0"
        ).description
    ]


def new_claim_token(worker_id: str) -> str:
    return f"{str(worker_id or '').strip()}:{secrets.token_hex(12)}"


def claim_next_generation_task(
    worker_id: str,
    *,
    claim_token: str = "",
    lease_seconds: int = 60,
    age_promotion_seconds: int | None = None,
) -> dict | None:
    worker_id = str(worker_id or "").strip()
    if not worker_id:
        raise ValueError("worker_id 不能为空")
    token = str(claim_token or "").strip() or new_claim_token(worker_id)
    now = _now()
    now_text = _iso(now)
    lease_expires_at = _iso(
        now + timedelta(seconds=max(5, int(lease_seconds)))
    )
    promotion_seconds = max(
        1,
        int(
            age_promotion_seconds
            if age_promotion_seconds is not None
            else os.getenv("CANVAS_QUEUE_AGE_PROMOTION_SECONDS", "300")
        ),
    )
    promotion_cutoff = _iso(now - timedelta(seconds=promotion_seconds))

    def write(connection):
        query = (
            "SELECT * FROM generation_tasks "
            "WHERE status='queued' "
            "AND COALESCE(queue_state,'') NOT IN ('blocked','paused') "
            "AND (COALESCE(available_at,'')='' OR available_at<=?) "
            "AND (COALESCE(lease_owner,'')='' "
            "OR COALESCE(lease_expires_at,'')='' "
            "OR lease_expires_at<=?) "
            "ORDER BY "
            "CASE WHEN COALESCE(created_at,'')<=? THEN 1 ELSE 0 END DESC, "
            "COALESCE(priority,0) DESC,created_at ASC,rowid ASC "
            "LIMIT 1"
        )
        params = (now_text, now_text, promotion_cutoff)
        if pg_compat.postgres_enabled():
            query += " FOR UPDATE SKIP LOCKED"
        row = connection.execute(query, params).fetchone()
        if not row:
            return None
        columns = _task_columns(connection)
        task = dict(zip(columns, row))
        updated = connection.execute(
            "UPDATE generation_tasks SET "
            "status='running',queue_state='claimed',phase='已领取执行租约',"
            "worker_id=?,lease_owner=?,lease_expires_at=?,heartbeat_at=?,"
            "attempt_count=COALESCE(attempt_count,0)+1,"
            "started_at=CASE WHEN COALESCE(started_at,'')='' "
            "THEN ? ELSE started_at END,"
            "updated_at=?,error='',finished_at='' "
            "WHERE id=? AND status='queued' "
            "AND (COALESCE(lease_owner,'')='' "
            "OR COALESCE(lease_expires_at,'')='' "
            "OR lease_expires_at<=?)",
            (
                worker_id,
                token,
                lease_expires_at,
                now_text,
                now_text,
                now_text,
                task["id"],
                now_text,
            ),
        )
        if int(updated.rowcount or 0) != 1:
            return None
        return {
            **task,
            "status": "running",
            "queue_state": "claimed",
            "phase": "已领取执行租约",
            "worker_id": worker_id,
            "lease_owner": token,
            "lease_expires_at": lease_expires_at,
            "heartbeat_at": now_text,
            "attempt_count": int(task.get("attempt_count") or 0) + 1,
            "claimToken": token,
        }

    return store._run_write_transaction(write)


def renew_generation_task_claim(
    task_id: str,
    claim_token: str,
    *,
    lease_seconds: int = 60,
) -> bool:
    now = _now()
    now_text = _iso(now)
    lease_expires_at = _iso(
        now + timedelta(seconds=max(5, int(lease_seconds)))
    )

    def write(connection):
        result = connection.execute(
            "UPDATE generation_tasks SET lease_expires_at=?,heartbeat_at=?,"
            "updated_at=? WHERE id=? AND status='running' AND lease_owner=?",
            (
                lease_expires_at,
                now_text,
                now_text,
                str(task_id or ""),
                str(claim_token or ""),
            ),
        )
        return int(result.rowcount or 0) == 1

    return bool(store._run_write_transaction(write))


def release_generation_task_claim(
    task_id: str,
    claim_token: str,
    *,
    requeue: bool = False,
    error: str = "",
) -> bool:
    now_text = _iso()

    def write(connection):
        row = connection.execute(
            "SELECT status,provider_task_id FROM generation_tasks "
            "WHERE id=? AND lease_owner=?",
            (str(task_id or ""), str(claim_token or "")),
        ).fetchone()
        if not row:
            return False
        status = str(row[0] or "")
        provider_task_id = str(row[1] or "").strip()
        if status in {"succeeded", "failed", "interrupted", "cancelled"}:
            result = connection.execute(
                "UPDATE generation_tasks SET lease_owner='',worker_id='',"
                "lease_expires_at='',heartbeat_at='',queue_state='terminal',"
                "updated_at=? WHERE id=? AND lease_owner=?",
                (now_text, str(task_id or ""), str(claim_token or "")),
            )
            return int(result.rowcount or 0) == 1
        if requeue and not provider_task_id:
            result = connection.execute(
                "UPDATE generation_tasks SET status='queued',queue_state='queued',"
                "phase='等待调度',lease_owner='',worker_id='',"
                "lease_expires_at='',heartbeat_at='',available_at=?,"
                "last_worker_error=?,updated_at=? "
                "WHERE id=? AND lease_owner=? AND status='running'",
                (
                    now_text,
                    str(error or "")[:1000],
                    now_text,
                    str(task_id or ""),
                    str(claim_token or ""),
                ),
            )
            return int(result.rowcount or 0) == 1
        result = connection.execute(
            "UPDATE generation_tasks SET status='interrupted',"
            "queue_state='blocked',phase='结果待确认',"
            "lease_owner='',worker_id='',lease_expires_at='',heartbeat_at='',"
            "last_worker_error=?,delivery_status='pending',updated_at=? "
            "WHERE id=? AND lease_owner=? AND status='running'",
            (
                str(error or "执行租约已释放，结果需要确认")[:1000],
                now_text,
                str(task_id or ""),
                str(claim_token or ""),
            ),
        )
        return int(result.rowcount or 0) == 1

    return bool(store._run_write_transaction(write))


def recover_expired_queue_claims(limit: int = 100) -> list[dict]:
    now_text = _iso()

    def write(connection):
        query = (
            "SELECT * FROM generation_tasks "
            "WHERE status IN ('submitting','running') "
            "AND COALESCE(lease_expires_at,'')!='' "
            "AND lease_expires_at<=? "
            "ORDER BY lease_expires_at ASC,rowid ASC LIMIT ?"
        )
        if pg_compat.postgres_enabled():
            query += " FOR UPDATE SKIP LOCKED"
        rows = connection.execute(
            query,
            (now_text, max(1, min(int(limit or 100), 500))),
        ).fetchall()
        if not rows:
            return []
        columns = _task_columns(connection)
        recovered = []
        for row in rows:
            task = dict(zip(columns, row))
            provider_task_id = str(
                task.get("provider_task_id") or ""
            ).strip()
            if provider_task_id:
                connection.execute(
                    "UPDATE generation_tasks SET status='queued',"
                    "queue_state='recover',phase='等待恢复',"
                    "lease_owner='',worker_id='',lease_expires_at='',"
                    "heartbeat_at='',available_at=?,last_worker_error=?,"
                    "updated_at=? WHERE id=?",
                    (
                        now_text,
                        "执行租约过期，按供应商任务号恢复",
                        now_text,
                        task["id"],
                    ),
                )
                task.update({
                    "status": "queued",
                    "queue_state": "recover",
                    "phase": "等待恢复",
                    "available_at": now_text,
                })
            else:
                connection.execute(
                    "UPDATE generation_tasks SET status='interrupted',"
                    "queue_state='blocked',phase='结果待确认',"
                    "lease_owner='',worker_id='',lease_expires_at='',"
                    "heartbeat_at='',last_worker_error=?,delivery_status='pending',"
                    "updated_at=? WHERE id=?",
                    (
                        "执行租约过期，未取得供应商任务号，禁止自动重提",
                        now_text,
                        task["id"],
                    ),
                )
                task.update({
                    "status": "interrupted",
                    "queue_state": "blocked",
                    "phase": "结果待确认",
                })
            recovered.append(task)
        return recovered

    return list(store._run_write_transaction(write) or [])


def heartbeat_worker(
    worker_id: str,
    *,
    active_tasks: int = 0,
    metadata: dict | None = None,
) -> dict:
    worker_id = str(worker_id or "").strip()
    if not worker_id:
        raise ValueError("worker_id 不能为空")
    now_text = _iso()

    def write(connection):
        connection.execute(
            "INSERT INTO generation_worker_heartbeats("
            "worker_id,started_at,heartbeat_at,active_tasks,metadata_json"
            ") VALUES(?,?,?,?,?) "
            "ON CONFLICT(worker_id) DO UPDATE SET "
            "heartbeat_at=EXCLUDED.heartbeat_at,"
            "active_tasks=EXCLUDED.active_tasks,"
            "metadata_json=EXCLUDED.metadata_json",
            (
                worker_id,
                now_text,
                now_text,
                max(0, int(active_tasks or 0)),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        return {
            "workerId": worker_id,
            "heartbeatAt": now_text,
            "activeTasks": max(0, int(active_tasks or 0)),
        }

    return store._run_write_transaction(write)


def worker_heartbeats(*, active_within_seconds: int = 30) -> list[dict]:
    cutoff = _iso(_now() - timedelta(seconds=max(1, active_within_seconds)))
    connection = store._connect()
    try:
        rows = connection.execute(
            "SELECT worker_id,started_at,heartbeat_at,active_tasks,"
            "metadata_json FROM generation_worker_heartbeats "
            "WHERE heartbeat_at>=? ORDER BY heartbeat_at DESC",
            (cutoff,),
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "workerId": str(row[0]),
            "startedAt": str(row[1]),
            "heartbeatAt": str(row[2]),
            "activeTasks": int(row[3] or 0),
            "metadata": _safe_json(row[4]),
        }
        for row in rows
    ]


def queue_metrics() -> dict:
    connection = store._connect()
    try:
        counts = {
            str(row[0] or ""): int(row[1] or 0)
            for row in connection.execute(
                "SELECT status,COUNT(*) FROM generation_tasks "
                "GROUP BY status"
            ).fetchall()
        }
        oldest = connection.execute(
            "SELECT MIN(created_at) FROM generation_tasks "
            "WHERE status='queued'"
        ).fetchone()[0]
        expired = int(connection.execute(
            "SELECT COUNT(*) FROM generation_tasks "
            "WHERE status IN ('submitting','running') "
            "AND COALESCE(lease_expires_at,'')!='' "
            "AND lease_expires_at<=?",
            (_iso(),),
        ).fetchone()[0] or 0)
    finally:
        connection.close()
    return {
        "status": counts,
        "queued": counts.get("queued", 0),
        "active": counts.get("submitting", 0) + counts.get("running", 0),
        "expiredLeases": expired,
        "oldestQueuedAt": oldest or "",
    }


def _safe_json(raw) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _advisory_key(scope: str) -> int:
    digest = hashlib.sha256(
        f"generation-admission:{scope}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _admission_limits(
    provider: str,
    model: str,
    kind: str,
    overrides: dict | None = None,
) -> dict[str, int]:
    overrides = overrides or {}
    kind_defaults = {
        "image": 16,
        "video": 10,
        "audio": 8,
        "text": 8,
    }
    return {
        "global": max(
            1,
            int(
                overrides.get("global")
                or os.getenv("CANVAS_GENERATION_GLOBAL_CONCURRENCY", "80")
            ),
        ),
        "tenant": max(
            1,
            int(
                overrides.get("tenant")
                or os.getenv("CANVAS_GENERATION_TENANT_CONCURRENCY", "32")
            ),
        ),
        "provider": max(
            1,
            int(
                overrides.get("provider")
                or os.getenv("CANVAS_GENERATION_PROVIDER_CONCURRENCY", "20")
            ),
        ),
        "model": max(
            1,
            int(
                overrides.get("model")
                or kind_defaults.get(str(kind or "").lower(), 8)
            ),
        ),
    }


def _tenant_canvas_schema() -> str:
    return pg_compat.schema_for_path(
        tenant_context.current_db_path(),
        "canvas",
    )


def _mark_task_admitted(
    connection,
    task_id: str,
    claim_token: str,
    now_text: str,
    lease_expires_at: str,
) -> bool:
    schema = _tenant_canvas_schema()
    result = connection.execute(
        f'UPDATE "{schema}".generation_tasks SET '
        "status='submitting',queue_state='admitted',phase='已获得执行槽位',"
        "lease_expires_at=?,heartbeat_at=?,updated_at=?,error='',"
        "finished_at='' WHERE id=? AND status='running' AND lease_owner=?",
        (
            lease_expires_at,
            now_text,
            now_text,
            task_id,
            claim_token,
        ),
    )
    return int(result.rowcount or 0) == 1


def acquire_generation_admission(
    task_id: str,
    claim_token: str,
    *,
    provider: str,
    model: str,
    kind: str,
    lease_seconds: int = 60,
    limits: dict | None = None,
) -> dict:
    task_id = str(task_id or "").strip()
    claim_token = str(claim_token or "").strip()
    provider = str(provider or "").strip()
    model = str(model or "").strip()
    kind = str(kind or "").strip().lower()
    if not task_id or not claim_token:
        raise ValueError("task_id 和 claim_token 不能为空")
    task = store.get_generation_task(task_id) or {}
    if (
        str(task.get("status") or "") != "running"
        or str(task.get("lease_owner") or "") != claim_token
    ):
        return {"acquired": False, "reason": "lease_lost", "taskId": task_id}
    tenant_id = tenant_context.current_tenant_id()
    now = _now()
    now_text = _iso(now)
    expires_at = _iso(
        now + timedelta(seconds=max(5, int(lease_seconds)))
    )
    configured = _admission_limits(provider, model, kind, limits)
    keys = [
        _advisory_key("global"),
        _advisory_key(f"tenant:{tenant_id}"),
        _advisory_key(f"provider:{tenant_id}:{provider}"),
        _advisory_key(f"model:{tenant_id}:{provider}:{model}:{kind}"),
    ]

    def write(connection):
        for key in keys:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
        existing = connection.execute(
            "SELECT * FROM generation_admissions WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if existing:
            columns = [
                item[0]
                for item in connection.execute(
                    "SELECT * FROM generation_admissions LIMIT 0"
                ).description
            ]
            admission = dict(zip(columns, existing))
            if (
                str(admission.get("lease_owner") or "") == claim_token
                and str(admission.get("expires_at") or "") > now_text
            ):
                connection.execute(
                    "UPDATE generation_admissions SET expires_at=? "
                    "WHERE task_id=? AND lease_owner=?",
                    (expires_at, task_id, claim_token),
                )
                if not _mark_task_admitted(
                    connection,
                    task_id,
                    claim_token,
                    now_text,
                    expires_at,
                ):
                    return {
                        "acquired": False,
                        "reason": "lease_lost",
                        "taskId": task_id,
                    }
                return {
                    **admission,
                    "expires_at": expires_at,
                    "acquired": True,
                    "reused": True,
                }
            connection.execute(
                "DELETE FROM generation_admissions WHERE task_id=?",
                (task_id,),
            )
        connection.execute(
            "DELETE FROM generation_admissions WHERE expires_at<=?",
            (now_text,),
        )
        cooldown_key = f"{tenant_id}\x1f{provider}"
        cooldown = connection.execute(
            "SELECT until_at FROM generation_provider_cooldowns "
            "WHERE scope_key=?",
            (cooldown_key,),
        ).fetchone()
        until_at = str(cooldown[0] or "") if cooldown else ""
        if until_at and until_at > now_text:
            try:
                retry_after = max(
                    1,
                    int(
                        (
                            datetime.fromisoformat(
                                until_at.replace("Z", "+00:00")
                            )
                            - now
                        ).total_seconds()
                    ),
                )
            except ValueError:
                retry_after = 1
            connection.execute(
                f'UPDATE "{_tenant_canvas_schema()}".generation_tasks SET '
                "status='queued',queue_state='cooldown',"
                "phase=?,lease_owner='',worker_id='',lease_expires_at='',"
                "heartbeat_at='',available_at=?,updated_at=? "
                "WHERE id=? AND lease_owner=?",
                (
                    f"供应商限流冷却 {retry_after}s",
                    until_at,
                    now_text,
                    task_id,
                    claim_token,
                ),
            )
            return {
                "acquired": False,
                "reason": "provider_cooldown",
                "retryAfterSeconds": retry_after,
                "taskId": task_id,
            }
        active_global = int(connection.execute(
            "SELECT COUNT(*) FROM generation_admissions WHERE expires_at>?",
            (now_text,),
        ).fetchone()[0] or 0)
        active_tenant = int(connection.execute(
            "SELECT COUNT(*) FROM generation_admissions "
            "WHERE tenant_id=? AND expires_at>?",
            (tenant_id, now_text),
        ).fetchone()[0] or 0)
        active_provider = int(connection.execute(
            "SELECT COUNT(*) FROM generation_admissions "
            "WHERE tenant_id=? AND provider=? AND expires_at>?",
            (tenant_id, provider, now_text),
        ).fetchone()[0] or 0)
        active_model = int(connection.execute(
            "SELECT COUNT(*) FROM generation_admissions "
            "WHERE tenant_id=? AND provider=? AND model=? AND kind=? "
            "AND expires_at>?",
            (tenant_id, provider, model, kind, now_text),
        ).fetchone()[0] or 0)
        blocked_by = ""
        if active_global >= configured["global"]:
            blocked_by = "global"
        elif active_tenant >= configured["tenant"]:
            blocked_by = "tenant"
        elif active_provider >= configured["provider"]:
            blocked_by = "provider"
        elif active_model >= configured["model"]:
            blocked_by = "model"
        if blocked_by:
            connection.execute(
                f'UPDATE "{_tenant_canvas_schema()}".generation_tasks SET '
                "status='queued',queue_state='waiting_capacity',"
                "phase=?,lease_owner='',worker_id='',lease_expires_at='',"
                "heartbeat_at='',available_at=?,updated_at=? "
                "WHERE id=? AND lease_owner=?",
                (
                    f"等待生成槽位（{blocked_by}）",
                    _iso(now + timedelta(seconds=2)),
                    now_text,
                    task_id,
                    claim_token,
                ),
            )
            return {
                "acquired": False,
                "reason": "capacity",
                "blockedBy": blocked_by,
                "retryAfterSeconds": 2,
                "taskId": task_id,
                "active": {
                    "global": active_global,
                    "tenant": active_tenant,
                    "provider": active_provider,
                    "model": active_model,
                },
                "limits": configured,
            }
        admission_id = store._rid(22)
        worker_id = str(task.get("worker_id") or "")
        connection.execute(
            "INSERT INTO generation_admissions("
            "id,tenant_id,task_id,worker_id,lease_owner,provider,model,kind,"
            "acquired_at,expires_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                admission_id,
                tenant_id,
                task_id,
                worker_id,
                claim_token,
                provider,
                model,
                kind,
                now_text,
                expires_at,
            ),
        )
        if not _mark_task_admitted(
            connection,
            task_id,
            claim_token,
            now_text,
            expires_at,
        ):
            return {
                "acquired": False,
                "reason": "lease_lost",
                "taskId": task_id,
            }
        return {
            "acquired": True,
            "admissionId": admission_id,
            "tenantId": tenant_id,
            "taskId": task_id,
            "leaseOwner": claim_token,
            "provider": provider,
            "model": model,
            "kind": kind,
            "expiresAt": expires_at,
            "limits": configured,
            "active": {
                "global": active_global + 1,
                "tenant": active_tenant + 1,
                "provider": active_provider + 1,
                "model": active_model + 1,
            },
        }

    return _with_control_transaction(write)


def renew_generation_admission(
    task_id: str,
    claim_token: str,
    *,
    lease_seconds: int = 60,
) -> bool:
    expires_at = _iso(
        _now() + timedelta(seconds=max(5, int(lease_seconds)))
    )

    def write(connection):
        result = connection.execute(
            "UPDATE generation_admissions SET expires_at=? "
            "WHERE task_id=? AND lease_owner=?",
            (
                expires_at,
                str(task_id or ""),
                str(claim_token or ""),
            ),
        )
        return int(result.rowcount or 0) == 1

    return bool(_with_control_transaction(write))


def release_generation_admission(
    task_id: str,
    claim_token: str = "",
) -> bool:
    def write(connection):
        if claim_token:
            result = connection.execute(
                "DELETE FROM generation_admissions "
                "WHERE task_id=? AND lease_owner=?",
                (str(task_id or ""), str(claim_token or "")),
            )
        else:
            result = connection.execute(
                "DELETE FROM generation_admissions WHERE task_id=?",
                (str(task_id or ""),),
            )
        return int(result.rowcount or 0) > 0

    return bool(_with_control_transaction(write))


def mark_provider_cooldown(
    provider: str,
    retry_after_seconds: float,
) -> dict:
    tenant_id = tenant_context.current_tenant_id()
    provider = str(provider or "").strip()
    now = _now()
    until_at = _iso(
        now + timedelta(seconds=max(1.0, float(retry_after_seconds)))
    )
    scope_key = f"{tenant_id}\x1f{provider}"
    now_text = _iso(now)

    def write(connection):
        connection.execute(
            "INSERT INTO generation_provider_cooldowns("
            "scope_key,tenant_id,provider,until_at,updated_at"
            ") VALUES(?,?,?,?,?) "
            "ON CONFLICT(scope_key) DO UPDATE SET "
            "until_at=GREATEST(generation_provider_cooldowns.until_at,"
            "EXCLUDED.until_at),updated_at=EXCLUDED.updated_at",
            (
                scope_key,
                tenant_id,
                provider,
                until_at,
                now_text,
            ),
        )
        return {
            "scopeKey": scope_key,
            "provider": provider,
            "untilAt": until_at,
        }

    return _with_control_transaction(write)


def admission_metrics() -> dict:
    now_text = _iso()

    def read(connection):
        active = int(connection.execute(
            "SELECT COUNT(*) FROM generation_admissions WHERE expires_at>?",
            (now_text,),
        ).fetchone()[0] or 0)
        cooldowns = int(connection.execute(
            "SELECT COUNT(*) FROM generation_provider_cooldowns "
            "WHERE until_at>?",
            (now_text,),
        ).fetchone()[0] or 0)
        return {
            "activeAdmissions": active,
            "activeCooldowns": cooldowns,
        }

    connection = tenant_registry._connect()
    try:
        return read(connection)
    finally:
        connection.close()


def _with_control_transaction(callback):
    connection = tenant_registry._connect()

    try:
        connection.execute("BEGIN")
        result = callback(connection)
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
