import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db import close_pool, get_pool
from app.routers import actions, agents, memories, workflows

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    yield
    await close_pool()


app = FastAPI(
    title="Revenue Agents API",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(workflows.router)
app.include_router(actions.router)
app.include_router(agents.router)
app.include_router(memories.router)


@app.get("/healthz")
async def health():
    return {"status": "ok"}
