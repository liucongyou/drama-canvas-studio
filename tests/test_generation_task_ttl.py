from __future__ import annotations

from datetime import datetime, timedelta, timezone

import canvas_store as store
import generation_outcome


def _init_store(monkeypatch, tmp_path):
    monkeypatch.setenv("CANVAS_DB_BACKEND", "sqlite")
    monkeypatch.setenv("CANVAS_ALLOW_SQLITE", "1")
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "canvas.db"))
    monkeypatch.setattr(store, "_SCHEMA_READY", False)
    store._connect().close()


def _task(
    monkeypatch,
    tmp_path,
    *,
    provider_task_id="",
    age_hours=25,
    error="等待供应商回执，无法确认请求是否受理",
):
    _init_store(monkeypatch, tmp_path)
    task = store.create_generation_task(
        "project",
        f"node-{provider_task_id or 'unconfirmed'}",
        kind="video",
    )
    store.update_generation_task(
        task["id"],
        status="interrupted",
        phase="结果待确认",
        error=error,
        provider_task_id=provider_task_id,
        delivery_status="pending",
    )
    created_at = (
        datetime.now(timezone.utc) - timedelta(hours=age_hours)
    ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    con = store._connect()
    try:
        con.execute(
            "UPDATE generation_tasks SET created_at=?,updated_at=? WHERE id=?",
            (created_at, created_at, task["id"]),
        )
        con.commit()
    finally:
        con.close()
    return store.get_generation_task(task["id"])


def test_old_uncertain_task_without_provider_id_expires(monkeypatch, tmp_path):
    task = _task(monkeypatch, tmp_path)

    expired = store.expire_unconfirmed_generation_tasks()

    saved = store.get_generation_task(task["id"])
    assert [item["id"] for item in expired] == [task["id"]]
    assert saved["status"] == "failed"
    assert saved["phase"] == "无法确认且已过期"
    assert saved["delivery_status"] == "failed"
    assert "供应商" in saved["error"]
    outcome = generation_outcome.task_outcome(saved)
    assert outcome["reason"] == "provider_unconfirmed_expired"
    assert outcome["regenerate"] == "confirm"


def test_task_with_provider_id_is_not_auto_closed(monkeypatch, tmp_path):
    task = _task(
        monkeypatch,
        tmp_path,
        provider_task_id="provider-1",
        error="执行租约已过期，但已有供应商任务号",
    )

    assert store.expire_unconfirmed_generation_tasks() == []
    assert store.get_generation_task(task["id"])["status"] == "interrupted"


def test_recent_uncertain_task_is_not_expired(monkeypatch, tmp_path):
    task = _task(monkeypatch, tmp_path, age_hours=23)

    assert store.expire_unconfirmed_generation_tasks() == []
    assert store.get_generation_task(task["id"])["status"] == "interrupted"


def test_old_local_validation_failure_is_not_reclassified(
    monkeypatch,
    tmp_path,
):
    task = _task(
        monkeypatch,
        tmp_path,
        error="参考素材未就绪：角色图缺失",
    )

    assert store.expire_unconfirmed_generation_tasks() == []
    saved = store.get_generation_task(task["id"])
    assert saved["status"] == "interrupted"
    assert saved["phase"] == "结果待确认"
