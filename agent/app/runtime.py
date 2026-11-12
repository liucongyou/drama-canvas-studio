from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from app import db, files
from app.canvas_client import CanvasClient, CanvasError
from app.canvas_tools import CanvasTools, TOOL_DEFINITIONS
from app.config import get_settings
from app.feed_compiler import compile_feed_draft
from app.llm import LLMError, chat, chat_stream, chat_tool_round_stream, extract_json
from app.skills import SkillError, get_skill, read_entry, read_route_entry

ACTIVE = {"queued", "running", "waiting_user"}

_RUN_SEMAPHORE = threading.BoundedSemaphore(max(1, int(os.getenv("AGENT_MAX_CONCURRENT_RUNS", "2"))))


class AgentToolLimitError(RuntimeError):
    pass


def execute_tool_loop(
    system: str,
    messages: list[dict[str, Any]],
    tools: CanvasTools,
    context: dict[str, Any],
    *,
    model_id: str = "",
    max_steps: int | None = None,
    on_content: Callable[[str], None] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    settings = get_settings()
    limit = max(1, int(max_steps or settings.agent_max_tool_steps or 30))
    working = [dict(item) for item in messages]
    executed: list[dict[str, Any]] = []
    seen_calls: dict[str, int] = {}
    for _step in range(limit):
        round_chunks: list[str] = []
        round_started = False

        def on_round_content(chunk: str) -> None:
            nonlocal round_started
            if not round_started:
                if on_content and executed:
                    on_content("\n\n")
                round_started = True
            if on_content:
                on_content(str(chunk))
            round_chunks.append(str(chunk))

        result = chat_tool_round_stream(
            system,
            working,
            tools=TOOL_DEFINITIONS_FOR_RUNTIME,
            model_id=model_id,
            tenant_id=str(context.get("tenantId") or ""),
            on_content=on_round_content if on_content else None,
        )
        if not result.tool_calls:
            if on_content and not round_chunks and result.content:
                on_content(("\n\n" if executed else "") + result.content)
            return result.content, executed
        working.append(
            {
                "role": "assistant",
                "content": result.content or "",
                "tool_calls": result.tool_calls,
            }
        )
        for item in result.tool_calls:
            function = item.get("function") if isinstance(item.get("function"), dict) else {}
            name = str(function.get("name") or "")
            raw_args = function.get("arguments")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else raw_args
            except ValueError:
                args = {"_invalidArguments": raw_args}
            if not isinstance(args, dict):
                args = {"_invalidArguments": args}
            signature = json.dumps(
                {"name": name, "arguments": args},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            seen_calls[signature] = seen_calls.get(signature, 0) + 1
            if seen_calls[signature] > 2:
                raise AgentToolLimitError(
                    f"工具 {name} 以相同参数重复调用超过 2 次，已停止空转"
                )
            call_id = 0
            try:
                call_id = db.start_tool_call(
                    str(context.get("runId") or ""),
                    name,
                    args,
                    idempotency_key=str(item.get("id") or ""),
                ) if context.get("runId") else 0
                tool_result = tools.call(name, args, context)
                status = "done"
                if call_id:
                    db.finish_tool_call(call_id, status, tool_result)
            except Exception as exc:
                tool_result = {"error": str(exc)}
                status = "failed"
                if call_id:
                    db.finish_tool_call(
                        call_id,
                        status,
                        tool_result,
                        error_code="AGENT_TOOL_ARGUMENT_INVALID",
                    )
            executed.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": name,
                    "arguments": args,
                    "status": status,
                    "result": tool_result,
                }
            )
            working.append(
                {
                    "role": "tool",
                    "tool_call_id": str(item.get("id") or ""),
                    "name": name,
                    "content": json.dumps(tool_result, ensure_ascii=False),
                }
            )
    raise AgentToolLimitError(f"工具调用超过 {limit} 步上限")


TOOL_DEFINITIONS_FOR_RUNTIME = TOOL_DEFINITIONS


def create_run(
    project_id: str,
    skill_id: str,
    prompt: str,
    model_id: str = "",
    route_id: str = "",
    locks: dict[str, Any] | None = None,
    file_ids: list[str] | None = None,
    reference_ids: list[str] | None = None,
    conversation_id: str = "",
    tenant_id: str = "",
    account_id: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    project_id = str(project_id or "").strip()
    skill_id = str(skill_id or "").strip()
    prompt = str(prompt or "").strip()
    if not project_id:
        raise ValueError("必须选择项目")
    if not prompt:
        raise ValueError("必须填写目标")
    skill = get_skill(skill_id)
    if not skill:
        raise ValueError("技能不存在或未激活")
    run_id = "run_" + uuid.uuid4().hex[:20]
    now = db.utcnow()
    with db.db() as conn:
        conn.execute(
            "INSERT INTO runs(id, project_id, tenant_id, account_id, skill_id, skill_version, model_id, route_id, locks_json, file_ids_json, reference_ids_json, conversation_id, prompt, status, idempotency_key, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                project_id,
                str(tenant_id or ""),
                str(account_id or ""),
                skill_id,
                skill["version"],
                model_id,
                route_id,
                json.dumps(locks or {}, ensure_ascii=False),
                json.dumps(file_ids or [], ensure_ascii=False),
                json.dumps(reference_ids or [], ensure_ascii=False),
                conversation_id,
                prompt,
                "queued",
                str(idempotency_key or ""),
                now,
                now,
            ),
        )
    db.add_message(run_id, "user", prompt)
    db.add_event(run_id, "run.created", {"runId": run_id, "projectId": project_id, "skillId": skill_id})
    thread = threading.Thread(target=execute_run, args=(run_id,), name=f"agent-{run_id}", daemon=True)
    thread.start()
    return get_run(run_id) or {"id": run_id}


def get_run(
    run_id: str,
    tenant_id: str = "",
    account_id: str = "",
) -> dict[str, Any] | None:
    with db.db() as conn:
        if tenant_id or account_id:
            owner_compat = str(tenant_id or "") == "owner"
            row = conn.execute(
                "SELECT * FROM runs WHERE id=? AND ("
                "(tenant_id=? AND account_id=?)"
                + (" OR (tenant_id='' AND account_id='')" if owner_compat else "")
                + ")",
                (
                    run_id,
                    str(tenant_id or ""),
                    str(account_id or ""),
                ),
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        messages = conn.execute("SELECT * FROM messages WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        steps = conn.execute("SELECT * FROM steps WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        stages = conn.execute("SELECT * FROM stages WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        artifacts = conn.execute("SELECT * FROM artifacts WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        tool_calls = conn.execute(
            "SELECT * FROM tool_calls WHERE run_id=? ORDER BY id",
            (run_id,),
        ).fetchall()
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "tenantId": row["tenant_id"] if "tenant_id" in row.keys() else "",
        "accountId": row["account_id"] if "account_id" in row.keys() else "",
        "skillId": row["skill_id"],
        "skillVersion": row["skill_version"],
        "modelId": row["model_id"] if "model_id" in row.keys() else "",
        "routeId": row["route_id"] if "route_id" in row.keys() else "",
        "locks": db.json_loads(row["locks_json"] if "locks_json" in row.keys() else "{}", {}),
        "fileIds": db.json_loads(row["file_ids_json"] if "file_ids_json" in row.keys() else "[]", []),
        "referenceNodeIds": db.json_loads(row["reference_ids_json"] if "reference_ids_json" in row.keys() else "[]", []),
        "conversationId": row["conversation_id"] if "conversation_id" in row.keys() else "",
        "prompt": row["prompt"],
        "status": row["status"],
        "error": row["error"],
        "errorCode": row["error_code"] if "error_code" in row.keys() else "",
        "currentStage": row["current_stage"] if "current_stage" in row.keys() else "",
        "cancelRequested": bool(row["cancel_requested"]) if "cancel_requested" in row.keys() else False,
        "idempotencyKey": row["idempotency_key"] if "idempotency_key" in row.keys() else "",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "messages": [
            {"id": item["id"], "role": item["role"], "content": item["content"], "createdAt": item["created_at"]}
            for item in messages
        ],
        "steps": [
            {"id": item["id"], "name": item["name"], "status": item["status"], "detail": db.json_loads(item["detail_json"], {}), "createdAt": item["created_at"]}
            for item in steps
        ],
        "stages": [
            {"id": item["id"], "name": item["name"], "status": item["status"], "attempt": item["attempt"], "detail": db.json_loads(item["detail_json"], {}), "createdAt": item["created_at"], "updatedAt": item["updated_at"]}
            for item in stages
        ],
        "artifacts": [
            {"id": item["id"], "kind": item["kind"], "payload": db.json_loads(item["payload_json"], {}), "createdAt": item["created_at"]}
            for item in artifacts
        ],
        "toolCalls": [
            {
                "id": item["id"],
                "name": item["tool_name"],
                "arguments": db.json_loads(item["args_json"], {}),
                "status": item["status"],
                "result": db.json_loads(item["result_json"], {}),
                "resultBytes": item["result_bytes"],
                "errorCode": item["error_code"],
                "startedAt": item["started_at"],
                "finishedAt": item["finished_at"],
            }
            for item in tool_calls
        ],
    }


def list_runs(
    project_id: str | None = None,
    conversation_id: str | None = None,
    tenant_id: str = "",
    account_id: str = "",
) -> list[dict[str, Any]]:
    clauses = []
    params: list[Any] = []
    if tenant_id or account_id:
        if str(tenant_id or "") == "owner":
            clauses.append(
                "((tenant_id=? AND account_id=?) OR (tenant_id='' AND account_id=''))"
            )
            params.extend([str(tenant_id or ""), str(account_id or "")])
        else:
            clauses.extend(["tenant_id=?", "account_id=?"])
            params.extend([str(tenant_id or ""), str(account_id or "")])
    if project_id:
        clauses.append("project_id=?")
        params.append(project_id)
    if conversation_id:
        if conversation_id == "default":
            clauses.append("(conversation_id='' OR conversation_id='default')")
        else:
            clauses.append("conversation_id=?")
            params.append(conversation_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with db.db() as conn:
        rows = conn.execute("SELECT * FROM runs" + where + " ORDER BY created_at DESC LIMIT 50", tuple(params)).fetchall()
    return [
        {
            "id": row["id"], "projectId": row["project_id"], "skillId": row["skill_id"],
            "skillVersion": row["skill_version"], "modelId": row["model_id"] if "model_id" in row.keys() else "", "routeId": row["route_id"] if "route_id" in row.keys() else "", "prompt": row["prompt"], "status": row["status"],
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }
        for row in rows
    ]


def find_idempotent_run(
    idempotency_key: str,
    tenant_id: str,
    account_id: str,
    conversation_id: str,
) -> dict[str, Any] | None:
    key = str(idempotency_key or "").strip()
    if not key:
        return None
    with db.db() as conn:
        row = conn.execute(
            "SELECT id FROM runs WHERE idempotency_key=? AND tenant_id=? AND account_id=? AND conversation_id=? "
            "ORDER BY created_at DESC LIMIT 1",
            (
                key,
                str(tenant_id or ""),
                str(account_id or ""),
                str(conversation_id or ""),
            ),
        ).fetchone()
    return get_run(row["id"], tenant_id, account_id) if row else None


def active_run_for_conversation(
    conversation_id: str,
    tenant_id: str,
    account_id: str,
) -> dict[str, Any] | None:
    if not conversation_id:
        return None
    with db.db() as conn:
        row = conn.execute(
            "SELECT id FROM runs WHERE conversation_id=? AND tenant_id=? AND account_id=? "
            "AND status IN ('queued','running','waiting_locks','waiting_review','importing') "
            "ORDER BY created_at DESC LIMIT 1",
            (
                str(conversation_id or ""),
                str(tenant_id or ""),
                str(account_id or ""),
            ),
        ).fetchone()
    return get_run(row["id"], tenant_id, account_id) if row else None


def _system_prompt(skill: dict[str, Any], route_entry: str = "") -> str:
    settings = get_settings()
    entry = route_entry or read_entry(skill, settings.max_entry_chars)
    quality_path = (
        Path(str(skill.get("rootPath") or ""))
        / "references"
        / "runtime-quality-contract.md"
    )
    try:
        quality_contract = quality_path.read_text(
            encoding="utf-8",
            errors="replace",
        )[:16000]
    except OSError:
        quality_contract = ""
    return (
        "你是戏剧画布（drama-canvas-studio）的投喂稿导演。"
        "你的唯一交付是一个可直接导入画布的 JSON 对象，格式固定为 drama-canvas-feed/v2。"
        "不要输出 markdown 解释，不要输出多份 JSON。\n\n"
        "硬性要求：\n"
        "1) canvas_contract.schema 必须是 drama-canvas-feed/v2；\n"
        "2) 顶层必须包含 title、canvas_contract、asset_prompts（数组，可为空）、voice_prompts（数组，可为空）、segments；\n"
        "3) 每段包含 no、name、raw；raw 是给画布生成用的正文；每个资产/角色在 asset_prompts / voice_prompts 中必须有对应声明；\n"
        "4) 资产引用使用 @//chr/...、@//scn/...、@//prp/... 或 @角色｜名称（图N）；\n"
        "5) 有对白时写 台词：角色（情绪）：\"内容\"，并保证 voice_prompts 与角色一致；\n"
        "6) 不要改画布已有节点，不要输出导入命令；\n"
        "7) canvas_contract.generator 必须包含 name、skillVersion、packageRevision，用于追踪技能版本。\n\n"
        "9) asset_prompts 每项必须包含 code、name、raw；raw 不能为空。\n"
        "10) voice_prompts 每项必须包含 code、name、age（纯数字如 28）、gender（female 或 male）、dialect（如 standard）、format=audio-prompt-v1、prompt、label、description；prompt 必须以 A {gender} aged {age} 开头，并以固定音频结尾结束；每个有台词的角色都必须有一条 voice。\n\n"
        "11) 服务端会用共享 canvas_feed_contract 做确定性编译；图号、generator、schemaSha256 和音色机械字段以服务端编译结果为准。\n\n"
        "8) 输出骨架如下，必须补齐字段，不要省略 asset_prompts / voice_prompts：\n"
        "{\"title\":\"第01集\",\"canvas_contract\":{\"schema\":\"drama-canvas-feed/v2\",\"video_model\":\"doubao-seedance-2-0\",\"video_aspect\":\"9:16\",\"generator\":{\"name\":\"<skill id>\",\"skillVersion\":\"<version>\",\"packageRevision\":\"<revision>\"}},\"asset_prompts\":[],\"voice_prompts\":[],\"segments\":[{\"no\":1,\"name\":\"段1\",\"raw\":\"...\"}]}\n\n"
        "技能信息：id=" + str(skill.get("id")) + " version=" + str(skill.get("version")) + "\n\n"
        "运行时质量合同（必须执行）：\n" + quality_contract + "\n\n"
        "技能入口与运行时合同已内置；不要请求额外工具或额外章节，"
        "直接完成本次投喂稿输出：\n" + entry
    )


def _project_brief(project: dict[str, Any]) -> str:
    payload = project.get("payload") if isinstance(project.get("payload"), dict) else {}
    episodes = payload.get("episodeImports") if isinstance(payload.get("episodeImports"), list) else []
    nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
    lines = [f"项目：{project.get('title') or payload.get('title') or ''}"]
    lines.append(f"已有集数：{len(episodes)}；已有节点：{len(nodes)}")
    for episode in episodes[-4:]:
        if isinstance(episode, dict):
            lines.append(f"- 集 {episode.get('id')}: {episode.get('title') or ''}（{len(episode.get('nodeIds') or [])} 节点）")
    for node in nodes[:12]:
        if isinstance(node, dict):
            lines.append(f"- 节点 {node.get('id')} · {node.get('type')} · {node.get('title') or ''}")
    return "\n".join(lines)[:6000]


def _save_artifact(run_id: str, kind: str, payload: dict[str, Any]) -> None:
    with db.db() as conn:
        conn.execute(
            "INSERT INTO artifacts(run_id, kind, payload_json, created_at) VALUES(?,?,?,?)",
            (run_id, kind, json.dumps(payload, ensure_ascii=False), db.utcnow()),
        )


def _is_cancelled(run_id: str) -> bool:
    with db.db() as conn:
        row = conn.execute(
            "SELECT status, cancel_requested FROM runs WHERE id=?",
            (run_id,),
        ).fetchone()
    return bool(
        row
        and (
            row["status"] == "cancelled"
            or bool(row["cancel_requested"])
        )
    )


def _execute_run_body(run_id: str) -> None:
    run = get_run(run_id)
    if not run:
        return
    settings = get_settings()
    db.set_run_status(run_id, "running")
    db.set_run_stage(run_id, "brief")
    db.add_event(run_id, "run.started", {"runId": run_id})
    try:
        skill = get_skill(run["skillId"])
        if not skill:
            raise SkillError("技能不存在")
        package_revision = str(
            (skill.get("manifest", {}).get("raw") or {}).get("packageRevision")
            or skill.get("version")
            or ""
        )

        brief_stage = db.start_stage(run_id, "brief")
        db.add_step(run_id, "project", "running")
        canvas_client = CanvasClient(
            tenant_id=run.get("tenantId") or "",
            account_id=run.get("accountId") or "",
            request_id=run_id,
        )
        project = canvas_client.get_project(run["projectId"])
        brief_text = _project_brief(project)
        db.finish_stage(brief_stage, "done", {"title": project.get("title"), "revision": project.get("revision")})
        _save_artifact(run_id, "brief", {"brief": brief_text, "projectId": run["projectId"]})
        if run.get("locks"):
            locks_stage = db.start_stage(run_id, "locks")
            _save_artifact(run_id, "locks", run["locks"])
            db.finish_stage(locks_stage, "done", run["locks"])
        db.add_step(run_id, "project", "done", {"title": project.get("title"), "revision": project.get("revision")})

        generate_stage = db.start_stage(run_id, "generate")
        db.set_run_stage(run_id, "generate")
        db.add_step(run_id, "generate", "running")
        route_entry = read_route_entry(skill, str(run.get("routeId") or ""), settings.max_entry_chars)
        locks_text = "；".join(f"{key}={value}" for key, value in (run.get("locks") or {}).items() if value)
        reference_text = ""
        if run.get("referenceNodeIds"):
            payload = project.get("payload") if isinstance(project.get("payload"), dict) else {}
            node_map = {
                str(node.get("id")): node
                for node in payload.get("nodes") or []
                if isinstance(node, dict) and node.get("id")
            }
            lines = []
            for node_id in run.get("referenceNodeIds") or []:
                node = node_map.get(str(node_id))
                if node:
                    lines.append(f"- {node_id} | {node.get('type')} | {node.get('title') or ''}")
            if lines:
                reference_text = "\n".join(lines)
        attachment_text = ""
        for item in files.load_texts(run.get("fileIds") or []):
            attachment_text += f"\n\n【附件：{item['name']}】\n{item['text']}"
        user_prompt = (
            f"项目上下文：\n{brief_text}\n\n"
            + (f"已锁题：{locks_text}\n\n" if locks_text else "")
            + (f"参考节点：\n{reference_text}\n\n" if reference_text else "")
            + (f"参考文件：{attachment_text}\n\n" if attachment_text else "")
            + f"用户目标：\n{run['prompt']}\n\n请基于即将提供的只读画布上下文，直接输出一份可导入的完整 JSON。"
        )
        system_prompt = _system_prompt(skill, route_entry)
        status_message_id = db.create_message(run_id, "status", "上下文已预取，正在生成投喂稿…")
        canvas_tools = CanvasTools(
            project,
            canvas_client,
            max_result_bytes=settings.agent_tool_result_bytes,
            skill=skill,
        )
        prefetch_trace: list[dict[str, Any]] = []
        context_payload = {
            "projectId": run["projectId"],
            "runId": run_id,
            "tenantId": run.get("tenantId") or "",
            "accountId": run.get("accountId") or "",
        }
        prefetch_context: dict[str, Any] = {}
        for index, (tool_name, tool_args) in enumerate(
            (("get_project_context", {"nodeLimit": 160}),),
            start=1,
        ):
            call_id = db.start_tool_call(
                run_id,
                tool_name,
                tool_args,
                idempotency_key=f"prefetch:{run_id}:{index}",
            )
            try:
                tool_result = canvas_tools.call(
                    tool_name,
                    tool_args,
                    context_payload,
                )
                db.finish_tool_call(call_id, "done", tool_result)
                status = "done"
            except Exception as exc:
                tool_result = {"error": str(exc)}
                db.finish_tool_call(
                    call_id,
                    "failed",
                    tool_result,
                    error_code="AGENT_TOOL_ARGUMENT_INVALID",
                )
                status = "failed"
            prefetch_context[tool_name] = tool_result
            prefetch_trace.append(
                {
                    "id": f"prefetch-{index}",
                    "name": tool_name,
                    "arguments": tool_args,
                    "status": status,
                    "result": tool_result,
                }
            )
        user_prompt += (
            "\n\n画布上下文预读取结果（只读，不得自行改写）：\n"
            + json.dumps(prefetch_context, ensure_ascii=False)[:32_000]
            + "\n\n上下文已经预取完整。直接基于以上上下文和用户目标输出一份完整 JSON；"
            + "不要输出解释，不要省略 asset_prompts、voice_prompts 或 segments。"
        )
        stream_message_id = db.create_message(run_id, "draft", "")
        stream_parts: list[str] = []
        stream_last_flush = 0.0
        stream_flushed_chars = 0

        def flush_stream_content(*, force: bool = False) -> None:
            nonlocal stream_last_flush, stream_flushed_chars
            text = "".join(stream_parts)
            if not text or (not force and len(text) == stream_flushed_chars):
                return
            now = time.monotonic()
            if (
                not force
                and len(text) - stream_flushed_chars < 120
                and now - stream_last_flush < 0.08
            ):
                return
            db.update_message(stream_message_id, text)
            db.add_event(
                run_id,
                "run.content_delta",
                {"chars": len(text)},
            )
            stream_last_flush = now
            stream_flushed_chars = len(text)

        def stream_content(chunk: str) -> None:
            stream_parts.append(str(chunk))
            flush_stream_content()

        try:
            streamed_parts: list[str] = []
            for chunk in chat_stream(
                system_prompt,
                user_prompt,
                model_id=run.get("modelId") or "",
                tenant_id=run.get("tenantId") or "",
            ):
                streamed_parts.append(str(chunk))
                stream_content(str(chunk))
            raw = "".join(streamed_parts)
        finally:
            flush_stream_content(force=True)
        tool_trace = prefetch_trace
        db.update_message(
            status_message_id,
            f"已生成投喂稿，执行 {len(tool_trace)} 次内部上下文读取；模型调用 1 次。",
        )
        db.add_event(
            run_id,
            "run.progress",
            {
                "chars": len(raw),
                "toolCalls": [
                    {
                        "name": item.get("name"),
                        "status": item.get("status"),
                    }
                    for item in tool_trace[:20]
                ],
            },
        )
        if _is_cancelled(run_id):
            db.update_message(status_message_id, "已停止")
            return
        db.update_message(status_message_id, "投喂稿已生成，正在校验契约…")
        compiled = compile_feed_draft(
            extract_json(raw),
            skill_id=run["skillId"],
            skill_version=run["skillVersion"],
            package_revision=package_revision,
        )
        feed = compiled.feed
        db.finish_stage(generate_stage, "done", {"chars": len(raw)})
        db.add_step(run_id, "generate", "done", {"chars": len(raw)})

        validate_stage = db.start_stage(run_id, "validate")
        db.set_run_stage(run_id, "validate")
        db.add_step(run_id, "validate", "running")
        errors = list(compiled.errors)
        preview = (
            canvas_client.preview_import(run["projectId"], feed)
            if not errors
            else {
                "errors": errors,
                "warnings": list(compiled.warnings),
                "summary": {},
                "revision": None,
            }
        )
        errors = preview.get("errors") if isinstance(preview.get("errors"), list) else []
        repairs = 0
        for _ in range(max(0, int(settings.max_repair_rounds))):
            if not errors:
                break
            repairs += 1
            repair = (
                "上一次 JSON 未通过画布预检。问题如下：\n"
                + json.dumps(errors[:16], ensure_ascii=False, indent=2)
                + "\n只修复上述问题，不要改动已经正确的剧情/台词；\n"
                + "如果是音色缺失：不要删除台词，只补 voice_prompts，每个有台词的角色一条，name 必须与台词说话人完全一致，age 用数字，gender 从角色资产 raw 推断，format=audio-prompt-v1。\n"
                + "请只返回修复后的完整 JSON；不要解释，不要省略 asset_prompts / voice_prompts。"
            )
            db.update_message(status_message_id, f"正在修复契约问题（第 {repairs} 轮）…")
            try:
                raw = chat(
                    system_prompt,
                    user_prompt + "\n\n" + repair,
                    model_id=run.get("modelId") or "",
                    tenant_id=run.get("tenantId") or "",
                )
                compiled = compile_feed_draft(
                    extract_json(raw),
                    skill_id=run["skillId"],
                    skill_version=run["skillVersion"],
                    package_revision=package_revision,
                )
            except (LLMError, ValueError) as exc:
                # A later repair turn must not discard an already valid feed.
                # Keep the latest parsed version and surface its concrete contract
                # errors to the user instead of replacing them with a JSON parser
                # exception.
                db.update_message(
                    status_message_id,
                    "补充修复返回格式异常，已保留上一版有效投喂稿。",
                )
                db.add_event(
                    run_id,
                    "run.repair_json_invalid",
                    {"repair": repairs, "error": str(exc)},
                )
                break
            feed = compiled.feed
            errors = list(compiled.errors)
            preview = (
                canvas_client.preview_import(run["projectId"], feed)
                if not errors
                else {
                    "errors": errors,
                    "warnings": list(compiled.warnings),
                    "summary": {},
                    "revision": None,
                }
            )
            errors = preview.get("errors") if isinstance(preview.get("errors"), list) else []
        if _is_cancelled(run_id):
            db.update_message(status_message_id, "已停止")
            return
        auto_healed = compiled.auto_healed
        db.update_message(status_message_id, "投喂稿已通过预检" if not errors else "预检未通过，请查看问题")
        db.finish_stage(validate_stage, "done" if not errors else "failed", {"errors": errors[:12], "repairs": repairs, "autoHealed": auto_healed})
        db.add_step(run_id, "validate", "done" if not errors else "failed", {"errors": errors[:12], "repairs": repairs})

        artifact = {
            "feed": feed,
            "preview": preview,
            "errors": errors,
            "warnings": preview.get("warnings") or [],
            "repairs": repairs,
            "autoHealed": auto_healed,
            "contract": {
                "schemaSha256": compiled.schema_sha256,
                "feedSha256": compiled.feed_sha256,
                "warnings": list(compiled.warnings),
            },
        }
        with db.db() as conn:
            conn.execute(
                "INSERT INTO artifacts(run_id, kind, payload_json, created_at) VALUES(?,?,?,?)",
                (run_id, "canvas_feed", json.dumps(artifact, ensure_ascii=False), db.utcnow()),
            )
        if errors:
            db.set_run_status(run_id, "failed", "导入预检未通过")
            db.add_message(run_id, "assistant", "生成完成，但画布预检未通过；请查看问题后重试。")
            db.add_event(run_id, "run.failed", {"runId": run_id, "errors": errors[:8]})
        else:
            db.set_run_status(run_id, "waiting_user")
            db.add_message(run_id, "assistant", "投喂稿已通过画布预检，请在右侧确认导入。")
            db.add_event(run_id, "run.waiting_user", {"runId": run_id, "summary": preview.get("summary") or {}})
    except (CanvasError, LLMError, SkillError, AgentToolLimitError, ValueError) as exc:
        db.add_message(run_id, "status", f"生成中断：{exc}")
        db.fail_running_stages(run_id, str(exc))
        db.set_run_status(run_id, "failed", str(exc))
        db.add_message(run_id, "assistant", f"生成失败：{exc}")
        db.add_event(run_id, "run.failed", {"runId": run_id, "error": str(exc)})
    except Exception as exc:  # pragma: no cover - defensive boundary
        db.fail_running_stages(run_id, str(exc))
        db.set_run_status(run_id, "failed", str(exc))
        db.add_message(run_id, "assistant", f"生成失败：{exc}")
        db.add_event(run_id, "run.failed", {"runId": run_id, "error": str(exc)})


def confirm_import(
    run_id: str,
    tenant_id: str = "",
    account_id: str = "",
) -> dict[str, Any]:
    run = get_run(run_id, tenant_id, account_id)
    if not run:
        raise ValueError("运行不存在")
    if run["status"] != "waiting_user":
        raise ValueError("当前运行不在可导入状态")
    artifact = next((item for item in run["artifacts"] if item["kind"] == "canvas_feed"), None)
    if not artifact:
        raise ValueError("没有可导入的投喂稿")
    payload = artifact["payload"]
    feed = payload.get("feed")
    preview = payload.get("preview") or {}
    revision = preview.get("revision")
    if revision is None:
        raise ValueError("预检结果缺少 revision，请重新生成")
    deliver_stage = db.start_stage(run_id, "deliver")
    db.set_run_stage(run_id, "deliver")
    db.set_run_status(run_id, "running")
    try:
        client = CanvasClient(
            tenant_id=run.get("tenantId") or tenant_id,
            account_id=run.get("accountId") or account_id,
            request_id=run_id,
        )
        # An owner-scoped legacy run may have empty attribution. Resolve the
        # project through the current owner context before any write so a stale
        # run id can never become a cross-tenant import capability.
        client.get_project(run["projectId"])
        result = client.commit_import(run["projectId"], feed, int(revision))
    except Exception as exc:
        db.finish_stage(deliver_stage, "failed", {"error": str(exc)})
        db.set_run_status(run_id, "failed", str(exc))
        db.add_message(run_id, "assistant", f"导入失败：{exc}")
        db.add_event(run_id, "run.failed", {"runId": run_id, "error": str(exc)})
        raise
    db.finish_stage(deliver_stage, "done", result)
    db.set_run_status(run_id, "done")
    db.add_message(run_id, "assistant", "已导入画布。")
    db.add_event(run_id, "run.done", {"runId": run_id, "result": result})
    return {"ok": True, "run": get_run(run_id), "result": result}


def cancel_run(
    run_id: str,
    tenant_id: str = "",
    account_id: str = "",
) -> dict[str, Any]:
    run = get_run(run_id, tenant_id, account_id)
    if not run:
        raise ValueError("运行不存在")
    if run["status"] in {"done", "failed", "cancelled"}:
        return {"ok": True, "run": run}
    db.request_run_cancel(run_id)
    db.set_run_status(run_id, "cancelled")
    db.add_message(run_id, "assistant", "运行已取消。")
    db.add_event(run_id, "run.cancelled", {"runId": run_id})
    return {"ok": True, "run": get_run(run_id)}



def execute_run(run_id: str) -> None:
    """Run one agent request with a process-wide provider concurrency cap.

    A thread per run is fine for isolation, but without a cap a burst of
    conversations can hit the model provider simultaneously. The semaphore
    queues excess runs while keeping their DB status as queued.
    """
    acquired = False
    while not acquired:
        if _is_cancelled(run_id):
            return
        acquired = _RUN_SEMAPHORE.acquire(timeout=0.5)
    try:
        _execute_run_body(run_id)
    finally:
        _RUN_SEMAPHORE.release()
