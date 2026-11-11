import pytest

from app import db
from app.config import get_settings
from app.conversations import (
    append_user_message,
    ensure_conversation,
    list_conversations,
    load_draft,
    save_draft,
)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "agent.db"))
    monkeypatch.setenv("CANVAS_ALLOW_SQLITE", "1")
    get_settings.cache_clear()
    db.init_db()
    yield
    get_settings.cache_clear()


def test_conversation_is_scoped(tmp_db) -> None:
    conversation = ensure_conversation(
        "tenant-a",
        "account-a",
        "project-1",
    )
    assert list_conversations("tenant-b", "account-a", "project-1") == []
    rows = list_conversations("tenant-a", "account-a", "project-1")
    assert rows[0]["id"] == conversation["id"]


def test_draft_round_trip(tmp_db) -> None:
    scope = {
        "tenantId": "tenant-a",
        "accountId": "account-a",
        "conversationId": "conversation-1",
    }
    save_draft(
        scope,
        {
            "text": "中文草稿",
            "locks": {"track": "国内剧"},
            "references": [{"assetId": "character:陈大强:default"}],
        },
    )
    loaded = load_draft(scope)
    assert loaded["text"] == "中文草稿"
    assert loaded["locks"]["track"] == "国内剧"
    assert loaded["references"][0]["assetId"] == "character:陈大强:default"


def test_message_sequence_and_scope(tmp_db) -> None:
    conversation = ensure_conversation(
        "tenant-a",
        "account-a",
        "project-1",
    )
    first = append_user_message(
        conversation["id"],
        "tenant-a",
        "account-a",
        [{"type": "text", "text": "第一段"}],
    )
    second = append_user_message(
        conversation["id"],
        "tenant-a",
        "account-a",
        [{"type": "text", "text": "第二段"}],
    )
    assert first["sequenceNo"] == 1
    assert second["sequenceNo"] == 2
    with pytest.raises(ValueError):
        append_user_message(
            conversation["id"],
            "tenant-b",
            "account-a",
            [{"type": "text", "text": "越权"}],
        )
