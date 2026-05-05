"""Channel 抽象层 — 统一消息模型和渠道基类。

Channel 负责:
1. 接收来自特定平台的消息，转为统一格式
2. 将引擎的响应转发回特定平台
3. 管理平台特定的认证和连接
"""

import time
import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, AsyncGenerator, Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────
# 统一消息模型
# ──────────────────────────────────────────

class MessageType(str, Enum):
    """消息类型"""
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    AUDIO = "audio"
    CARD = "card"  # 富文本卡片（飞书、钉钉等）


class ChannelType(str, Enum):
    """渠道类型"""
    WEB = "web"
    FEISHU = "feishu"
    DINGTALK = "dingtalk"
    WECHAT = "wechat"
    API = "api"  # 纯 API 调用


class IncomingMessage(BaseModel):
    """统一的入站消息 — 从任意渠道进入引擎"""
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    channel_type: ChannelType
    user_id: str
    conversation_id: Optional[str] = None
    content: str  # 文本内容
    message_type: MessageType = MessageType.TEXT
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)  # 平台特定数据
    timestamp: float = Field(default_factory=time.time)


class OutgoingMessage(BaseModel):
    """统一的出站消息 — 从引擎发往任意渠道"""
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str
    content: str
    message_type: MessageType = MessageType.TEXT
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)


class ChannelEvent(BaseModel):
    """渠道事件 — 流式推送用"""
    event_type: str  # status | message | skill_result | error | done | metadata
    data: Any
    conversation_id: Optional[str] = None


# ──────────────────────────────────────────
# Channel 基类
# ──────────────────────────────────────────

class BaseChannel(ABC):
    """
    渠道抽象基类。

    每个渠道实现:
    1. receive() — 将平台原始消息转为 IncomingMessage
    2. send() — 将 OutgoingMessage 发送到平台
    3. send_stream() — 流式推送事件到平台（可选）
    """

    def __init__(self, channel_type: ChannelType):
        self.channel_type = channel_type
        self._enabled = True

    @property
    def name(self) -> str:
        return self.channel_type.value

    @property
    def enabled(self) -> bool:
        return self._enabled

    @abstractmethod
    async def send(self, message: OutgoingMessage, **kwargs) -> bool:
        """发送消息到渠道，返回是否成功"""
        ...

    @abstractmethod
    async def send_event(self, event: ChannelEvent, **kwargs) -> None:
        """发送流式事件到渠道"""
        ...

    async def on_startup(self) -> None:
        """渠道启动时的初始化（注册 webhook 等）"""
        pass

    async def on_shutdown(self) -> None:
        """渠道关闭时的清理"""
        pass


# ──────────────────────────────────────────
# Channel 注册中心
# ──────────────────────────────────────────

class ChannelRegistry:
    """管理所有已注册的渠道"""

    def __init__(self):
        self._channels: dict[str, BaseChannel] = {}

    def register(self, channel: BaseChannel) -> None:
        self._channels[channel.name] = channel

    def get(self, name: str) -> Optional[BaseChannel]:
        return self._channels.get(name)

    def list_all(self) -> list[BaseChannel]:
        return list(self._channels.values())

    @property
    def count(self) -> int:
        return len(self._channels)

    async def startup_all(self) -> None:
        for ch in self._channels.values():
            await ch.on_startup()

    async def shutdown_all(self) -> None:
        for ch in self._channels.values():
            await ch.on_shutdown()


channel_registry = ChannelRegistry()
