"""记忆存储后端 — Redis（会话级）+ PostgreSQL（全局/Skill 级）。"""

import json
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, Optional

import redis.asyncio as aioredis
from loguru import logger
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import ChatMessage, GlobalMemory, SkillMemory


# ──────────────────────────────────────────
# 抽象接口
# ──────────────────────────────────────────

class SessionBackend(ABC):
    """会话级存储接口（会话变量 + 对话历史）"""

    @abstractmethod
    async def set_session(self, conversation_id: str, key: str, value: Any) -> None: ...

    @abstractmethod
    async def get_session(self, conversation_id: str, key: str, default: Any = None) -> Any: ...

    @abstractmethod
    async def clear_session(self, conversation_id: str) -> None: ...

    @abstractmethod
    async def add_message(self, conversation_id: str, role: str, content: str, user_id: str = "") -> None: ...

    @abstractmethod
    async def get_chat_history(self, conversation_id: str, limit: Optional[int] = None) -> list[dict]: ...


class PersistentBackend(ABC):
    """持久化存储接口（全局 + Skill 级记忆）"""

    @abstractmethod
    async def set_global(self, user_id: str, key: str, value: Any) -> None: ...

    @abstractmethod
    async def get_global(self, user_id: str, key: str, default: Any = None) -> Any: ...

    @abstractmethod
    async def get_all_global(self, user_id: str) -> dict[str, Any]: ...

    @abstractmethod
    async def set_skill(self, user_id: str, skill_name: str, key: str, value: Any) -> None: ...

    @abstractmethod
    async def get_skill(self, user_id: str, skill_name: str, key: str, default: Any = None) -> Any: ...

    @abstractmethod
    async def get_all_skill(self, user_id: str, skill_name: str) -> dict[str, Any]: ...

    @abstractmethod
    async def delete_global(self, user_id: str, key: str) -> None: ...

    @abstractmethod
    async def save_message(self, conversation_id: str, role: str, content: str, user_id: str = "") -> None: ...

    @abstractmethod
    async def load_chat_history(self, conversation_id: str, limit: Optional[int] = None) -> list[dict]: ...


# ──────────────────────────────────────────
# Redis 会话后端
# ──────────────────────────────────────────

class RedisSessionBackend(SessionBackend):
    """Redis 会话存储 — 快速、支持 TTL 自动过期"""

    SESSION_TTL = 86400  # 会话变量 24 小时过期
    HISTORY_TTL = 604800  # 对话历史 7 天过期

    def __init__(self, redis_url: str):
        self._redis = aioredis.from_url(redis_url, decode_responses=True)

    def _session_key(self, conv_id: str) -> str:
        return f"session:{conv_id}"

    def _history_key(self, conv_id: str) -> str:
        return f"history:{conv_id}"

    async def set_session(self, conversation_id: str, key: str, value: Any) -> None:
        redis_key = self._session_key(conversation_id)
        await self._redis.hset(redis_key, key, json.dumps(value, ensure_ascii=False))
        await self._redis.expire(redis_key, self.SESSION_TTL)

    async def get_session(self, conversation_id: str, key: str, default: Any = None) -> Any:
        raw = await self._redis.hget(self._session_key(conversation_id), key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    async def clear_session(self, conversation_id: str) -> None:
        await self._redis.delete(
            self._session_key(conversation_id),
            self._history_key(conversation_id),
        )

    async def add_message(self, conversation_id: str, role: str, content: str, user_id: str = "") -> None:
        msg = json.dumps({
            "role": role,
            "content": content,
            "user_id": user_id,
            "timestamp": time.time(),
        }, ensure_ascii=False)
        key = self._history_key(conversation_id)
        await self._redis.rpush(key, msg)
        await self._redis.expire(key, self.HISTORY_TTL)

    async def get_chat_history(self, conversation_id: str, limit: Optional[int] = None) -> list[dict]:
        key = self._history_key(conversation_id)
        if limit:
            raw_list = await self._redis.lrange(key, -limit, -1)
        else:
            raw_list = await self._redis.lrange(key, 0, -1)
        return [json.loads(r) for r in raw_list]

    async def close(self):
        await self._redis.close()


# ──────────────────────────────────────────
# PostgreSQL 持久化后端
# ──────────────────────────────────────────

class PostgresPersistentBackend(PersistentBackend):
    """PostgreSQL 全局/Skill 级记忆 — 持久、可查询"""

    def __init__(self, session_factory):
        self._session_factory = session_factory

    async def _get_session(self) -> AsyncSession:
        return self._session_factory()

    async def set_global(self, user_id: str, key: str, value: Any) -> None:
        async with await self._get_session() as session:
            stmt = pg_insert(GlobalMemory).values(
                user_id=user_id, key=key, value=value,
                created_at=time.time(), updated_at=time.time(),
            ).on_conflict_do_update(
                index_elements=["user_id", "key"],
                set_={"value": value, "updated_at": time.time()},
            )
            await session.execute(stmt)
            await session.commit()

    async def get_global(self, user_id: str, key: str, default: Any = None) -> Any:
        async with await self._get_session() as session:
            result = await session.execute(
                select(GlobalMemory.value).where(
                    GlobalMemory.user_id == user_id,
                    GlobalMemory.key == key,
                )
            )
            row = result.scalar_one_or_none()
            return row if row is not None else default

    async def get_all_global(self, user_id: str) -> dict[str, Any]:
        async with await self._get_session() as session:
            result = await session.execute(
                select(GlobalMemory.key, GlobalMemory.value).where(
                    GlobalMemory.user_id == user_id
                )
            )
            return {row.key: row.value for row in result.all()}

    async def set_skill(self, user_id: str, skill_name: str, key: str, value: Any) -> None:
        async with await self._get_session() as session:
            stmt = pg_insert(SkillMemory).values(
                user_id=user_id, skill_name=skill_name, key=key, value=value,
                created_at=time.time(), updated_at=time.time(),
            ).on_conflict_do_update(
                index_elements=["user_id", "skill_name", "key"],
                set_={"value": value, "updated_at": time.time()},
            )
            await session.execute(stmt)
            await session.commit()

    async def get_skill(self, user_id: str, skill_name: str, key: str, default: Any = None) -> Any:
        async with await self._get_session() as session:
            result = await session.execute(
                select(SkillMemory.value).where(
                    SkillMemory.user_id == user_id,
                    SkillMemory.skill_name == skill_name,
                    SkillMemory.key == key,
                )
            )
            row = result.scalar_one_or_none()
            return row if row is not None else default

    async def get_all_skill(self, user_id: str, skill_name: str) -> dict[str, Any]:
        async with await self._get_session() as session:
            result = await session.execute(
                select(SkillMemory.key, SkillMemory.value).where(
                    SkillMemory.user_id == user_id,
                    SkillMemory.skill_name == skill_name,
                )
            )
            return {row.key: row.value for row in result.all()}

    async def delete_global(self, user_id: str, key: str) -> None:
        async with await self._get_session() as session:
            await session.execute(
                delete(GlobalMemory).where(
                    GlobalMemory.user_id == user_id,
                    GlobalMemory.key == key,
                )
            )
            await session.commit()

    async def save_message(self, conversation_id: str, role: str, content: str, user_id: str = "") -> None:
        async with await self._get_session() as session:
            msg = ChatMessage(
                id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                user_id=user_id,
                role=role,
                content=content,
                timestamp=time.time(),
            )
            session.add(msg)
            await session.commit()

    async def load_chat_history(self, conversation_id: str, limit: Optional[int] = None) -> list[dict]:
        async with await self._get_session() as session:
            query = (
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.timestamp.asc())
            )
            result = await session.execute(query)
            rows = result.scalars().all()

            messages = [
                {
                    "role": r.role,
                    "content": r.content,
                    "user_id": r.user_id,
                    "timestamp": r.timestamp,
                }
                for r in rows
            ]

            if limit:
                return messages[-limit:]
            return messages
