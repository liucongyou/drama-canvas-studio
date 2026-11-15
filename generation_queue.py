from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta, timezone

import canvas_store as store
import pg_compat


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
