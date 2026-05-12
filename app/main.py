import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import get_current_user
from app.config import settings
from app.db import close_pool, get_pool
from app.db_security import lock_down_langgraph_tables
from app.orchestrator.graphs import register_all as register_graphs
from app.orchestrator.runner import runner
from app.services.chat_sessions import mark_orphaned_streaming_failed
from app.routers import (
    agents,
    analytics,
    approvals,
    audit_log,
    chat,
    llm_calls,
    memories,
    workflows,
)
from app.seed import seed_agents, seed_voice_profile

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await get_pool()
    orphaned = await mark_orphaned_streaming_failed(pool)
    if orphaned:
        logging.getLogger(__name__).info(
            "Marked %d orphaned streaming chat message(s) as failed", orphaned
        )
    await seed_agents()
    await seed_voice_profile()
    await runner.init()
    register_graphs(runner)
    await lock_down_langgraph_tables(pool)
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

app.include_router(workflows.router, dependencies=_auth)
app.include_router(analytics.router, dependencies=_auth)
app.include_router(agents.router, dependencies=_auth)
app.include_router(audit_log.router, dependencies=_auth)
app.include_router(llm_calls.router, dependencies=_auth)
app.include_router(memories.router, dependencies=_auth)
app.include_router(chat.router, dependencies=_auth)
app.include_router(approvals.router, dependencies=_auth)


@app.get("/healthz")
async def health():
    return {"status": "ok"}
