from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import api, db, skills
from app.config import get_settings
from app.context import context_from_headers


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    db.mark_interrupted_runs()
    skills.install_builtin_demo()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Drama Canvas Agent", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8895", "http://localhost:8895", "http://127.0.0.1:8890", "http://localhost:8890"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    @app.middleware("http")
    async def require_agent_context(request: Request, call_next):
        settings = get_settings()
        if (
            request.url.path.startswith("/api/agent/")
            and settings.agent_internal_secret
        ):
            body = await request.body()
            try:
                context = context_from_headers(
                    request.headers,
                    secret=settings.agent_internal_secret,
                    method=request.method,
                    path=request.url.path
                    + (("?" + request.url.query) if request.url.query else ""),
                    body=body,
                    window_seconds=settings.agent_signature_window_seconds,
                )
            except ValueError as exc:
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": str(exc),
                        "code": "AGENT_AUTH_REQUIRED",
                    },
                )
            request.state.agent_context = context
        return await call_next(request)

    app.include_router(api.router)
    api.register_web(app)
    return app


app = create_app()
