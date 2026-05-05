"""飞书 Channel — 企业自建应用 Bot 消息收发，支持实时状态更新。

消息流程:
1. 收到飞书消息 → 解析为 IncomingMessage
2. 立即回复 "🤔 正在思考..." 占位消息
3. 处理过程中实时 PATCH 更新状态（⚙️ 正在查询... / ✍️ 正在生成...）
4. 最终 PATCH 为完整回复内容
用户全程只看到一条消息在变化。
"""

import asyncio
import json
import threading
import time
from typing import Any, Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageReceiveV1,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from loguru import logger

from app.channels.base import (
    BaseChannel,
    ChannelEvent,
    ChannelType,
    IncomingMessage,
    MessageType,
    OutgoingMessage,
)


STATUS_MAP = {
    "正在分析您的需求": "🤔 正在思考...",
    "正在使用工具": "⚙️",
    "执行": "⚙️",
    "正在生成回复": "✍️ 正在生成回复...",
}


def _status_text(raw: str) -> str:
    """将引擎状态文本转为用户友好的飞书状态"""
    for key, icon in STATUS_MAP.items():
        if key in raw:
            if icon.endswith("..."):
                return icon
            return f"{icon} {raw}"
    return f"⏳ {raw}"


class FeishuChannel(BaseChannel):

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        verification_token: Optional[str] = None,
        encrypt_key: Optional[str] = None,
    ):
        super().__init__(ChannelType.FEISHU)
        self._app_id = app_id
        self._app_secret = app_secret
        self._verification_token = verification_token or ""
        self._encrypt_key = encrypt_key or ""

        self._lark_client: Optional[lark.Client] = None
        self._ws_client: Optional[lark.ws.Client] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

        self._processed_events: set[str] = set()
        self._event_timestamps: dict[str, float] = {}
        self._max_events = 200

        self._msg_received_count = 0
        self._msg_sent_count = 0
        self._last_error: Optional[str] = None

    async def on_startup(self) -> None:
        self._main_loop = asyncio.get_running_loop()
        self._lark_client = (
            lark.Client.builder()
            .app_id(self._app_id)
            .app_secret(self._app_secret)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
        self._start_ws_client()
        logger.info(f"FeishuChannel 已启动 (长连接模式, app_id={self._app_id[:8]}...)")

    async def on_shutdown(self) -> None:
        logger.info("FeishuChannel 已关闭")

    # ── 长连接 ──────────────────────────────

    def _start_ws_client(self) -> None:
        handler = (
            lark.EventDispatcherHandler.builder(
                self._verification_token, self._encrypt_key,
            )
            .register_p2_im_message_receive_v1(self._on_message_receive)
            .build()
        )
        self._ws_client = lark.ws.Client(
            app_id=self._app_id, app_secret=self._app_secret,
            event_handler=handler, log_level=lark.LogLevel.WARNING,
            auto_reconnect=True,
        )

        def _run_ws():
            import lark_oapi.ws.client as ws_mod
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            ws_mod.loop = new_loop
            try:
                self._ws_client.start()
            except Exception as e:
                logger.error(f"飞书长连接异常退出: {e}", exc_info=True)

        self._ws_thread = threading.Thread(target=_run_ws, name="feishu-ws", daemon=True)
        self._ws_thread.start()
        logger.info("飞书长连接线程已启动")

    def _on_message_receive(self, data: P2ImMessageReceiveV1) -> None:
        try:
            event = data.event
            message = event.message
            sender = event.sender

            if message.message_type != "text":
                return

            try:
                text = json.loads(message.content).get("text", "").strip()
            except (json.JSONDecodeError, AttributeError):
                text = (message.content or "").strip()

            if not text:
                return

            user_id = sender.sender_id.open_id or sender.sender_id.user_id or "unknown"

            incoming = IncomingMessage(
                message_id=message.message_id,
                channel_type=ChannelType.FEISHU,
                user_id=user_id,
                conversation_id=message.chat_id,
                content=text,
                message_type=MessageType.TEXT,
                metadata={
                    "chat_id": message.chat_id,
                    "message_id": message.message_id,
                    "sender_type": sender.sender_type,
                    "chat_type": message.chat_type,
                },
            )

            self._msg_received_count += 1
            logger.info(f"[飞书] 收到: user={user_id} msg={text[:50]}")

            if self._main_loop and self._main_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._process_and_reply(incoming), self._main_loop,
                )

        except Exception as e:
            logger.error(f"飞书消息解析异常: {e}", exc_info=True)

    # ── 核心处理流程（卡片消息实时状态更新）──────

    def _card_json(self, text: str) -> str:
        """构建飞书交互卡片 JSON（支持 markdown）"""
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [
                {"tag": "markdown", "content": text}
            ],
        }
        return json.dumps(card, ensure_ascii=False)

    @staticmethod
    def build_report_card(text: str, current_freq: str = "open_only") -> str:
        """构建带频率按钮的持仓快报卡片"""
        buttons = []
        for key, label in [
            ("open_only", "仅开盘"),
            ("30min", "每30分"),
            ("1h", "每1小时"),
            ("3h", "每3小时"),
            ("off", "关闭"),
        ]:
            btn = {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"{'✓ ' if key == current_freq else ''}{label}"},
                "type": "primary" if key == current_freq else "default",
                "value": {"action": "set_push_frequency", "frequency": key},
            }
            buttons.append(btn)

        card = {
            "config": {"wide_screen_mode": True},
            "elements": [
                {"tag": "markdown", "content": text},
                {"tag": "hr"},
                {"tag": "markdown", "content": "**推送频率设置:**"},
                {"tag": "action", "actions": buttons},
            ],
        }
        return json.dumps(card, ensure_ascii=False)

    async def _reply_card_and_get_id(self, message_id: str, text: str) -> Optional[str]:
        """回复一条卡片消息，返回 message_id"""
        if not message_id:
            return None
        try:
            request = (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .msg_type("interactive")
                    .content(self._card_json(text))
                    .build()
                ).build()
            )
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, self._lark_client.im.v1.message.reply, request,
            )
            if response.success():
                return response.data.message_id
            logger.error(f"飞书卡片回复失败: code={response.code} msg={response.msg}")
            return None
        except Exception as e:
            logger.error(f"飞书卡片回复异常: {e}", exc_info=True)
            return None

    async def _patch_card(self, message_id: str, text: str) -> bool:
        """更新卡片消息内容"""
        try:
            request = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    PatchMessageRequestBody.builder()
                    .content(self._card_json(text))
                    .build()
                ).build()
            )
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, self._lark_client.im.v1.message.patch, request,
            )
            if not response.success():
                logger.warning(f"飞书卡片更新失败: code={response.code} msg={response.msg}")
                return False
            return True
        except Exception as e:
            logger.warning(f"飞书卡片更新异常: {e}")
            return False

    async def _process_and_reply(self, incoming: IncomingMessage) -> None:
        from app.channels.dispatcher import dispatch

        reply_to = incoming.metadata.get("message_id")

        card_id = await self._reply_card_and_get_id(reply_to, "🤔 正在思考...")

        full_response = ""
        image_keys = []
        freq_buttons = None  # {"current_freq": "open_only"} if portfolio wants buttons
        try:
            async for event in dispatch(incoming, stream=False):
                etype = event.event_type

                if etype == "status" and card_id:
                    await self._patch_card(card_id, _status_text(str(event.data)))

                elif etype == "message":
                    full_response += str(event.data)

                elif etype == "skill_result":
                    data = event.data
                    if isinstance(data, dict) and data.get("ui_card"):
                        ui_card = data["ui_card"]
                        if ui_card.get("type") == "freq_buttons":
                            freq_buttons = ui_card
                        img_b64 = ui_card.get("image", "")
                        if img_b64 and img_b64.startswith("data:image/png;base64,"):
                            key = await self._upload_image_b64(img_b64.split(",", 1)[1])
                            if key:
                                image_keys.append(key)

        except Exception as e:
            self._last_error = str(e)
            logger.error(f"[飞书处理] 异常: {e}", exc_info=True)

        if not full_response:
            full_response = "抱歉，处理出错了，请稍后再试。"

        if freq_buttons:
            card_content = self.build_report_card(full_response, freq_buttons.get("current_freq", "open_only"))
        else:
            card_content = self._build_final_card(full_response, image_keys)
        if card_id:
            ok = await self._patch_card_raw(card_id, card_content)
            if ok:
                self._msg_sent_count += 1
                logger.info(f"[飞书回复] 卡片更新成功: {full_response[:50]}...")
                return

        reply = OutgoingMessage(
            conversation_id=incoming.conversation_id or "",
            content=full_response, metadata=incoming.metadata,
        )
        await self.send(reply, reply_to=reply_to)
        self._msg_sent_count += 1

    def _build_final_card(self, text: str, image_keys: list[str]) -> str:
        """构建包含文字和图片的最终卡片"""
        elements = [{"tag": "markdown", "content": text}]
        for key in image_keys:
            elements.append({
                "tag": "img",
                "img_key": key,
                "alt": {"tag": "plain_text", "content": "K线图"},
            })
        card = {"config": {"wide_screen_mode": True}, "elements": elements}
        return json.dumps(card, ensure_ascii=False)

    async def _upload_image_b64(self, b64_data: str) -> Optional[str]:
        """上传 base64 图片到飞书，返回 image_key"""
        import base64
        import tempfile
        import os
        try:
            img_bytes = base64.b64decode(b64_data)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(img_bytes)
                tmp_path = f.name

            request = (
                CreateImageRequest.builder()
                .request_body(
                    CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(open(tmp_path, "rb"))
                    .build()
                ).build()
            )
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, self._lark_client.im.v1.image.create, request,
            )
            os.unlink(tmp_path)

            if response.success():
                key = response.data.image_key
                logger.info(f"[飞书] 图片上传成功: {key}")
                return key
            logger.error(f"[飞书] 图片上传失败: code={response.code} msg={response.msg}")
            return None
        except Exception as e:
            logger.error(f"[飞书] 图片上传异常: {e}", exc_info=True)
            return None

    # ── 消息操作 ──────────────────────────────

    async def _reply_and_get_id(self, message_id: str, text: str) -> Optional[str]:
        """回复消息并返回新消息的 message_id"""
        if not message_id:
            return None
        try:
            content = json.dumps({"text": text}, ensure_ascii=False)
            request = (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .msg_type("text").content(content).build()
                ).build()
            )
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, self._lark_client.im.v1.message.reply, request,
            )
            if response.success():
                return response.data.message_id
            logger.error(f"飞书回复失败: code={response.code} msg={response.msg}")
            return None
        except Exception as e:
            logger.error(f"飞书回复异常: {e}", exc_info=True)
            return None

    async def _send_and_get_id(self, chat_id: str, text: str) -> Optional[str]:
        """发送消息并返回 message_id"""
        if not chat_id:
            return None
        try:
            content = json.dumps({"text": text}, ensure_ascii=False)
            request = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id).msg_type("text").content(content).build()
                ).build()
            )
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, self._lark_client.im.v1.message.create, request,
            )
            if response.success():
                return response.data.message_id
            logger.error(f"飞书发送失败: code={response.code} msg={response.msg}")
            return None
        except Exception as e:
            logger.error(f"飞书发送异常: {e}", exc_info=True)
            return None

    async def _patch_message(self, message_id: str, text: str) -> bool:
        """更新已发送消息的内容"""
        return await self._patch_card_raw(message_id, self._card_json(text))

    async def _patch_card_raw(self, message_id: str, card_json: str) -> bool:
        """更新卡片消息（原始 JSON）"""
        try:
            request = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    PatchMessageRequestBody.builder().content(card_json).build()
                ).build()
            )
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, self._lark_client.im.v1.message.patch, request,
            )
            if not response.success():
                logger.warning(f"飞书卡片更新失败: code={response.code} msg={response.msg}")
                return False
            return True
        except Exception as e:
            logger.warning(f"飞书卡片更新异常: {e}")
            return False

    # ── 兼容接口 ──────────────────────────────

    async def send(self, message: OutgoingMessage, **kwargs) -> bool:
        reply_to = kwargs.get("reply_to") or message.metadata.get("message_id")
        chat_id = kwargs.get("chat_id") or message.metadata.get("chat_id")
        content = json.dumps({"text": message.content}, ensure_ascii=False)

        if reply_to:
            mid = await self._reply_and_get_id(reply_to, message.content)
            return mid is not None
        elif chat_id:
            mid = await self._send_and_get_id(chat_id, message.content)
            return mid is not None
        else:
            logger.error("飞书发送消息缺少 reply_to 或 chat_id")
            return False

    async def send_event(self, event: ChannelEvent, **kwargs) -> None:
        pass

    # ── Webhook 模式（备选）──────────────────

    def verify_url_challenge(self, body: dict) -> Optional[dict]:
        if body.get("type") == "url_verification":
            return {"challenge": body.get("challenge", "")}
        return None

    def is_duplicate_event(self, event_id: str) -> bool:
        if event_id in self._processed_events:
            return True
        self._processed_events.add(event_id)
        self._event_timestamps[event_id] = time.time()
        if len(self._processed_events) > self._max_events:
            now = time.time()
            expired = [eid for eid, ts in self._event_timestamps.items() if now - ts > 300]
            for eid in expired:
                self._processed_events.discard(eid)
                self._event_timestamps.pop(eid, None)
        return False

    @staticmethod
    def parse_webhook_event(body: dict) -> Optional[IncomingMessage]:
        header = body.get("header", {})
        event = body.get("event", {})
        if header.get("event_type") != "im.message.receive_v1":
            return None
        message = event.get("message", {})
        sender = event.get("sender", {})
        if message.get("message_type") != "text":
            return None
        try:
            text = json.loads(message.get("content", "{}")).get("text", "").strip()
        except (json.JSONDecodeError, AttributeError):
            text = ""
        if not text:
            return None
        sender_id = sender.get("sender_id", {})
        user_id = sender_id.get("open_id", sender_id.get("user_id", "unknown"))
        return IncomingMessage(
            message_id=message.get("message_id", ""),
            channel_type=ChannelType.FEISHU,
            user_id=user_id,
            conversation_id=message.get("chat_id", ""),
            content=text, message_type=MessageType.TEXT,
            metadata={
                "chat_id": message.get("chat_id", ""),
                "message_id": message.get("message_id", ""),
                "sender_type": sender.get("sender_type", ""),
                "chat_type": message.get("chat_type", ""),
            },
        )
