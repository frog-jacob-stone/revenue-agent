from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("")
async def list_agents():
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/{agent_id}")
async def get_agent():
    raise HTTPException(status_code=501, detail="Not implemented")
