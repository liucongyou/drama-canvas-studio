from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api.v1.canvas import router as canvas_router
from backend.db.session import engine, warm_connection_pool


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        warm_connection_pool()
    except Exception:
        # Health checks must still be reachable when PostgreSQL is down.
        logger.exception("Could not pre-warm the PostgreSQL connection pool")
    try:
        yield
    finally:
        engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Drama Canvas Shadow API",
        version="2026.11.10",
        docs_url="/internal/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.include_router(canvas_router)
    return app


app = create_app()
