from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderProtocol:
    """Documented runtime capabilities for one explicit provider adapter."""

    adapter: str
    label: str
    submit: str
    query: str
    task_id_fields: tuple[str, ...]
    success_statuses: frozenset[str]
    failure_statuses: frozenset[str]
    result_retention_seconds: int
    idempotent_submit: bool = False
    query_retryable: bool = True

    def public(self) -> dict[str, Any]:
        result = asdict(self)
        result["task_id_fields"] = list(self.task_id_fields)
        result["success_statuses"] = sorted(self.success_statuses)
        result["failure_statuses"] = sorted(self.failure_statuses)
        return result


COMMON_SUCCESS = frozenset(
    {"completed", "succeeded", "success", "done"}
)
COMMON_FAILURE = frozenset(
    {"failed", "error", "cancelled", "canceled", "expired"}
)


PROTOCOLS: dict[str, ProviderProtocol] = {
    "ark_video": ProviderProtocol(
        adapter="ark_video",
        label="火山方舟视频任务",
        submit="POST /contents/generations/tasks",
        query="GET /contents/generations/tasks/{task_id}",
        task_id_fields=("id", "task_id"),
        success_statuses=COMMON_SUCCESS,
        failure_statuses=COMMON_FAILURE,
        result_retention_seconds=7 * 24 * 60 * 60,
    ),
    "newapi_image": ProviderProtocol(
        adapter="newapi_image",
        label="New API 图片任务",
        submit="profile-defined",
        query="profile-defined",
        task_id_fields=("id", "task_id"),
        success_statuses=COMMON_SUCCESS | frozenset({"succeeded"}),
        failure_statuses=COMMON_FAILURE,
        result_retention_seconds=23 * 60 * 60,
    ),
    "canvaspro_video": ProviderProtocol(
        adapter="canvaspro_video",
        label="New API 视频任务",
        submit="documented profile",
        query="documented profile",
        task_id_fields=("id", "task_id"),
        success_statuses=COMMON_SUCCESS,
        failure_statuses=COMMON_FAILURE,
        result_retention_seconds=24 * 60 * 60,
    ),
    "openai_video": ProviderProtocol(
        adapter="openai_video",
        label="OpenAI 兼容视频任务",
        submit="POST /videos",
        query="GET /videos/{task_id}",
        task_id_fields=("id", "task_id"),
        success_statuses=COMMON_SUCCESS,
        failure_statuses=COMMON_FAILURE,
        result_retention_seconds=24 * 60 * 60,
    ),
    "agnes_video": ProviderProtocol(
        adapter="agnes_video",
        label="Agnes 视频任务",
        submit="POST /videos",
        query="GET /agnesapi",
        task_id_fields=("video_id", "id"),
        success_statuses=COMMON_SUCCESS,
        failure_statuses=COMMON_FAILURE,
        result_retention_seconds=24 * 60 * 60,
    ),
}


def for_adapter(adapter: str) -> ProviderProtocol | None:
    key = str(adapter or "").strip()
    if key == "newapi_video":
        # Historical model records used this adapter before documented video
        # profiles were split into explicit canvaspro/openai protocols.
        key = "canvaspro_video"
    return PROTOCOLS.get(key)


def public_protocols() -> dict[str, dict[str, Any]]:
    return {
        key: protocol.public()
        for key, protocol in PROTOCOLS.items()
    }
