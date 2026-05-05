"""企业微信 Webhook 回调 — URL 验证 + 消息接收。"""

import asyncio

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse
from loguru import logger

router = APIRouter(prefix="/api/wecom", tags=["wecom"])

_wecom_channel = None


def get_wecom_channel():
    global _wecom_channel
    if _wecom_channel is None:
        from app.channels.base import channel_registry
        _wecom_channel = channel_registry.get("wecom")
    return _wecom_channel


@router.get("/webhook")
async def wecom_verify(
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """企业微信 URL 验证 — GET 请求，返回解密后的 echostr"""
    ch = get_wecom_channel()
    if not ch:
        return PlainTextResponse("channel not configured", status_code=500)

    decrypted = ch.crypto.decrypt_echostr(msg_signature, timestamp, nonce, echostr)
    if decrypted is None:
        logger.error("[WeCom Webhook] URL 验证签名校验失败")
        return PlainTextResponse("signature error", status_code=403)

    logger.info("[WeCom Webhook] URL 验证成功")
    return PlainTextResponse(decrypted)


@router.post("/webhook")
async def wecom_callback(
    request: Request,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
):
    """企业微信消息回调 — POST 请求，接收加密消息"""
    ch = get_wecom_channel()
    if not ch:
        return PlainTextResponse("ok")

    body = await request.body()
    xml_text = body.decode("utf-8")

    incoming = ch.parse_message(xml_text, msg_signature, timestamp, nonce)
    if not incoming:
        return PlainTextResponse("ok")

    logger.info(f"[WeCom Webhook] user={incoming.user_id} msg={incoming.content[:50]}")

    asyncio.create_task(ch.process_and_reply(incoming))

    return PlainTextResponse("ok")
