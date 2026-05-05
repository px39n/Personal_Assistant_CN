"""记忆持久化模型 — PostgreSQL 表定义。"""

import time

from sqlalchemy import Column, Float, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.engine.db import Base


class GlobalMemory(Base):
    """全局记忆表 — 用户级别，跨 Skill 跨会话"""
    __tablename__ = "global_memory"

    user_id = Column(String(64), primary_key=True)
    key = Column(String(256), primary_key=True)
    value = Column(JSONB, nullable=False)
    created_at = Column(Float, default=time.time)
    updated_at = Column(Float, default=time.time, onupdate=time.time)


class SkillMemory(Base):
    """Skill 级记忆表 — 特定 Skill 范围，跨会话"""
    __tablename__ = "skill_memory"

    user_id = Column(String(64), primary_key=True)
    skill_name = Column(String(128), primary_key=True)
    key = Column(String(256), primary_key=True)
    value = Column(JSONB, nullable=False)
    created_at = Column(Float, default=time.time)
    updated_at = Column(Float, default=time.time, onupdate=time.time)

    __table_args__ = (
        Index("ix_skill_memory_user_skill", "user_id", "skill_name"),
    )


class ChatMessage(Base):
    """对话消息表 — 持久化对话历史"""
    __tablename__ = "chat_messages"

    id = Column(String(64), primary_key=True)  # uuid
    conversation_id = Column(String(64), nullable=False, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    role = Column(String(16), nullable=False)  # user / assistant / system
    content = Column(Text, nullable=False)
    timestamp = Column(Float, default=time.time)

    __table_args__ = (
        Index("ix_chat_messages_conv_ts", "conversation_id", "timestamp"),
    )
