from __future__ import annotations

import json
import secrets
from typing import Any, Iterable

from app import db


def _scope(tenant_id: str, account_id: str) -> tuple[str, str]:
    tenant = str(tenant_id or "").strip()
    account = str(account_id or "").strip()
    if not tenant or not account:
        raise ValueError("缺少租户或账号上下文")
    return tenant, account


def _content_text(blocks: Iterable[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        text = str(block.get("text") or block.get("message") or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def ensure_conversation(
    tenant_id: str,
    account_id: str,
    project_id: str,
    conversation_id: str = "",
    title: str = "",
) -> dict[str, Any]:
    tenant, account = _scope(tenant_id, account_id)
    project = str(project_id or "").strip()
    if not project:
        raise ValueError("必须选择项目")
    desired_id = str(conversation_id or "").strip()
    now = db.utcnow()
    with db.db() as conn:
        if desired_id:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id=? AND tenant_id=? AND account_id=? AND project_id=?",
                (desired_id, tenant, account, project),
            ).fetchone()
            if row:
                return _conversation_dict(row)
        else:
            row = conn.execute(
                "SELECT * FROM conversations WHERE tenant_id=? AND account_id=? AND project_id=? "
                "ORDER BY updated_at DESC LIMIT 1",
                (tenant, account, project),
            ).fetchone()
            if row:
                return _conversation_dict(row)
        new_id = desired_id or "conv_" + secrets.token_hex(12)
        conn.execute(
            "INSERT INTO conversations(id, tenant_id, account_id, project_id, title, status, active_run_id, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                new_id,
                tenant,
                account,
                project,
                str(title or "新会话")[:200],
                "active",
                "",
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM conversations WHERE id=?",
            (new_id,),
        ).fetchone()
    return _conversation_dict(row)


def list_conversations(
    tenant_id: str,
    account_id: str,
    project_id: str,
) -> list[dict[str, Any]]:
    tenant, account = _scope(tenant_id, account_id)
    with db.db() as conn:
        rows = conn.execute(
            "SELECT * FROM conversations WHERE tenant_id=? AND account_id=? AND project_id=? "
            "ORDER BY updated_at DESC LIMIT 100",
            (tenant, account, str(project_id or "").strip()),
        ).fetchall()
    return [_conversation_dict(row) for row in rows]


def get_conversation(
    conversation_id: str,
    tenant_id: str,
    account_id: str,
) -> dict[str, Any] | None:
    tenant, account = _scope(tenant_id, account_id)
    with db.db() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id=? AND tenant_id=? AND account_id=?",
            (str(conversation_id or "").strip(), tenant, account),
        ).fetchone()
        if not row:
            return None
        messages = conn.execute(
            "SELECT * FROM conversation_messages WHERE conversation_id=? AND tenant_id=? AND account_id=? "
            "ORDER BY sequence_no, id",
            (row["id"], tenant, account),
        ).fetchall()
    result = _conversation_dict(row)
    result["messages"] = [_message_dict(item) for item in messages]
    return result


def append_user_message(
    conversation_id: str,
    tenant_id: str,
    account_id: str,
    blocks: list[dict[str, Any]],
    run_id: str = "",
) -> dict[str, Any]:
    tenant, account = _scope(tenant_id, account_id)
    conversation = get_conversation(conversation_id, tenant, account)
    if not conversation:
        raise ValueError("会话不存在或无权访问")
    now = db.utcnow()
    content_text = _content_text(blocks)
    with db.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_no "
            "FROM conversation_messages WHERE conversation_id=? AND tenant_id=? AND account_id=?",
            (conversation_id, tenant, account),
        ).fetchone()
        sequence_no = int(row["next_no"] or 1)
        cur = conn.execute(
            "INSERT INTO conversation_messages(conversation_id, tenant_id, account_id, run_id, role, content_json, content_text, sequence_no, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                conversation_id,
                tenant,
                account,
                str(run_id or ""),
                "user",
                json.dumps(blocks, ensure_ascii=False),
                content_text,
                sequence_no,
                now,
            ),
        )
        conn.execute(
            "UPDATE conversations SET updated_at=?, active_run_id=? WHERE id=? AND tenant_id=? AND account_id=?",
            (now, str(run_id or ""), conversation_id, tenant, account),
        )
        inserted = conn.execute(
            "SELECT * FROM conversation_messages WHERE id=?",
            (int(cur.lastrowid or 0),),
        ).fetchone()
    return _message_dict(inserted)


def save_draft(scope: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    tenant, account = _scope(
        str(scope.get("tenantId") or ""),
        str(scope.get("accountId") or ""),
    )
    conversation_id = str(scope.get("conversationId") or "").strip()
    if not conversation_id:
        raise ValueError("缺少会话 ID")
    text = str(payload.get("text") or "")[:100_000]
    attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
    references = payload.get("references") if isinstance(payload.get("references"), list) else []
    locks = payload.get("locks") if isinstance(payload.get("locks"), dict) else {}
    recipe_bindings = (
        payload.get("recipeBindings")
        if isinstance(payload.get("recipeBindings"), list)
        else []
    )
    now = db.utcnow()
    with db.db() as conn:
        conn.execute(
            "INSERT INTO agent_drafts(tenant_id, account_id, conversation_id, text, attachment_ids_json, reference_json, skill_id, route_id, model_id, locks_json, recipe_bindings_json, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(tenant_id, account_id, conversation_id) DO UPDATE SET "
            "text=excluded.text, attachment_ids_json=excluded.attachment_ids_json, "
            "reference_json=excluded.reference_json, skill_id=excluded.skill_id, "
            "route_id=excluded.route_id, model_id=excluded.model_id, locks_json=excluded.locks_json, "
            "recipe_bindings_json=excluded.recipe_bindings_json, "
            "updated_at=excluded.updated_at",
            (
                tenant,
                account,
                conversation_id,
                text,
                json.dumps(attachments, ensure_ascii=False),
                json.dumps(references, ensure_ascii=False),
                str(payload.get("skillId") or ""),
                str(payload.get("routeId") or ""),
                str(payload.get("modelId") or ""),
                json.dumps(locks, ensure_ascii=False),
                json.dumps(recipe_bindings, ensure_ascii=False),
                now,
            ),
        )
    return load_draft(scope)


def load_draft(scope: dict[str, Any]) -> dict[str, Any]:
    tenant, account = _scope(
        str(scope.get("tenantId") or ""),
        str(scope.get("accountId") or ""),
    )
    conversation_id = str(scope.get("conversationId") or "").strip()
    with db.db() as conn:
        row = conn.execute(
            "SELECT * FROM agent_drafts WHERE tenant_id=? AND account_id=? AND conversation_id=?",
            (tenant, account, conversation_id),
        ).fetchone()
    if not row:
        return {
            "text": "",
            "attachments": [],
            "references": [],
            "skillId": "",
            "routeId": "",
            "modelId": "",
            "locks": {},
            "recipeBindings": [],
            "updatedAt": "",
        }
    return {
        "text": row["text"],
        "attachments": db.json_loads(row["attachment_ids_json"], []),
        "references": db.json_loads(row["reference_json"], []),
        "skillId": row["skill_id"],
        "routeId": row["route_id"],
        "modelId": row["model_id"],
        "locks": db.json_loads(row["locks_json"], {}),
        "recipeBindings": db.json_loads(
            row["recipe_bindings_json"]
            if "recipe_bindings_json" in row.keys()
            else "[]",
            [],
        ),
        "updatedAt": row["updated_at"],
    }


def _conversation_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "tenantId": row["tenant_id"],
        "accountId": row["account_id"],
        "projectId": row["project_id"],
        "title": row["title"],
        "status": row["status"],
        "activeRunId": row["active_run_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _message_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "conversationId": row["conversation_id"],
        "runId": row["run_id"],
        "role": row["role"],
        "blocks": db.json_loads(row["content_json"], []),
        "content": row["content_text"],
        "sequenceNo": row["sequence_no"],
        "createdAt": row["created_at"],
    }
