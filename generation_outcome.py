from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import provider_protocols


LOCAL_VALIDATION_MARKERS = (
    "参考素材未就绪",
    "参考素材缺失",
    "引用节点不存在",
    "无法解析引用",
    "对应多个资产",
    "提示词包含 @ 资产引用",
    "提示词为空",
    "提示词超过",
    "当前模型最多",
    "音色节点尚未生成",
    "源节点尚未生成",
)

TERMINAL_PROVIDER_FAILURE_PHASES = frozenset({
    "查询失败",
    "生成失败",
    "人工确认失败",
})

TERMINAL_PROVIDER_FAILURE_MARKERS = (
    "供应商明确返回任务不存在",
    "HTTP 404",
    "供应商视频生成失败",
    "供应商图片生成失败",
    "供应商返回生成失败",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def is_local_validation_error(message: Any) -> bool:
    text = _text(message)
    return any(marker in text for marker in LOCAL_VALIDATION_MARKERS)


_UNSETTLED_ACTIVE_STATUSES = frozenset({"queued", "submitting", "running"})
_UNSETTLED_DELIVERY_STATUSES = frozenset({"pending", "materializing"})


def task_blocks_node_deletion(task: dict[str, Any] | None) -> bool:
    """Return whether deleting this task's node could orphan paid work.

    This is deliberately an allow-only-on-proof rule. Active work, an
    interrupted worker whose provider request may be live, a fetched provider
    result awaiting durable storage, or a provider URL not yet written to the
    local node all block deletion. A provider-confirmed terminal failure or a
    non-local-validation failure before acceptance is safe to discard.
    """
    if not task:
        return False
    status = _text(task.get("status")).lower()
    phase = _text(task.get("phase"))
    delivery_status = _text(task.get("delivery_status")).lower()
    # Legacy rows were created with the schema default `pending` even after a
    # pre-submit rejection or a safe terminal task. That default is not proof a
    # result is still outstanding; explicit fetching/materializing is.
    if delivery_status == "pending" and status not in _UNSETTLED_ACTIVE_STATUSES:
        delivery_status = ""
    provider_task_id = _text(task.get("provider_task_id"))
    provider_result_url = _text(task.get("provider_result_url"))
    local_result_url = _text(task.get("local_result_url"))
    if status in _UNSETTLED_ACTIVE_STATUSES:
        return True
    if phase == "人工确认失败":
        return False
    if delivery_status in _UNSETTLED_DELIVERY_STATUSES:
        return True
    if provider_task_id and not local_result_url:
        if is_provider_terminal_failure(task):
            return False
        return _provider_reported_live(task) or _provider_submission_may_be_live(task)
    if provider_result_url and not local_result_url:
        return True
    if status == "interrupted" and _provider_submission_may_be_live(task):
        return not is_provider_terminal_failure(task)
    if status in {"succeeded", "failed", "interrupted", "cancelled"}:
        return False
    return True


def is_provider_terminal_failure(task: dict[str, Any] | None) -> bool:
    """Return true only for a supplier-confirmed terminal failure.

    Local lease loss, a timeout, or an uncertain submission is deliberately not
    terminal. Those states may still be reconciled against the same provider
    task. A supplier 404 or explicit generation failure is final and must never
    be reopened by journal reconciliation.
    """
    if not task or _text(task.get("status")).lower() != "failed":
        return False
    phase = _text(task.get("phase"))
    error = _text(task.get("error"))
    return (
        phase in TERMINAL_PROVIDER_FAILURE_PHASES
        or any(marker in error for marker in TERMINAL_PROVIDER_FAILURE_MARKERS)
    )


def _provider_submission_may_be_live(task: dict[str, Any]) -> bool:
    if _text(task.get("provider_task_id")):
        return True
    error = _text(task.get("error"))
    return any(
        marker in error
        for marker in (
            "结果待确认",
            "等待供应商回执",
            "没有返回任务号",
            "没有解析出任务号",
            "未取得可确认结果",
            "请求已发出",
            "连接.*中止",
            "WinError 10053",
        )
    )


def _provider_reported_live(task: dict[str, Any]) -> bool:
    if not _text(task.get("provider_task_id")):
        return False
    status = _text(task.get("status")).lower()
    phase = _text(task.get("phase"))
    error = _text(task.get("error"))
    if status not in {"running", "interrupted", "failed"}:
        return False
    return (
        phase in {
            "排队中",
            "生成中",
            "查询重试中",
            "结果待确认",
            "等待恢复",
            "正在连接供应商",
        }
        or phase.startswith("生成中 ")
        or "供应商任务仍在执行" in error
        or "执行租约已过期" in error
        or "结果待确认" in error
    )


def _provider_reported_dead(task: dict[str, Any]) -> bool:
    if not _text(task.get("provider_task_id")):
        return False
    phase = _text(task.get("phase"))
    error = _text(task.get("error"))
    return (
        phase in {"查询失败", "生成失败"}
        or "供应商明确返回任务不存在" in error
        or "供应商视频生成失败" in error
        or "供应商图片生成失败" in error
    )


def task_outcome(task: dict[str, Any] | None) -> dict[str, Any]:
    """Compute the only public next-step contract from persisted task facts.

    This projection never submits or mutates a task. The front end should use
    these fields rather than classify error strings independently.
    """
    if not task:
        return {
            "phase": "failed",
            "regenerate": "blocked",
            "reason": "task_not_found",
            "requiresConfirmation": False,
            "providerTaskId": "",
            "providerAccepted": False,
            "retentionExpiresAt": "",
        }

    status = _text(task.get("status")).lower()
    phase = _text(task.get("phase"))
    error = _text(task.get("error"))
    provider_task_id = _text(task.get("provider_task_id"))
    delivery_status = _text(task.get("delivery_status")).lower()
    local_blocked = is_local_validation_error(error)
    accepted = bool(provider_task_id)
    protocol = provider_protocols.for_adapter(str(task.get("adapter") or ""))
    retention_expires_at = ""
    if protocol and accepted:
        try:
            created = datetime.fromisoformat(
                _text(task.get("created_at")).replace("Z", "+00:00")
            )
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            retention_expires_at = datetime.fromtimestamp(
                created.timestamp() + protocol.result_retention_seconds,
                tz=timezone.utc,
            ).isoformat()
        except (TypeError, ValueError):
            retention_expires_at = ""
    provider_retention_expired = False
    if retention_expires_at:
        try:
            provider_retention_expired = (
                datetime.fromisoformat(retention_expires_at)
                <= datetime.now(timezone.utc)
            )
        except ValueError:
            provider_retention_expired = False
    uncertain = (
        status in {"running", "interrupted", "failed"}
        and _provider_submission_may_be_live(task)
    )

    if status == "queued":
        return {
            "phase": "running",
            "regenerate": "blocked",
            "reason": "queued",
            "requiresConfirmation": False,
            "providerTaskId": provider_task_id,
            "providerAccepted": accepted,
            "retentionExpiresAt": retention_expires_at,
        }
    if status == "succeeded":
        if delivery_status in {"pending", "materializing"}:
            return {
                "phase": "fetching",
                "regenerate": "blocked",
                "reason": "result_fetching",
                "requiresConfirmation": False,
                "providerTaskId": provider_task_id,
                "providerAccepted": accepted,
                "retentionExpiresAt": retention_expires_at,
            }
        if delivery_status == "failed":
            return {
                "phase": "failed",
                "regenerate": "confirm",
                "reason": "result_delivery_failed",
                "requiresConfirmation": True,
                "providerTaskId": provider_task_id,
                "providerAccepted": accepted,
                "retentionExpiresAt": retention_expires_at,
            }
        return {
            "phase": "ready",
            "regenerate": "allowed",
            "reason": "result_ready",
            "requiresConfirmation": False,
            "providerTaskId": provider_task_id,
            "providerAccepted": accepted,
            "retentionExpiresAt": retention_expires_at,
        }
    if (
        status == "interrupted"
        and accepted
        and delivery_status in {"pending", "materializing"}
    ):
        # A provider task may already be complete while its image is being
        # downloaded or atomically written back to the canvas. `interrupted`
        # describes the previous local worker, not the paid provider task.
        return {
            "phase": "fetching",
            "regenerate": "blocked",
            "reason": "result_fetching",
            "requiresConfirmation": False,
            "providerTaskId": provider_task_id,
            "providerAccepted": True,
            "retentionExpiresAt": retention_expires_at,
        }
    if phase == "无法确认且已过期":
        return {
            "phase": "failed",
            "regenerate": "confirm",
            "reason": "provider_unconfirmed_expired",
            "requiresConfirmation": True,
            "providerTaskId": provider_task_id,
            "providerAccepted": accepted,
            "retentionExpiresAt": retention_expires_at,
        }
    if _provider_reported_live(task):
        return {
            "phase": "running",
            "regenerate": "blocked",
            "reason": "provider_task_running",
            "requiresConfirmation": False,
            "providerTaskId": provider_task_id,
            "providerAccepted": True,
            "retentionExpiresAt": retention_expires_at,
        }
    if _provider_reported_dead(task):
        return {
            "phase": "failed",
            "regenerate": "confirm",
            "reason": "provider_task_failed",
            "requiresConfirmation": True,
            "providerTaskId": provider_task_id,
            "providerAccepted": True,
            "retentionExpiresAt": retention_expires_at,
        }
    if status in {"running", "submitting"}:
        if provider_retention_expired:
            return {
                "phase": "unknown",
                "regenerate": "confirm",
                "reason": "provider_retention_expired",
                "requiresConfirmation": True,
                "providerTaskId": provider_task_id,
                "providerAccepted": accepted,
                "retentionExpiresAt": retention_expires_at,
            }
        return {
            "phase": "running",
            "regenerate": "blocked",
            "reason": "provider_running" if provider_task_id else "submitting",
            "requiresConfirmation": False,
            "providerTaskId": provider_task_id,
            "providerAccepted": accepted,
            "retentionExpiresAt": retention_expires_at,
        }
    if status == "interrupted" and uncertain:
        return {
            "phase": "unknown",
            "regenerate": "confirm",
            "reason": "provider_outcome_unknown",
            "requiresConfirmation": True,
            "providerTaskId": provider_task_id,
            "providerAccepted": accepted,
            "retentionExpiresAt": retention_expires_at,
        }
    if local_blocked:
        return {
            "phase": "failed",
            "regenerate": "allowed",
            "reason": "local_validation",
            "requiresConfirmation": False,
            "providerTaskId": provider_task_id,
            "providerAccepted": accepted,
            "retentionExpiresAt": retention_expires_at,
        }
    if accepted:
        return {
            "phase": "failed",
            "regenerate": "confirm",
            "reason": "provider_failed_after_acceptance",
            "requiresConfirmation": True,
            "providerTaskId": provider_task_id,
            "providerAccepted": True,
            "retentionExpiresAt": retention_expires_at,
        }
    if status in {"failed", "interrupted", "cancelled"}:
        return {
            "phase": "failed",
            "regenerate": "allowed",
            "reason": "provider_rejected_before_acceptance",
            "requiresConfirmation": False,
            "providerTaskId": provider_task_id,
            "providerAccepted": False,
            "retentionExpiresAt": retention_expires_at,
        }
    return {
        "phase": "failed",
        "regenerate": "blocked",
        "reason": "unsupported_task_state",
        "requiresConfirmation": False,
        "providerTaskId": provider_task_id,
        "providerAccepted": accepted,
        "retentionExpiresAt": retention_expires_at,
    }


def can_manually_resolve_task(
    task: dict[str, Any] | None,
    outcome: dict[str, Any] | None = None,
) -> bool:
    """Whether an operator may close an unresolved paid task as failed."""
    if not task:
        return False
    status = _text(task.get("status")).lower()
    phase = _text(task.get("phase"))
    if status != "interrupted" or phase == "人工确认失败":
        return False
    if is_local_validation_error(task.get("error")):
        return False
    if is_provider_terminal_failure(task):
        # The provider already supplied a definitive failure. The task is
        # closed and only needs normal regeneration confirmation.
        return False
    projected = outcome if isinstance(outcome, dict) else task_outcome(task)
    if (
        _text(projected.get("phase")) == "fetching"
        and (
            _text(task.get("delivery_status")).lower() == "materializing"
            or bool(_text(task.get("local_result_url")))
        )
    ):
        # A paid provider result is already being downloaded/materialized.
        # Manual failure here would orphan a result the user has paid for.
        return False
    # Interrupted means the local worker no longer owns the task. The explicit
    # user action is the bounded escape hatch when provider reconciliation
    # never yields a terminal receipt. CAS in the store still protects a late
    # successful result from being overwritten.
    return True
