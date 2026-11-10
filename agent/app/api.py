from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import backend_models, conversations, db, files, runtime, skills
from app.config import get_settings
from app.canvas_client import CanvasClient, CanvasError
from app.config import AGENT_DIR
from app.context import RequestContext

router = APIRouter(prefix="/api/agent")


class RunCreate(BaseModel):
    projectId: str
    skillId: str
    prompt: str = Field(min_length=1, max_length=20000)
    modelId: str = ""
    routeId: str = ""
    locks: dict[str, Any] = Field(default_factory=dict)
    fileIds: list[str] = Field(default_factory=list)
    referenceNodeIds: list[str] = Field(default_factory=list)
    conversationId: str = ""


class ConversationCreate(BaseModel):
    projectId: str
    title: str = ""
    conversationId: str = ""


class DraftUpdate(BaseModel):
    text: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    references: list[dict[str, Any]] = Field(default_factory=list)
    skillId: str = ""
    routeId: str = ""
    modelId: str = ""
    locks: dict[str, Any] = Field(default_factory=dict)


class MessageCreate(BaseModel):
    content: list[dict[str, Any]] = Field(min_length=1)
    skillId: str
    modelId: str = ""
    routeId: str = ""
    locks: dict[str, Any] = Field(default_factory=dict)
    fileIds: list[str] = Field(default_factory=list)
    referenceNodeIds: list[str] = Field(default_factory=list)


class ActivateRequest(BaseModel):
    version: str


def _context(request: Request) -> RequestContext:
    context = getattr(request.state, "agent_context", None)
    if isinstance(context, RequestContext):
        return context
    settings = get_settings()
    return RequestContext(
        tenant_id=str(settings.canvas_tenant_id or "owner"),
        account_id="legacy",
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )


def _context_scope(context: RequestContext) -> tuple[str, str]:
    tenant = str(context.tenant_id or "").strip()
    account = str(context.account_id or "").strip()
    if not tenant or not account:
        raise HTTPException(
            status_code=401,
            detail="缺少 Agent 租户或账号上下文",
        )
    return tenant, account


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/bootstrap")
def bootstrap(
    request: Request,
    projectId: str = "",
) -> dict[str, Any]:
    tenant_id, account_id = _context_scope(_context(request))
    project = str(projectId or "").strip()
    conversation_rows = (
        conversations.list_conversations(tenant_id, account_id, project)
        if project
        else []
    )
    return {
        "tenantId": tenant_id,
        "accountId": account_id,
        "conversations": conversation_rows,
    }


@router.post("/conversations")
def create_conversation(
    body: ConversationCreate,
    request: Request,
) -> dict[str, Any]:
    tenant_id, account_id = _context_scope(_context(request))
    try:
        conversation = conversations.ensure_conversation(
            tenant_id,
            account_id,
            body.projectId,
            body.conversationId,
            body.title,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "conversation": conversation}


@router.get("/conversations/{conversation_id}")
def conversation_detail(
    conversation_id: str,
    request: Request,
) -> dict[str, Any]:
    tenant_id, account_id = _context_scope(_context(request))
    conversation = conversations.get_conversation(
        conversation_id,
        tenant_id,
        account_id,
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"conversation": conversation}


@router.put("/conversations/{conversation_id}/draft")
def update_conversation_draft(
    conversation_id: str,
    body: DraftUpdate,
    request: Request,
) -> dict[str, Any]:
    tenant_id, account_id = _context_scope(_context(request))
    conversation = conversations.get_conversation(
        conversation_id,
        tenant_id,
        account_id,
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在")
    draft = conversations.save_draft(
        {
            "tenantId": tenant_id,
            "accountId": account_id,
            "conversationId": conversation_id,
        },
        body.model_dump(),
    )
    return {"ok": True, "draft": draft}


@router.get("/conversations/{conversation_id}/draft")
def conversation_draft(
    conversation_id: str,
    request: Request,
) -> dict[str, Any]:
    tenant_id, account_id = _context_scope(_context(request))
    return {
        "draft": conversations.load_draft(
            {
                "tenantId": tenant_id,
                "accountId": account_id,
                "conversationId": conversation_id,
            }
        )
    }


@router.post("/conversations/{conversation_id}/messages")
def append_message(
    conversation_id: str,
    body: MessageCreate,
    request: Request,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> dict[str, Any]:
    tenant_id, account_id = _context_scope(_context(request))
    conversation = conversations.get_conversation(
        conversation_id,
        tenant_id,
        account_id,
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在")
    key = str(idempotency_key or "").strip()
    existing = runtime.find_idempotent_run(
        key,
        tenant_id,
        account_id,
        conversation_id,
    )
    if existing:
        return {
            "ok": True,
            "idempotent": True,
            "run": existing,
            "message": None,
        }
    prompt = "\n".join(
        str(block.get("text") or block.get("message") or "").strip()
        for block in body.content
        if isinstance(block, dict)
        and str(block.get("text") or block.get("message") or "").strip()
    )
    if not prompt:
        raise HTTPException(status_code=400, detail="消息内容不能为空")
    try:
        run = runtime.create_run(
            conversation["projectId"],
            body.skillId,
            prompt,
            body.modelId,
            body.routeId,
            body.locks,
            body.fileIds,
            body.referenceNodeIds,
            conversation_id,
            tenant_id,
            account_id,
            key,
        )
        message = conversations.append_user_message(
            conversation_id,
            tenant_id,
            account_id,
            body.content,
            run["id"],
        )
    except (ValueError, skills.SkillError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "run": run, "message": message}


@router.get("/projects")
def projects() -> dict[str, Any]:
    try:
        return {"projects": CanvasClient().list_projects()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"画布服务不可用：{exc}") from exc


@router.get("/models")
def list_models() -> dict[str, Any]:
    try:
        models = backend_models.list_text_models()
    except Exception:
        models = []
    settings = get_settings()
    if not models:
        models = [{
            "id": settings.llm_model,
            "label": settings.llm_model,
            "providerId": "env",
            "providerName": "Environment",
            "model": settings.llm_model,
            "description": "来自 agent/.env",
            "badge": "Text",
            "hasApiKey": bool(settings.llm_api_key),
        }]
    default_id = next((item["id"] for item in models if item.get("hasApiKey")), models[0]["id"] if models else "")
    return {"models": models, "defaultModelId": default_id}


@router.post("/files/upload")
async def upload_reference(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    try:
        saved = files.save_file(file.filename or "script.txt", file.content_type or "", raw)
    except files.FileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "file": saved}


@router.get("/skills")
def list_skills() -> dict[str, Any]:
    result = []
    for item in skills.list_skills():
        result.append({**item, "routes": skills.list_routes(str(item.get("id") or ""))})
    return {"skills": result}


@router.get("/skills/{skill_id}/versions")
def skill_versions(skill_id: str) -> dict[str, Any]:
    return {"versions": skills.list_versions(skill_id)}


@router.get("/skills/{skill_id}")
def skill_detail(skill_id: str) -> dict[str, Any]:
    skill = skills.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="技能不存在")
    entry = ""
    try:
        entry = skills.read_entry(skill, 12000)
    except Exception:
        entry = ""
    return {"skill": {**skill, "routes": skills.list_routes(skill_id), "locks": skills.lock_schema(skill)}, "entry": entry}


@router.get("/projects/{project_id}")
def project_detail(project_id: str, request: Request) -> dict[str, Any]:
    context = _context(request)
    try:
        return {
            "project": CanvasClient(
                tenant_id=context.tenant_id,
                account_id=context.account_id,
                request_id=context.request_id,
            ).get_project(project_id)
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取项目失败：{exc}") from exc


@router.post("/skills/upload")
async def upload_skill(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    try:
        manifest = skills.upload_skill(file.filename or "skill.zip", raw)
    except skills.SkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "skill": manifest}


@router.post("/skills/{skill_id}/activate")
def activate_skill(skill_id: str, body: ActivateRequest) -> dict[str, Any]:
    try:
        return {"ok": True, "skill": skills.activate_version(skill_id, body.version)}
    except skills.SkillError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs")
def create_run(
    body: RunCreate,
    request: Request,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> dict[str, Any]:
    context = _context(request)
    try:
        existing = runtime.find_idempotent_run(
            idempotency_key,
            context.tenant_id,
            context.account_id,
            body.conversationId,
        )
        if existing:
            return {
                "ok": True,
                "idempotent": True,
                "run": existing,
            }
        return {
            "ok": True,
            "run": runtime.create_run(
                body.projectId,
                body.skillId,
                body.prompt,
                body.modelId,
                body.routeId,
                body.locks,
                body.fileIds,
                body.referenceNodeIds,
                body.conversationId,
                context.tenant_id,
                context.account_id,
                idempotency_key,
            ),
        }
    except (ValueError, skills.SkillError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs")
def list_runs(
    request: Request,
    projectId: str = "",
    conversationId: str = "",
) -> dict[str, Any]:
    context = _context(request)
    return {
        "runs": runtime.list_runs(
            projectId or None,
            conversationId or None,
            context.tenant_id,
            context.account_id,
        )
    }


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict[str, Any]:
    context = _context(request)
    run = runtime.get_run(run_id, context.tenant_id, context.account_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行不存在")
    return {"run": run}


@router.post("/runs/{run_id}/import")
def import_run(run_id: str, request: Request) -> dict[str, Any]:
    context = _context(request)
    try:
        return runtime.confirm_import(
            run_id,
            context.tenant_id,
            context.account_id,
        )
    except (ValueError, CanvasError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, request: Request) -> dict[str, Any]:
    context = _context(request)
    try:
        return runtime.cancel_run(
            run_id,
            context.tenant_id,
            context.account_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs/{run_id}/events")
def run_events(run_id: str, request: Request, since: int = 0) -> StreamingResponse:
    context = _context(request)
    if not runtime.get_run(run_id, context.tenant_id, context.account_id):
        raise HTTPException(status_code=404, detail="运行不存在")

    def generate():
        last = max(0, int(since or 0))
        idle_ticks = 0
        while True:
            with db.db() as conn:
                rows = conn.execute(
                    "SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT 100",
                    (run_id, last),
                ).fetchall()
                run = conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if rows:
                idle_ticks = 0
                for row in rows:
                    last = int(row["id"])
                    payload = db.json_loads(row["payload_json"], {})
                    body = json.dumps({"id": last, "type": row["type"], "payload": payload}, ensure_ascii=False)
                    yield f"id: {last}\ndata: {body}\n\n"
            else:
                idle_ticks += 1
                if run and run["status"] in {"done", "failed", "cancelled"} and idle_ticks >= 2:
                    break
                time.sleep(0.5)
    return StreamingResponse(generate(), media_type="text/event-stream")


def register_web(app) -> None:
    web_dir = AGENT_DIR / "app" / "web"
    index = web_dir / "index.html"

    @app.get("/")
    def root():
        return RedirectResponse(url="/agent")

    @app.get("/agent")
    def agent_page():
        return FileResponse(index, headers={"Cache-Control": "no-store"})

    @app.get("/agent/")
    def agent_page_slash():
        return FileResponse(index, headers={"Cache-Control": "no-store"})

    app.mount("/agent/static", StaticFiles(directory=str(web_dir)), name="agent-static")
