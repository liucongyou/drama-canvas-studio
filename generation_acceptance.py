from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ProviderAcceptance:
    provider_task_id: str
    journal: dict[str, Any]


def accept_provider_task(
    task: dict[str, Any],
    *,
    remember: Callable[..., dict[str, Any]],
    update_task: Callable[..., dict[str, Any]],
    task_id: str = "",
    project_id: str = "",
    node_id: str = "",
    provider: str = "",
    model: str = "",
    request: dict[str, Any] | None = None,
    next_poll_at: str = "",
    lease_owner: str = "",
) -> ProviderAcceptance:
    """Persist a supplier receipt in the crash-independent journal first.

    The supplier may already have charged the tenant when this callback runs.
    A database write can fail because of contention, so the journal must be the
    first durable write. Database state is repaired later from the journal.
    """
    provider_task_id = str(task.get("taskId") or "").strip()
    if not provider_task_id:
        raise ValueError("供应商任务号为空")
    journal = remember(
        task_id,
        provider_task_id,
        project_id=project_id,
        node_id=node_id,
        provider=provider,
        model=model,
        request=request,
    )
    if task_id:
        fields: dict[str, Any] = {
            "provider_task_id": provider_task_id,
            "status": "running",
            "phase": "排队中",
            "progress": 20,
            "error": "",
            "finished_at": "",
        }
        if next_poll_at:
            fields["next_poll_at"] = next_poll_at
        if lease_owner:
            fields["_lease_owner"] = lease_owner
        update_task(task_id, **fields)
    return ProviderAcceptance(
        provider_task_id=provider_task_id,
        journal=journal if isinstance(journal, dict) else {},
    )
