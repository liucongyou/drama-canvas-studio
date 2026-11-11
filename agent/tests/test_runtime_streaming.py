from __future__ import annotations

import time
from types import SimpleNamespace

from app import db, runtime
from app.config import get_settings


def test_execute_run_persists_content_in_progressive_snapshots(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "agent.db"))
    get_settings.cache_clear()
    db.init_db()
    run_id = "run_stream_snapshot"
    now = db.utcnow()
    with db.db() as conn:
        conn.execute(
            "INSERT INTO runs(id, project_id, tenant_id, account_id, skill_id, "
            "skill_version, model_id, route_id, locks_json, file_ids_json, "
            "reference_ids_json, conversation_id, prompt, status, "
            "idempotency_key, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                "project-1",
                "owner",
                "admin",
                "demo-director",
                "0.1.0",
                "mock",
                "",
                "{}",
                "[]",
                "[]",
                "conversation-1",
                "stream",
                "queued",
                "stream-snapshot",
                now,
                now,
            ),
        )
    monkeypatch.setattr(
        runtime,
        "get_skill",
        lambda _skill_id: {
            "id": "demo-director",
            "version": "0.1.0",
            "rootPath": str(tmp_path),
            "manifest": {"raw": {}},
        },
    )
    monkeypatch.setattr(runtime, "read_entry", lambda *_args: "# skill")
    monkeypatch.setattr(runtime, "read_route_entry", lambda *_args: "")
    monkeypatch.setattr(runtime, "_system_prompt", lambda *_args: "system")
    monkeypatch.setattr(
        runtime,
        "CanvasClient",
        lambda **_kwargs: SimpleNamespace(
            get_project=lambda _project_id: {
                "title": "stream",
                "revision": 1,
                "payload": {"nodes": []},
            }
        ),
    )
    monkeypatch.setattr(
        runtime,
        "CanvasTools",
        lambda *_args, **_kwargs: SimpleNamespace(
            call=lambda *_args: {"ok": True}
        ),
    )
    monkeypatch.setattr(
        runtime,
        "compile_feed_draft",
        lambda raw, **_kwargs: SimpleNamespace(
            feed={
                "title": "stream",
                "asset_prompts": [],
                "voice_prompts": [],
                "segments": [],
            },
            errors=[],
            warnings=[],
            preview={"revision": 1, "summary": {}},
            auto_healed=False,
            schema_sha256="schema",
            feed_sha256="feed",
        ),
    )
    chunks = ["第一段", "第二段", "第三段", "第四段"]

    def execute_tool_loop(
        _system,
        _messages,
        _tools,
        _context,
        *,
        on_content,
        **_kwargs,
    ):
        for chunk in chunks:
            on_content(chunk)
            time.sleep(0.08)
        return '{"ok":true}', []

    monkeypatch.setattr(runtime, "execute_tool_loop", execute_tool_loop)
    runtime.execute_run(run_id)

    with db.db() as conn:
        events = conn.execute(
            "SELECT payload_json FROM events "
            "WHERE run_id=? AND type='run.content_delta' ORDER BY id",
            (run_id,),
        ).fetchall()
    monotonic_chars = [
        int(db.json_loads(row["payload_json"], {}).get("chars") or 0)
        for row in events
    ]
    assert len(monotonic_chars) >= 2
    assert monotonic_chars == sorted(monotonic_chars)
    assert monotonic_chars[-1] == len("".join(chunks))
    get_settings.cache_clear()
