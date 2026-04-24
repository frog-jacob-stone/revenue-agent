import asyncpg
from fastapi import APIRouter, Depends

from app.db import get_pool
from app.services.analytics import AnalyticsResponse, get_analytics

router = APIRouter(prefix="/analytics", tags=["analytics"])


async def _db() -> asyncpg.Pool:
    return await get_pool()


@router.get("", response_model=AnalyticsResponse)
async def get_analytics_endpoint(
    days: int = 30,
    pool: asyncpg.Pool = Depends(_db),
):
    return await get_analytics(pool, days)
