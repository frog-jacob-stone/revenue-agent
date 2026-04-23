from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/memories", tags=["memories"])


@router.post("")
async def create_memory():
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/search")
async def search_memories():
    raise HTTPException(status_code=501, detail="Not implemented")
