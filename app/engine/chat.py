"""对话引擎 — 处理用户消息，协调 Skill 路由、执行、LLM 响应生成。"""

import asyncio
import uuid
from typing import AsyncGenerator, Optional

from loguru import logger

from app.engine.llm import LLMMessage, get_llm_client
from app.engine import memory as _mem
from app.engine.router import route_to_skills
from app.skills.base import SkillContext, SkillResult
from app.skills.registry import skill_registry


async def _execute_skill_with_timeout(
    skill_instance, context, skill_args: dict, timeout: int,
) -> SkillResult:
    """执行 Skill 并强制超时。用 asyncio.shield + sleep 竞争实现可靠超时。"""
    task = asyncio.create_task(skill_instance.execute(context, **skill_args))
    timer = asyncio.create_task(asyncio.sleep(timeout))

    done, pending = await asyncio.wait(
        {task, timer}, return_when=asyncio.FIRST_COMPLETED,
    )

    if task in done:
        timer.cancel()
        return task.result()
    else:
        logger.warning(f"Skill {skill_instance.name} 执行超时({timeout}s)")
        task.cancel()
        return SkillResult.fail(f"执行超时（>{timeout}秒），请稍后再试")


CHAT_SYSTEM_PROMPT = """你是一个智能个人助手，面向中国用户。你的能力:
1. 回答用户的问题（闲聊、常识、建议）
2. 借助工具完成任务（搜索、下单、查询等）

规则:
- 用中文回复
- 简洁、有条理
- 如果工具执行失败，诚实告知用户并给出替代建议
- 不要编造工具没有返回的信息

{tool_context}"""


async def handle_message(
    user_message: str,
    conversation_id: Optional[str] = None,
    user_id: str = "anonymous",
    stream: bool = True,
) -> AsyncGenerator[dict, None]:
    """
    处理用户消息的主入口。

    流程:
    1. 记录用户消息到会话历史
    2. LLM 路由决定是否需要 Skill
    3a. 需要 Skill → 执行 → 将结果喂给 LLM 生成回复
    3b. 不需要 → 直接 LLM 对话

    Yields:
        dict: 事件流，格式 {"type": "...", "data": "..."}
        type 可选: "status" | "message" | "skill_result" | "error" | "done"
    """
    if not conversation_id:
        conversation_id = str(uuid.uuid4())

    # 1. 记录用户消息
    await _mem.memory_store.add_message(conversation_id, "user", user_message, user_id=user_id)
    chat_history = await _mem.memory_store.get_chat_history(conversation_id, limit=20)

    yield {"type": "metadata", "data": {"conversation_id": conversation_id}}

    # 2. 路由决策
    yield {"type": "status", "data": "正在分析您的需求..."}

    tool_calls = await route_to_skills(
        user_message=user_message,
        chat_history=chat_history,
    )

    # 3. 执行 Skill（如果需要）
    tool_context_parts = []
    skill_results: list[SkillResult] = []

    if tool_calls:
        tool_names = [tc["name"] for tc in tool_calls]
        yield {"type": "status", "data": f"正在使用工具: {', '.join(tool_names)}"}

        for tool_call in tool_calls:
            skill_name = tool_call["name"]
            skill_args = tool_call.get("arguments", {})
            skill_instance = skill_registry.get(skill_name)

            if not skill_instance:
                logger.warning(f"Skill {skill_name} 不存在")
                continue

            yield {"type": "status", "data": f"执行 {skill_name}..."}

            try:
                mem_context = await _mem.memory_store.build_skill_context(user_id, skill_name, conversation_id)
                context = SkillContext(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    chat_history=chat_history,
                    skill_memory=mem_context["skill_memory"],
                    global_memory=mem_context["global_memory"],
                )

                SKILL_TIMEOUT = 30
                result = await _execute_skill_with_timeout(
                    skill_instance, context, skill_args, SKILL_TIMEOUT,
                )

                skill_results.append(result)

                if result.success:
                    tool_context_parts.append(
                        f"[工具 {skill_name} 结果]:\n{result.summary}"
                    )
                    yield {"type": "skill_result", "data": {
                        "skill": skill_name,
                        "success": True,
                        "summary": result.summary,
                        "ui_card": result.ui_card,
                    }}
                else:
                    tool_context_parts.append(
                        f"[工具 {skill_name} 失败]: {result.error}"
                    )
                    yield {"type": "skill_result", "data": {
                        "skill": skill_name,
                        "success": False,
                        "error": result.error,
                        "ui_card": result.ui_card,
                    }}

            except Exception as e:
                logger.error(f"Skill {skill_name} 执行异常: {e}", exc_info=True)
                tool_context_parts.append(f"[工具 {skill_name} 异常]: {str(e)}")
                yield {"type": "error", "data": f"工具 {skill_name} 执行出错"}

    # 4. 构建 LLM 消息并生成回复
    tool_context_str = ""
    if tool_context_parts:
        tool_context_str = (
            "以下是工具执行的结果，请基于这些信息回答用户:\n\n"
            + "\n\n".join(tool_context_parts)
        )

    system_prompt = CHAT_SYSTEM_PROMPT.format(tool_context=tool_context_str)

    user_ids_in_history = {
        msg.get("user_id") for msg in chat_history
        if msg["role"] == "user" and msg.get("user_id")
    }
    is_group_chat = len(user_ids_in_history) > 1

    if is_group_chat:
        system_prompt += (
            "\n\n注意：当前是群聊环境，消息前的 [用户 xxxx] 标识了不同的发言者。"
            "请根据上下文判断并回复最新发言的用户。"
        )

    messages = [LLMMessage(role="system", content=system_prompt)]
    for msg in chat_history:
        content = msg["content"]
        if is_group_chat and msg["role"] == "user" and msg.get("user_id"):
            short_id = msg["user_id"][-8:]
            content = f"[用户 {short_id}]: {content}"
        messages.append(LLMMessage(role=msg["role"], content=content))

    yield {"type": "status", "data": "正在生成回复..."}

    # 流式生成
    llm = get_llm_client(fast=False)
    full_response = ""

    try:
        if stream:
            async for token in llm.chat_stream(messages=messages):
                full_response += token
                yield {"type": "message", "data": token}
        else:
            response = await llm.chat(messages=messages)
            full_response = response.content
            yield {"type": "message", "data": full_response}
    except Exception as e:
        logger.error(f"LLM 生成回复失败: {e}")
        full_response = "抱歉，生成回复时出错了，请稍后重试。"
        yield {"type": "error", "data": full_response}

    # 5. 记录助手回复
    await _mem.memory_store.add_message(conversation_id, "assistant", full_response, user_id=user_id)

    yield {"type": "done", "data": {"conversation_id": conversation_id}}
