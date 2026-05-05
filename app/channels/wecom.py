"""企业微信 Channel — 接收普通微信/企业微信消息，通过 API 回复。

消息流程:
1. 企业微信推送加密 XML 到 /api/wecom/webhook (POST)
2. AES 解密 → 解析 XML → IncomingMessage
3. Dispatcher 处理 → 收集回复
4. 通过企业微信 API 发送文字 + 图片（如有）
"""

import asyncio
import base64
import hashlib
import socket
import struct
import time
import xml.etree.ElementTree as ET
from typing import Any, Optional

import httpx
from Crypto.Cipher import AES
from loguru import logger

from app.channels.base import (
    BaseChannel,
    ChannelEvent,
    ChannelType,
    IncomingMessage,
    MessageType,
    OutgoingMessage,
)
from app.config import settings

WECOM_API = "https://qyapi.weixin.qq.com/cgi-bin"


class WeComCrypto:
    """企业微信消息加解密"""

    def __init__(self, token: str, encoding_aes_key: str, corp_id: str):
        self.token = token
        self.corp_id = corp_id
        self.aes_key = base64.b64decode(encoding_aes_key + "=")
        self.iv = self.aes_key[:16]

    def verify_signature(self, signature: str, timestamp: str, nonce: str, echostr: str = "") -> bool:
        parts = sorted([self.token, timestamp, nonce, echostr])
        sha1 = hashlib.sha1("".join(parts).encode()).hexdigest()
        return sha1 == signature

    def decrypt(self, encrypted: str) -> str:
        cipher = AES.new(self.aes_key, AES.MODE_CBC, self.iv)
        plain = cipher.decrypt(base64.b64decode(encrypted))
        pad_len = plain[-1]
        content = plain[:-pad_len]
        # content = 16 bytes random + 4 bytes msg_len + msg + corp_id
        msg_len = struct.unpack(">I", content[16:20])[0]
        msg = content[20:20 + msg_len].decode("utf-8")
        return msg

    def encrypt(self, text: str) -> str:
        random_bytes = hashlib.md5(str(time.time()).encode()).digest()
        msg_bytes = text.encode("utf-8")
        corp_bytes = self.corp_id.encode("utf-8")
        body = random_bytes + struct.pack(">I", len(msg_bytes)) + msg_bytes + corp_bytes
        pad_len = 32 - (len(body) % 32)
        body += bytes([pad_len] * pad_len)
        cipher = AES.new(self.aes_key, AES.MODE_CBC, self.iv)
        encrypted = base64.b64encode(cipher.encrypt(body)).decode()
        return encrypted

    def decrypt_msg(self, xml_text: str, msg_signature: str, timestamp: str, nonce: str) -> Optional[str]:
        """解密完整的回调消息 XML"""
        try:
            root = ET.fromstring(xml_text)
            encrypted = root.find("Encrypt").text
            if not self.verify_signature(msg_signature, timestamp, nonce, encrypted):
                logger.error("[WeCom] 签名验证失败")
                return None
            return self.decrypt(encrypted)
        except Exception as e:
            logger.error(f"[WeCom] 解密失败: {e}", exc_info=True)
            return None

    def decrypt_echostr(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> Optional[str]:
        """解密 URL 验证的 echostr"""
        if not self.verify_signature(msg_signature, timestamp, nonce, echostr):
            return None
        return self.decrypt(echostr)


class WeComChannel(BaseChannel):

    def __init__(self):
        super().__init__(ChannelType.WEB)  # reuse WEB type for now
        self.channel_type_name = "wecom"
        self.corp_id = settings.wecom_corp_id or ""
        self.agent_id = settings.wecom_agent_id or 0
        self.secret = settings.wecom_secret or ""

        self.crypto = WeComCrypto(
            token=settings.wecom_token or "",
            encoding_aes_key=settings.wecom_encoding_aes_key or "",
            corp_id=self.corp_id,
        )

        self._access_token: Optional[str] = None
        self._token_expires: float = 0

    @property
    def name(self) -> str:
        return "wecom"

    async def on_startup(self) -> None:
        logger.info(f"WeComChannel 已启动 (corp_id={self.corp_id[:8]}...)")

    async def on_shutdown(self) -> None:
        logger.info("WeComChannel 已关闭")

    # ── Access Token ──────────────────────────

    async def _get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expires:
            return self._access_token
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{WECOM_API}/gettoken",
                params={"corpid": self.corp_id, "corpsecret": self.secret},
            )
            data = resp.json()
            if data.get("errcode") != 0:
                raise RuntimeError(f"获取 access_token 失败: {data}")
            self._access_token = data["access_token"]
            self._token_expires = time.time() + data.get("expires_in", 7200) - 300
            return self._access_token

    # ── 解析消息 ──────────────────────────────

    def parse_message(self, xml_text: str, msg_signature: str, timestamp: str, nonce: str) -> Optional[IncomingMessage]:
        """解密并解析企业微信推送的 XML 消息"""
        decrypted = self.crypto.decrypt_msg(xml_text, msg_signature, timestamp, nonce)
        if not decrypted:
            return None

        try:
            root = ET.fromstring(decrypted)
        except ET.ParseError as e:
            logger.error(f"[WeCom] XML 解析失败: {e}")
            return None

        msg_type = root.findtext("MsgType", "")
        if msg_type != "text":
            logger.info(f"[WeCom] 暂不支持消息类型: {msg_type}")
            return None

        content = root.findtext("Content", "").strip()
        if not content:
            return None

        user_id = root.findtext("FromUserName", "unknown")
        msg_id = root.findtext("MsgId", "")
        agent_id = root.findtext("AgentID", "")

        return IncomingMessage(
            message_id=msg_id,
            channel_type=ChannelType.WEB,
            user_id=user_id,
            conversation_id=f"wecom_{user_id}",
            content=content,
            message_type=MessageType.TEXT,
            metadata={
                "channel": "wecom",
                "user_id": user_id,
                "agent_id": agent_id,
            },
        )

    # ── 发送消息 ──────────────────────────────

    async def send_text(self, user_id: str, text: str) -> bool:
        """发送文本消息"""
        try:
            token = await self._get_access_token()
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{WECOM_API}/message/send?access_token={token}",
                    json={
                        "touser": user_id,
                        "msgtype": "text",
                        "agentid": self.agent_id,
                        "text": {"content": text},
                    },
                )
                data = resp.json()
                if data.get("errcode") != 0:
                    logger.error(f"[WeCom] 发送文本失败: {data}")
                    return False
                logger.info(f"[WeCom] 发送文本成功: touser={user_id} resp={data}")
                return True
        except Exception as e:
            logger.error(f"[WeCom] 发送文本异常: {e}", exc_info=True)
            return False

    async def send_markdown(self, user_id: str, text: str) -> bool:
        """发送 Markdown 消息"""
        try:
            token = await self._get_access_token()
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{WECOM_API}/message/send?access_token={token}",
                    json={
                        "touser": user_id,
                        "msgtype": "markdown",
                        "agentid": self.agent_id,
                        "markdown": {"content": text},
                    },
                )
                data = resp.json()
                if data.get("errcode") != 0:
                    logger.error(f"[WeCom] 发送 Markdown 失败: {data}")
                    return False
                return True
        except Exception as e:
            logger.error(f"[WeCom] 发送 Markdown 异常: {e}", exc_info=True)
            return False

    async def send_image(self, user_id: str, image_b64: str) -> bool:
        """上传并发送图片"""
        try:
            import tempfile, os
            img_bytes = base64.b64decode(image_b64)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(img_bytes)
                tmp = f.name

            token = await self._get_access_token()
            async with httpx.AsyncClient(timeout=30) as client:
                with open(tmp, "rb") as f:
                    resp = await client.post(
                        f"{WECOM_API}/media/upload?access_token={token}&type=image",
                        files={"media": ("chart.png", f, "image/png")},
                    )
                os.unlink(tmp)
                data = resp.json()
                if data.get("errcode") != 0:
                    logger.error(f"[WeCom] 图片上传失败: {data}")
                    return False
                media_id = data["media_id"]

                resp2 = await client.post(
                    f"{WECOM_API}/message/send?access_token={token}",
                    json={
                        "touser": user_id,
                        "msgtype": "image",
                        "agentid": self.agent_id,
                        "image": {"media_id": media_id},
                    },
                )
                data2 = resp2.json()
                if data2.get("errcode") != 0:
                    logger.error(f"[WeCom] 发送图片失败: {data2}")
                    return False
                return True
        except Exception as e:
            logger.error(f"[WeCom] 发送图片异常: {e}", exc_info=True)
            return False

    # ── 处理流程 ──────────────────────────────

    async def process_and_reply(self, incoming: IncomingMessage) -> None:
        """处理消息并回复"""
        from app.channels.dispatcher import dispatch

        user_id = incoming.metadata.get("user_id", "")
        await self.send_text(user_id, "🤔 正在思考...")

        full_response = ""
        image_b64 = None
        try:
            async for event in dispatch(incoming, stream=False):
                if event.event_type == "message":
                    full_response += str(event.data)
                elif event.event_type == "skill_result":
                    data = event.data
                    if isinstance(data, dict):
                        ui_card = data.get("ui_card") or {}
                        img = ui_card.get("image", "")
                        if img.startswith("data:image/png;base64,"):
                            image_b64 = img.split(",", 1)[1]
        except Exception as e:
            logger.error(f"[WeCom] 处理异常: {e}", exc_info=True)

        if not full_response:
            full_response = "抱歉，处理出错了，请稍后再试。"

        await self.send_text(user_id, full_response)
        if image_b64:
            await self.send_image(user_id, image_b64)

        logger.info(f"[WeCom 回复] user={user_id} len={len(full_response)}")

    # ── BaseChannel 接口 ──────────────────────

    async def send(self, message: OutgoingMessage, **kwargs) -> bool:
        user_id = message.metadata.get("user_id", "")
        return await self.send_text(user_id, message.content)

    async def send_event(self, event: ChannelEvent, **kwargs) -> None:
        pass
