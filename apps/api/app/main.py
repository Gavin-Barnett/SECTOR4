from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.db.session import SessionLocal
from app.services.scheduler import PollScheduler
from sector4_core.config import get_settings
from sector4_core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    scheduler: PollScheduler | None = None
    app.state.poll_scheduler = None
    if settings.ops_scheduler_enabled:
        scheduler = PollScheduler(SessionLocal, settings)
        scheduler.start()
        app.state.poll_scheduler = scheduler
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.stop()


settings = get_settings()

app = FastAPI(
    title="SECTOR4 API",
    version="0.1.0",
    summary="Public SEC Form 4 signal scanner",
    description=(
        "Uses public SEC filings only. Not investment advice. "
        "Users should review original SEC filings before acting."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)