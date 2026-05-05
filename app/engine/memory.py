"""三层记忆管理 — 全局记忆、Skill 级记忆、会话级记忆。

支持两种后端:
- 内存 (InMemoryStore): 开发/测试，无外部依赖
- Redis + PostgreSQL (PersistentMemoryStore): 生产环境
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger


@dataclass
class MemoryEntry:
    """记忆条目"""
    key: str
    value: Any
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class MemoryStore:
    """
    三层记忆系统（内存版 — 开发/测试用）:

    1. 全局记忆 (global): 用户级别，跨所有 Skill 和会话
       例: 用户名、常用地址、支付偏好

    2. Skill 记忆 (skill): 特定 Skill 范围内，跨会话
       例: flight_booking skill 记住 "常坐南航经济舱靠窗"

    3. 会话记忆 (session): 当前对话，会话结束后可归档
       例: "刚才说的那个航班"、对话历史
    """

    def __init__(self):
        # {user_id: {key: MemoryEntry}}
        self._global: dict[str, dict[str, MemoryEntry]] = defaultdict(dict)
        # {user_id: {skill_name: {key: MemoryEntry}}}
        self._skill: dict[str, dict[str, dict[str, MemoryEntry]]] = defaultdict(lambda: defaultdict(dict))
        # {conversation_id: {key: MemoryEntry}}
        self._session: dict[str, dict[str, MemoryEntry]] = defaultdict(dict)
        # {conversation_id: list[dict]}  对话历史
        self._chat_history: dict[str, list[dict]] = defaultdict(list)

    # --- 全局记忆 ---
    async def set_global(self, user_id: str, key: str, value: Any) -> None:
        now = time.time()
        if key in self._global[user_id]:
            self._global[user_id][key].value = value
            self._global[user_id][key].updated_at = now
        else:
            self._global[user_id][key] = MemoryEntry(key=key, value=value, created_at=now, updated_at=now)

    async def get_global(self, user_id: str, key: str, default: Any = None) -> Any:
        entry = self._global.get(user_id, {}).get(key)
        return entry.value if entry else default

    async def get_all_global(self, user_id: str) -> dict[str, Any]:
        return {k: v.value for k, v in self._global.get(user_id, {}).items()}

    async def delete_global(self, user_id: str, key: str) -> None:
        self._global.get(user_id, {}).pop(key, None)

    # --- Skill 级记忆 ---
    async def set_skill(self, user_id: str, skill_name: str, key: str, value: Any) -> None:
        now = time.time()
        store = self._skill[user_id][skill_name]
        if key in store:
            store[key].value = value
            store[key].updated_at = now
        else:
            store[key] = MemoryEntry(key=key, value=value, created_at=now, updated_at=now)

    async def get_skill(self, user_id: str, skill_name: str, key: str, default: Any = None) -> Any:
        entry = self._skill.get(user_id, {}).get(skill_name, {}).get(key)
        return entry.value if entry else default

    async def get_all_skill(self, user_id: str, skill_name: str) -> dict[str, Any]:
        return {k: v.value for k, v in self._skill.get(user_id, {}).get(skill_name, {}).items()}

    # --- 会话记忆 ---
    async def set_session(self, conversation_id: str, key: str, value: Any) -> None:
        now = time.time()
        store = self._session[conversation_id]
        if key in store:
            store[key].value = value
            store[key].updated_at = now
        else:
            store[key] = MemoryEntry(key=key, value=value, created_at=now, updated_at=now)

    async def get_session(self, conversation_id: str, key: str, default: Any = None) -> Any:
        entry = self._session.get(conversation_id, {}).get(key)
        return entry.value if entry else default

    async def clear_session(self, conversation_id: str) -> None:
        self._session.pop(conversation_id, None)
        self._chat_history.pop(conversation_id, None)

    # --- 对话历史 ---
    async def add_message(self, conversation_id: str, role: str, content: str, user_id: str = "") -> None:
        self._chat_history[conversation_id].append({
            "role": role,
            "content": content,
            "user_id": user_id,
            "timestamp": time.time(),
        })

    async def get_chat_history(self, conversation_id: str, limit: Optional[int] = None) -> list[dict]:
        history = self._chat_history.get(conversation_id, [])
        if limit:
            return history[-limit:]
        return history

    async def build_skill_context(self, user_id: str, skill_name: str, conversation_id: str) -> dict:
        """构建传给 Skill 的完整记忆上下文"""
        return {
            "global_memory": await self.get_all_global(user_id),
            "skill_memory": await self.get_all_skill(user_id, skill_name),
            "chat_history": await self.get_chat_history(conversation_id, limit=20),
        }


class PersistentMemoryStore:
    """
    持久化记忆系统 — Redis（会话） + PostgreSQL（全局/Skill）。

    接口与 MemoryStore 完全一致（均为 async），可无缝切换。
    """

    def __init__(self, session_backend, persistent_backend):
        from app.engine.memory_backends import PersistentBackend, SessionBackend
        self._session_be: SessionBackend = session_backend
        self._persistent_be: PersistentBackend = persistent_backend

    # --- 全局记忆 (PostgreSQL) ---
    async def set_global(self, user_id: str, key: str, value: Any) -> None:
        await self._persistent_be.set_global(user_id, key, value)

    async def get_global(self, user_id: str, key: str, default: Any = None) -> Any:
        return await self._persistent_be.get_global(user_id, key, default)

    async def get_all_global(self, user_id: str) -> dict[str, Any]:
        return await self._persistent_be.get_all_global(user_id)

    async def delete_global(self, user_id: str, key: str) -> None:
        await self._persistent_be.delete_global(user_id, key)

    # --- Skill 级记忆 (PostgreSQL) ---
    async def set_skill(self, user_id: str, skill_name: str, key: str, value: Any) -> None:
        await self._persistent_be.set_skill(user_id, skill_name, key, value)

    async def get_skill(self, user_id: str, skill_name: str, key: str, default: Any = None) -> Any:
        return await self._persistent_be.get_skill(user_id, skill_name, key, default)

    async def get_all_skill(self, user_id: str, skill_name: str) -> dict[str, Any]:
        return await self._persistent_be.get_all_skill(user_id, skill_name)

    # --- 会话记忆 (Redis) ---
    async def set_session(self, conversation_id: str, key: str, value: Any) -> None:
        await self._session_be.set_session(conversation_id, key, value)

    async def get_session(self, conversation_id: str, key: str, default: Any = None) -> Any:
        return await self._session_be.get_session(conversation_id, key, default)

    async def clear_session(self, conversation_id: str) -> None:
        await self._session_be.clear_session(conversation_id)

    # --- 对话历史 (Redis + PostgreSQL 双写) ---
    async def add_message(self, conversation_id: str, role: str, content: str, user_id: str = "") -> None:
        await self._session_be.add_message(conversation_id, role, content, user_id=user_id)
        try:
            await self._persistent_be.save_message(conversation_id, role, content, user_id=user_id)
        except Exception as e:
            logger.warning(f"持久化对话消息失败 (不影响会话): {e}")

    async def get_chat_history(self, conversation_id: str, limit: Optional[int] = None) -> list[dict]:
        history = await self._session_be.get_chat_history(conversation_id, limit)
        if history:
            return history
        return await self._persistent_be.load_chat_history(conversation_id, limit)

    async def build_skill_context(self, user_id: str, skill_name: str, conversation_id: str) -> dict:
        """构建传给 Skill 的完整记忆上下文"""
        return {
            "global_memory": await self.get_all_global(user_id),
            "skill_memory": await self.get_all_skill(user_id, skill_name),
            "chat_history": await self.get_chat_history(conversation_id, limit=20),
        }


def create_memory_store(mode: str = "memory") -> MemoryStore | PersistentMemoryStore:
    """
    创建记忆存储实例。

    Args:
        mode: "memory" (内存) 或 "persistent" (Redis + PostgreSQL)
    """
    if mode == "persistent":
        try:
            from app.config import settings
            from app.engine.db import async_session_factory
            from app.engine.memory_backends import (
                PostgresPersistentBackend,
                RedisSessionBackend,
            )

            session_be = RedisSessionBackend(settings.redis_url)
            persistent_be = PostgresPersistentBackend(async_session_factory)
            logger.info("记忆系统: Redis + PostgreSQL 持久化模式")
            return PersistentMemoryStore(session_be, persistent_be)
        except Exception as e:
            logger.warning(f"持久化记忆初始化失败，回退到内存模式: {e}")
            return MemoryStore()
    else:
        logger.info("记忆系统: 内存模式")
        return MemoryStore()


# 全局单例 — 默认内存模式，应用启动时可切换
memory_store: MemoryStore | PersistentMemoryStore = MemoryStore()
