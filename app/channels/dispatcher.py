"""Channel Dispatcher — 统一调度入站消息到对话引擎，输出统一事件流。

所有渠道的消息最终都通过 dispatcher 进入引擎，
dispatcher 负责将引擎事件流转为 ChannelEvent 序列。

单用户模式: 所有渠道的 user_id 统一映射为 DEFAULT_USER_ID，
飞书等渠道的路由信息（open_id / chat_id）单独保存用于推送。
"""

import uuid
from typing import AsyncGenerator

from loguru import logger

from app.channels.base import ChannelEvent, ChannelType, IncomingMessage
from app.config import DEFAULT_USER_ID
from app.engine.chat import handle_message


async def _save_channel_routing(incoming: IncomingMessage) -> None:
    """保存渠道路由信息，供定时推送使用；群聊消息同时记录到已知群列表。"""
    if incoming.channel_type != ChannelType.FEISHU:
        return
    if not incoming.user_id.startswith("ou_"):
        return

    from app.engine.memory import memory_store

    await memory_store.set_global(DEFAULT_USER_ID, "feishu_routing", {
        "open_id": incoming.user_id,
        "chat_id": incoming.metadata.get("chat_id", ""),
    })

    chat_type = incoming.metadata.get("chat_type", "")
    chat_id = incoming.metadata.get("chat_id", "")
    if chat_type == "group" and chat_id:
        await _register_feishu_group(chat_id)


async def _fetch_chat_name(chat_id: str) -> str | None:
    """通过飞书 API 获取群聊名称，失败返回 None。"""
    try:
        from app.channels.base import channel_registry
        ch = channel_registry.get("feishu")
        if not (ch and ch._lark_client):
            return None
        from lark_oapi.api.im.v1 import GetChatRequest
        import asyncio
        req = GetChatRequest.builder().chat_id(chat_id).build()
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(None, ch._lark_client.im.v1.chat.get, req)
        if resp.success() and resp.data:
            return resp.data.name or None
        logger.debug(f"[Dispatcher] chat.get 失败: code={resp.code} msg={resp.msg}")
    except Exception as e:
        logger.warning(f"[Dispatcher] 获取群名失败: {e}")
    return None


async def _register_feishu_group(chat_id: str) -> None:
    """将群聊 chat_id 记录到已知群列表，并尝试通过 API 获取群名。
    如果已存在但群名仍是 fallback（oc_ 开头），会重试获取。
    """
    from app.engine.memory import memory_store

    groups: list[dict] = await memory_store.get_global(DEFAULT_USER_ID, "feishu_groups", []) or []
    existing = next((g for g in groups if g["chat_id"] == chat_id), None)

    if existing and not existing["name"].startswith("oc_"):
        return

    api_name = await _fetch_chat_name(chat_id)
    name = api_name or chat_id[:12]

    if existing:
        if api_name:
            existing["name"] = api_name
            await memory_store.set_global(DEFAULT_USER_ID, "feishu_groups", groups)
            logger.info(f"[Dispatcher] 更新群名: {api_name} ({chat_id})")
        return

    groups.append({"chat_id": chat_id, "name": name})
    await memory_store.set_global(DEFAULT_USER_ID, "feishu_groups", groups)
    logger.info(f"[Dispatcher] 新增已知群: {name} ({chat_id})")


async def _is_companion_group(chat_id: str) -> bool:
    """检查 chat_id 是否为伴侣专属调教群。"""
    if not chat_id:
        return False
    try:
        from app.skills.chat.companion import _get_companion_group
        group = await _get_companion_group()
        return group is not None and group == chat_id
    except Exception:
        return False


_WORK_KEYWORDS = [
    "股票", "K线", "持仓", "行情", "基金", "涨跌", "大盘",
    "天气", "翻译", "汇率", "搜索", "查询", "停牌", "复牌",
    "帮我", "执行", "代码", "文档", "知识库",
]


def _looks_like_work(text: str) -> bool:
    return any(kw in text for kw in _WORK_KEYWORDS)


async def _companion_only_dispatch(
    incoming: IncomingMessage,
) -> AsyncGenerator[ChannelEvent, None]:
    """伴侣专属群的消息处理：仅调用 companion skill，不经过 Router。"""
    from app.skills.chat.companion import CompanionSkill
    from app.skills.base import SkillContext
    from app.engine import memory as _mem

    conversation_id = incoming.conversation_id or str(uuid.uuid4())
    yield {"type": "metadata", "data": {"conversation_id": conversation_id}}

    await _mem.memory_store.add_message(
        conversation_id, "user", incoming.content, user_id=DEFAULT_USER_ID,
    )

    if _looks_like_work(incoming.content):
        reply = "下班不要聊正事哦～ 有工作的事情去其他群找我吧 😝"
    else:
        skill_instance = CompanionSkill()
        ctx = SkillContext(user_id=DEFAULT_USER_ID, conversation_id=conversation_id)
        result = await skill_instance.execute(ctx, message=incoming.content)
        reply = result.summary if result.success else (result.error or "出错了～")

    await _mem.memory_store.add_message(
        conversation_id, "assistant", reply, user_id=DEFAULT_USER_ID,
    )

    yield ChannelEvent(event_type="message", data=reply, conversation_id=conversation_id)
    yield ChannelEvent(event_type="done", data={"conversation_id": conversation_id}, conversation_id=conversation_id)


async def dispatch(
    incoming: IncomingMessage,
    stream: bool = True,
) -> AsyncGenerator[ChannelEvent, None]:
    """
    将入站消息调度到对话引擎，生成统一的 ChannelEvent 流。

    所有渠道都通过这个入口:
    - WebChannel: SSE / 非流式
    - FeishuChannel: 回调推送
    - DingTalkChannel: 回调推送
    """
    logger.info(
        f"[Dispatcher] channel={incoming.channel_type.value} "
        f"user={incoming.user_id} → {DEFAULT_USER_ID} msg={incoming.content[:50]}"
    )

    await _save_channel_routing(incoming)

    chat_id = incoming.metadata.get("chat_id", "")
    if await _is_companion_group(chat_id):
        logger.info(f"[Dispatcher] 伴侣专属群消息，跳过 Router")
        async for event in _companion_only_dispatch(incoming):
            yield event if isinstance(event, ChannelEvent) else ChannelEvent(
                event_type=event.get("type", "message"),
                data=event.get("data", ""),
                conversation_id=incoming.conversation_id or "",
            )
        return

    conversation_id = incoming.conversation_id

    async for event in handle_message(
        user_message=incoming.content,
        conversation_id=conversation_id,
        user_id=DEFAULT_USER_ID,
        stream=stream,
    ):
        event_type = event.get("type", "message")
        data = event.get("data", "")

        if event_type in ("metadata", "done") and isinstance(data, dict):
            conversation_id = data.get("conversation_id", conversation_id)

        yield ChannelEvent(
            event_type=event_type,
            data=data,
            conversation_id=conversation_id,
        )
