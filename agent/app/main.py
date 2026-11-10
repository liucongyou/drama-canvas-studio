from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import api, db, skills


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
    app.include_router(api.router)
    api.register_web(app)
    return app


app = create_app()
