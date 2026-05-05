"""Web Channel — HTTP API / SSE / WebSocket 渠道实现。"""

from typing import Any, Optional

from loguru import logger

from app.channels.base import (
    BaseChannel,
    ChannelEvent,
    ChannelType,
    IncomingMessage,
    OutgoingMessage,
)


class WebChannel(BaseChannel):
    """
    Web 渠道 — 处理来自 HTTP API 和 WebSocket 的消息。

    特点:
    - 无状态：不维护长连接（SSE/WS 由 FastAPI 路由层管理）
    - send/send_event 由调用方（API 路由）收集并推送
    """

    def __init__(self):
        super().__init__(ChannelType.WEB)
        self._pending_events: dict[str, list[ChannelEvent]] = {}

    @classmethod
    def parse_incoming(
        cls,
        message: str,
        user_id: str = "anonymous",
        conversation_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> IncomingMessage:
        """将 HTTP 请求体转为统一的 IncomingMessage"""
        return IncomingMessage(
            channel_type=ChannelType.WEB,
            user_id=user_id,
            conversation_id=conversation_id,
            content=message,
            metadata=metadata or {},
        )

    async def send(self, message: OutgoingMessage, **kwargs) -> bool:
        """Web 渠道的 send 是无操作的 — 响应直接通过 HTTP response 返回"""
        logger.debug(f"[WebChannel] send: {message.content[:50]}...")
        return True

    async def send_event(self, event: ChannelEvent, **kwargs) -> None:
        """Web 渠道的事件由 API 路由层直接流式推送"""
        logger.debug(f"[WebChannel] event: {event.event_type}")

    async def on_startup(self) -> None:
        logger.info("WebChannel 已启动")

    async def on_shutdown(self) -> None:
        logger.info("WebChannel 已关闭")
