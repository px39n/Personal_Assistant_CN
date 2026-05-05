from fastapi import APIRouter
from pydantic import BaseModel
from typing import Dict, Any

from app.engine.memory import memory_store

router = APIRouter(prefix="/api/memory", tags=["memory"])

class MemoryUpdateRequest(BaseModel):
    user_id: str
    key: str
    value: Any

@router.get("/{user_id}")
async def get_user_memory(user_id: str):
    """获取用户的全局记忆/偏好"""
    memory = await memory_store.get_all_global(user_id)
    return {"user_id": user_id, "memory": memory}

@router.post("/update")
async def update_user_memory(req: MemoryUpdateRequest):
    """更新用户的全局记忆/偏好"""
    await memory_store.set_global(req.user_id, req.key, req.value)
    current_memory = await memory_store.get_all_global(req.user_id)
    return {"success": True, "memory": current_memory}

@router.delete("/{user_id}/{key}")
async def delete_user_memory_key(user_id: str, key: str):
    """删除用户全局记忆中的某个 key"""
    current_memory = await memory_store.get_all_global(user_id)
    
    if key not in current_memory:
        return {"success": False, "error": f"Key '{key}' not found in memory"}

    await memory_store.delete_global(user_id, key)

    updated = await memory_store.get_all_global(user_id)
    return {"success": True, "memory": updated}
