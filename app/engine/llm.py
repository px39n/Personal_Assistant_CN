"""多 LLM 供应商适配层，统一接口。当前通过 OpenAI 兼容 API 覆盖 DeepSeek/Qwen/Moonshot。"""

import json
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional

from loguru import logger
from openai import AsyncOpenAI

from app.config import settings


@dataclass
class LLMMessage:
    """统一消息格式"""
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list] = None

    def to_dict(self) -> dict:
        d = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        return d


@dataclass
class LLMResponse:
    """LLM 响应"""
    content: str = ""
    tool_calls: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    finish_reason: Optional[str] = None


@dataclass
class ToolDefinition:
    """工具定义，用于 function calling"""
    name: str
    description: str
    parameters: dict  # JSON Schema

    def to_openai_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class LLMClient:
    """统一 LLM 客户端，支持 OpenAI 兼容 API"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.model = model or settings.llm_model
        self._client = AsyncOpenAI(
            api_key=api_key or settings.llm_api_key,
            base_url=base_url or settings.llm_base_url,
        )

    async def chat(
        self,
        messages: list[LLMMessage],
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> LLMResponse:
        """非流式对话"""
        kwargs = self._build_kwargs(messages, tools, temperature, max_tokens, response_format)

        try:
            response = await self._client.chat.completions.create(**kwargs)
            choice = response.choices[0]

            tool_calls = []
            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    tool_calls.append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": json.loads(tc.function.arguments),
                    })

            return LLMResponse(
                content=choice.message.content or "",
                tool_calls=tool_calls,
                usage={
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                    "total_tokens": response.usage.total_tokens if response.usage else 0,
                },
                finish_reason=choice.finish_reason,
            )
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            raise

    async def chat_stream(
        self,
        messages: list[LLMMessage],
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        """流式对话，逐 token 返回"""
        kwargs = self._build_kwargs(messages, tools, temperature, max_tokens)
        kwargs["stream"] = True

        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.error(f"LLM 流式调用失败: {e}")
            raise

    def _build_kwargs(
        self,
        messages: list[LLMMessage],
        tools: Optional[list[ToolDefinition]],
        temperature: float,
        max_tokens: Optional[int],
        response_format: Optional[dict] = None,
    ) -> dict:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if tools:
            kwargs["tools"] = [t.to_openai_tool() for t in tools]
        if response_format:
            kwargs["response_format"] = response_format
        return kwargs


# 默认客户端实例
def get_llm_client(fast: bool = False) -> LLMClient:
    """获取 LLM 客户端。fast=True 时使用轻量模型（用于工具选择等）。"""
    model = settings.llm_fast_model if fast else settings.llm_model
    return LLMClient(model=model)
