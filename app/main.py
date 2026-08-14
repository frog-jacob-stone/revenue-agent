import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.auth import get_current_user
from app.config import _redact_dsn, config_warnings, settings
from app.db import close_pool, get_pool
from app.routers import (
    agents,
    approvals,
    audit_log,
    billing,
    chat,
    client_exclusions,
    llm_calls,
    projects,
)
from app.seed import seed_agents
from app.services.chat_sessions import mark_orphaned_streaming_failed

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # First line of every revision's log. `env` in particular is worth stating
    # out loud: the production config guard keys off it, so a revision that says
    # `env=development` is one where none of those checks ran. The DSN is
    # redacted — it carries the Postgres password.
    logger.info(
        "starting env=%s db=%s allowed_origins=%s",
        settings.env,
        _redact_dsn(settings.database_url.get_secret_value()),
        settings.allowed_origins,
    )
    for warning in config_warnings:
        logger.warning("config: %s", warning)

    pool = await get_pool()
    orphaned = await mark_orphaned_streaming_failed(pool)
    if orphaned:
        logger.info("Marked %d orphaned streaming chat message(s) as failed", orphaned)
    await seed_agents()
    yield
    await close_pool()


app = FastAPI(
    title="Revenue Agents API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

_auth = [Depends(get_current_user)]

app.include_router(agents.router, dependencies=_auth)
app.include_router(audit_log.router, dependencies=_auth)
app.include_router(llm_calls.router, dependencies=_auth)
app.include_router(chat.router, dependencies=_auth)
app.include_router(approvals.router, dependencies=_auth)
app.include_router(billing.router, dependencies=_auth)
app.include_router(projects.router, dependencies=_auth)
app.include_router(client_exclusions.router, dependencies=_auth)


@app.get("/healthz")
async def health():
    """Liveness. Answers one question: is this process still serving?

    Deliberately touches nothing. A liveness probe that checks the database
    turns a ten-second Postgres blip into a container restart, and at one replica
    a restart is an outage — one that also kills every in-flight chat turn and
    approval executor. Use /readyz for dependency health.
    """
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    """Readiness and startup. Can this process actually serve a request?

    Checks Postgres and nothing else. Harvest, Airtable and OpenAI are
    deliberately excluded: a third-party outage should degrade the features that
    need it, not pull the whole app out of rotation. The body stays empty of
    version and config detail because this route is unauthenticated.
    """
    try:
        pool = await get_pool()
        await asyncio.wait_for(pool.fetchval("select 1"), timeout=2.0)
    except Exception:
        logger.exception("readiness check failed")
        raise HTTPException(status_code=503, detail="not ready") from None
    return {"status": "ready"}
