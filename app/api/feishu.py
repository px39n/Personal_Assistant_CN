"""飞书 Webhook API 路由 — 接收飞书事件推送。"""

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger

from app.channels.base import ChannelEvent, OutgoingMessage, channel_registry
from app.channels.dispatcher import dispatch

router = APIRouter(prefix="/api/feishu", tags=["feishu"])


@router.post("/card_action")
async def feishu_card_action(request: Request):
    """飞书卡片按钮回调 — 处理 URL 验证 + 按钮点击"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    # URL 验证（配置回调地址时飞书会发这个）
    if body.get("type") == "url_verification":
        logger.info("[飞书卡片] URL 验证成功")
        return JSONResponse({"challenge": body.get("challenge", "")})

    logger.info(f"[飞书卡片] 收到回调: {json.dumps(body, ensure_ascii=False)[:300]}")

    # 兼容 v1 和 v2.0 两种回调格式
    event = body.get("event", {})
    action = event.get("action") or body.get("action") or {}
    value = action.get("value", {})

    if not value:
        logger.warning(f"[飞书卡片] value 为空，跳过")
        return JSONResponse({})

    action_type = value.get("action")
    logger.info(f"[飞书卡片] action_type={action_type}, value={value}")

    if action_type == "set_push_frequency":
        frequency = value.get("frequency", "open_only")
        from app.config import DEFAULT_USER_ID
        from app.engine.memory import memory_store
        from app.skills.finance.portfolio import PUSH_PRESETS, PUSH_CONFIG_KEY

        if frequency in PUSH_PRESETS:
            push_config = await memory_store.get_skill(DEFAULT_USER_ID, "portfolio", PUSH_CONFIG_KEY) or {}
            push_config["frequency"] = frequency
            await memory_store.set_skill(DEFAULT_USER_ID, "portfolio", PUSH_CONFIG_KEY, push_config)
            label = PUSH_PRESETS[frequency]["label"]
            logger.info(f"[飞书卡片] 修改推送频率为 {label}")

            from app.channels.feishu import FeishuChannel
            updated_card = json.loads(
                FeishuChannel.build_report_card(f"✅ 推送频率已修改为: **{label}**", frequency)
            )

            return JSONResponse({
                "toast": {"type": "success", "content": f"已修改为: {label}"},
                "card": {"type": "raw", "data": updated_card},
            })

    return JSONResponse({})


@router.get("/status")
async def feishu_status():
    """GET /api/feishu/status — 检查飞书连接状态"""
    from app.channels.feishu import FeishuChannel
    feishu_ch = channel_registry.get("feishu")
    if not feishu_ch or not isinstance(feishu_ch, FeishuChannel):
        return {"status": "not_configured"}

    ws_alive = feishu_ch._ws_thread is not None and feishu_ch._ws_thread.is_alive()
    ws_connected = (
        feishu_ch._ws_client is not None
        and feishu_ch._ws_client._conn is not None
    )
    return {
        "status": "ok" if ws_connected else "disconnected",
        "ws_thread_alive": ws_alive,
        "ws_connected": ws_connected,
        "messages_received": feishu_ch._msg_received_count,
        "messages_sent": feishu_ch._msg_sent_count,
        "last_received": feishu_ch._last_received_text,
        "last_error": feishu_ch._last_error,
    }


@router.post("/webhook")
async def feishu_webhook(request: Request):
    """
    POST /api/feishu/webhook — 飞书事件回调入口。

    处理:
    1. URL 验证 (url_verification) — 飞书配置 webhook 时的验证
    2. 消息接收 (im.message.receive_v1) — 用户发送消息给 Bot
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    # 获取飞书 Channel
    from app.channels.feishu import FeishuChannel
    feishu_ch = channel_registry.get("feishu")
    if not feishu_ch or not isinstance(feishu_ch, FeishuChannel):
        logger.error("FeishuChannel 未注册")
        return JSONResponse({"error": "Feishu channel not configured"}, status_code=503)

    # 1. URL 验证
    challenge_resp = feishu_ch.verify_url_challenge(body)
    if challenge_resp:
        logger.info("飞书 URL 验证成功")
        return JSONResponse(challenge_resp)

    # 2. 事件去重
    header = body.get("header", {})
    event_id = header.get("event_id", "")
    if event_id and feishu_ch.is_duplicate_event(event_id):
        logger.debug(f"忽略重复事件: {event_id}")
        return JSONResponse({"code": 0, "msg": "ok"})

    # 3. 解析消息
    incoming = FeishuChannel.parse_webhook_event(body)
    if not incoming:
        # 非消息事件，直接返回 ok
        return JSONResponse({"code": 0, "msg": "ok"})

    logger.info(
        f"[飞书消息] user={incoming.user_id} "
        f"chat={incoming.metadata.get('chat_id', '')} "
        f"content={incoming.content[:50]}"
    )

    # 4. 异步处理消息（不阻塞飞书回调）
    import asyncio
    asyncio.create_task(_process_feishu_message(feishu_ch, incoming))

    # 立即返回 ok（飞书要求 3 秒内响应）
    return JSONResponse({"code": 0, "msg": "ok"})


async def _process_feishu_message(feishu_ch, incoming):
    """异步处理飞书消息并回复"""
    try:
        # 收集完整回复（飞书不支持流式，需要收集完再发）
        full_response = ""
        skill_summaries = []

        async for event in dispatch(incoming, stream=False):
            if event.event_type == "message":
                full_response += str(event.data)
            elif event.event_type == "skill_result":
                if isinstance(event.data, dict) and event.data.get("success"):
                    skill_summaries.append(event.data.get("summary", ""))

        if not full_response:
            full_response = "抱歉，处理您的消息时出了问题。"

        # 构建回复消息
        reply = OutgoingMessage(
            conversation_id=incoming.conversation_id or "",
            content=full_response,
            metadata={
                "chat_id": incoming.metadata.get("chat_id"),
                "message_id": incoming.message_id,
            },
        )

        # 发送回复
        success = await feishu_ch.send(
            reply,
            reply_to=incoming.metadata.get("message_id"),
        )

        if success:
            logger.info(f"飞书回复成功: {full_response[:50]}...")
        else:
            logger.error("飞书回复失败")

    except Exception as e:
        logger.error(f"处理飞书消息异常: {e}", exc_info=True)
