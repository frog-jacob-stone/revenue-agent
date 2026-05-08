import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import close_pool, get_pool
from app.orchestrator.chains import register_all as register_chains
from app.orchestrator_v2.graphs import register_all as register_v2_graphs
from app.orchestrator_v2.runner import runner as v2_runner
from app.routers import (
    actions,
    agents,
    analytics,
    approvals,
    audit_log,
    chains,
    chat,
    memories,
    workflows,
)
from app.seed import seed_agents, seed_voice_profile

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    await seed_agents()
    await seed_voice_profile()
    register_chains()
    await v2_runner.init()
    register_v2_graphs(v2_runner)
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
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workflows.router)
app.include_router(actions.router)
app.include_router(analytics.router)
app.include_router(agents.router)
app.include_router(audit_log.router)
app.include_router(memories.router)
app.include_router(chat.router)
app.include_router(chains.router)
app.include_router(approvals.router)


@app.get("/healthz")
async def health():
    return {"status": "ok"}
