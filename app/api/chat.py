"""聊天 API 路由 — SSE 流式响应 + WebSocket，通过 Channel 调度。"""

import json
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from app.channels.dispatcher import dispatch
from app.channels.web import WebChannel

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    """聊天请求体"""
    message: str
    conversation_id: Optional[str] = None
    user_id: str = "anonymous"
    stream: bool = True


@router.post("/chat")
async def chat(request: ChatRequest):
    """
    POST /api/chat — 发送消息并获取 SSE 流式响应。

    事件类型:
    - status: 处理状态更新
    - message: LLM 回复 token
    - skill_result: Skill 执行结果
    - error: 错误信息
    - done: 完成标记
    """
    incoming = WebChannel.parse_incoming(
        message=request.message,
        user_id=request.user_id,
        conversation_id=request.conversation_id,
    )

    if request.stream:
        return StreamingResponse(
            _sse_generator(incoming),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        # 非流式：收集所有事件，返回完整响应
        full_response = ""
        skill_results = []
        conversation_id = None

        async for event in dispatch(incoming, stream=False):
            if event.event_type == "message":
                full_response += event.data
            elif event.event_type == "skill_result":
                skill_results.append(event.data)
            elif event.event_type == "metadata":
                conversation_id = event.data.get("conversation_id") if isinstance(event.data, dict) else None
            elif event.event_type == "done":
                conversation_id = event.conversation_id

        return {
            "message": full_response,
            "conversation_id": conversation_id,
            "skill_results": skill_results,
        }


async def _sse_generator(incoming):
    """将 Channel 事件流转为 SSE 格式"""
    try:
        async for event in dispatch(incoming, stream=True):
            data = event.data

            if isinstance(data, dict):
                data_str = json.dumps(data, ensure_ascii=False)
            else:
                data_str = str(data)

            yield f"event: {event.event_type}\ndata: {data_str}\n\n"
    except Exception as e:
        logger.error(f"SSE 流错误: {e}", exc_info=True)
        yield f"event: error\ndata: 服务器内部错误\n\n"


@router.websocket("/ws/chat")
async def websocket_chat(
    websocket: WebSocket,
    user_id: str = Query(default="anonymous"),
):
    """
    WebSocket /ws/chat — 双向实时对话。

    客户端发送: {"message": "...", "conversation_id": "..."}
    服务端推送: {"type": "...", "data": "..."}
    """
    await websocket.accept()
    logger.info(f"WebSocket 连接: user={user_id}")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "data": "无效的 JSON 格式"})
                continue

            message = payload.get("message", "")
            conversation_id = payload.get("conversation_id")

            if not message.strip():
                await websocket.send_json({"type": "error", "data": "消息不能为空"})
                continue

            incoming = WebChannel.parse_incoming(
                message=message,
                user_id=user_id,
                conversation_id=conversation_id,
            )

            async for event in dispatch(incoming, stream=True):
                await websocket.send_json({
                    "type": event.event_type,
                    "data": event.data,
                })

    except WebSocketDisconnect:
        logger.info(f"WebSocket 断开: user={user_id}")
    except Exception as e:
        logger.error(f"WebSocket 错误: {e}", exc_info=True)
        try:
            await websocket.close()
        except Exception:
            pass


@router.get("/conversations/{conversation_id}/history")
async def get_conversation_history(conversation_id: str):
    """GET /api/conversations/{id}/history — 获取对话历史"""
    from app.engine.memory import memory_store

    history = await memory_store.get_chat_history(conversation_id)
    return {"conversation_id": conversation_id, "messages": history}


@router.get("/channels")
async def list_channels():
    """GET /api/channels — 列出所有已注册的渠道"""
    from app.channels.base import channel_registry

    return {
        "count": channel_registry.count,
        "channels": [
            {
                "name": ch.name,
                "type": ch.channel_type.value,
                "enabled": ch.enabled,
            }
            for ch in channel_registry.list_all()
        ],
    }
