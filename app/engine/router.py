"""动态 Skill 路由 — 使用 LLM 原生 function calling 从已注册 Skill 中选择工具。"""

import json
from typing import Optional

from loguru import logger

from app.engine.llm import LLMClient, LLMMessage, get_llm_client
from app.skills.registry import skill_registry


ROUTER_SYSTEM_PROMPT = """你是一个工具路由器。你的唯一职责是决定调用哪些工具。你不能自己回答问题。

规则:
- 只要用户的请求涉及任何工具能做的事，就必须调用工具，绝不能自己假装完成
- 添加/移除/增加/关注股票、持仓快报 → 必须调用 portfolio（只需股票名，不要问数量价格）
- 查看/分析股票、股票K线/走势 → 必须调用 stock_chart
- 停复牌查询 → 必须调用 stock_suspend
- 天气 → weather，翻译 → translate，汇率 → currency_exchange
- 日常闲聊、问候、情感交流、聊天、吐槽、分享心情 → 必须调用 companion
- 如果用户一次请求多个操作，为每个操作调用对应的工具
{dynamic_context}"""


async def route_to_skills(
    user_message: str,
    chat_history: list[dict] = None,
    client: Optional[LLMClient] = None,
) -> list[dict]:
    """
    使用 LLM 原生 function calling 选择工具。

    返回:
        list[dict]: [{"name": "skill_name", "arguments": {...}}, ...]
        空列表表示不需要工具。
    """
    if skill_registry.count == 0:
        return []

    tool_defs = skill_registry.get_tool_definitions()
    if not tool_defs:
        return []

    llm = client or get_llm_client(fast=True)

    dynamic_context = _build_dynamic_context()
    system_prompt = ROUTER_SYSTEM_PROMPT.format(dynamic_context=dynamic_context)

    messages = [LLMMessage(role="system", content=system_prompt)]
    messages.append(LLMMessage(role="user", content=user_message))

    try:
        response = await llm.chat(
            messages=messages,
            tools=tool_defs,
            temperature=0.0,
        )

        if response.tool_calls:
            valid_calls = []
            for tc in response.tool_calls:
                name = tc["name"]
                args = tc.get("arguments", {})
                if skill_registry.get(name):
                    valid_calls.append({"name": name, "arguments": args})
                    logger.info(f"路由决策 (function calling): {name}({args})")
                else:
                    logger.warning(f"LLM 选择了不存在的 Skill: {name}")
            return valid_calls
        else:
            logger.debug("路由决策: 直接对话（无 tool_calls）")
            return []

    except Exception as e:
        logger.error(f"路由调用失败: {e}", exc_info=True)
        return []


def _build_dynamic_context() -> str:
    """构建动态上下文（知识库状态等）"""
    parts = []
    try:
        from app.engine.vectorstore import vector_store
        if vector_store.document_count > 0:
            docs = vector_store.list_documents()
            doc_titles = [d["title"] for d in docs]
            parts.append(
                f"\n当前知识库: {vector_store.document_count} 份文档"
                f"（{', '.join(doc_titles)}），"
                f"用户询问相关内容时应使用 knowledge_search。"
            )
    except Exception:
        pass
    return "".join(parts)
