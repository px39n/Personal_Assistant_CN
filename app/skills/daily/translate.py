"""翻译 Skill — 调用 LLM 进行高质量多语言翻译，带记忆偏好。"""

from typing import Any, Optional

from loguru import logger

from app.engine.llm import get_llm_client, LLMMessage
from app.engine.memory import memory_store
from app.skills.base import Skill, SkillCategory, SkillContext, SkillResult, skill


@skill(
    name="translate",
    description="翻译文本到指定语言。支持中英日韩法德等主流语言互译。如果用户没有指定目标语言，会根据输入语言自动选择（中文→英文，其他→中文）。",
    category=SkillCategory.ACTION,
    icon="🌐",
    config_schema={
        "type": "object",
        "properties": {
            "default_target_language": {
                "type": "string",
                "title": "默认目标语言",
                "description": "未指定目标语言时的默认选择（留空则自动检测）",
                "default": "",
                "enum": ["", "中文", "English", "日本語", "한국어", "Français", "Deutsch"],
            },
        },
    },
    parameters_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "需要翻译的文本",
            },
            "target_language": {
                "type": "string",
                "description": "目标语言，例如：中文、English、日本語、한국어、Français、Deutsch。留空则自动选择。",
            },
        },
        "required": ["text"],
    },
)
class TranslateSkill(Skill):
    """使用 LLM 进行翻译"""

    async def execute(self, context: SkillContext, **kwargs: Any) -> SkillResult:
        text = kwargs.get("text", "")
        target_language = kwargs.get("target_language", "")
        user_id = context.user_id or "web_user"

        if not text.strip():
            return SkillResult.fail("请提供需要翻译的文本")

        # 从 Skill 记忆中读取用户的默认目标语言偏好
        if not target_language:
            target_language = context.skill_memory.get("preferred_target_language", "")

        # 自动判断：如果全是中文字符→翻译成英文，否则翻译成中文
        if not target_language:
            if self._is_mostly_chinese(text):
                target_language = "English"
            else:
                target_language = "中文"

        try:
            llm = get_llm_client(fast=True)
            messages = [
                LLMMessage(
                    role="system",
                    content=(
                        f"你是一位专业翻译。请将以下文本翻译成{target_language}。\n"
                        "要求：\n"
                        "1. 翻译要自然流畅，符合目标语言的表达习惯\n"
                        "2. 保留原文的语气和风格\n"
                        "3. 仅输出翻译结果，不要添加任何解释"
                    ),
                ),
                LLMMessage(role="user", content=text),
            ]

            response = await llm.chat(messages=messages, temperature=0.3)
            translated = response.content.strip()

            # 记录最近的翻译方向到 Skill 记忆
            if user_id:
                await memory_store.set_skill(
                    user_id, self.name, "last_target_language", target_language
                )

            summary = f"**{target_language} 翻译结果：**\n\n{translated}"

            return SkillResult(
                success=True,
                data={
                    "original": text,
                    "translated": translated,
                    "target_language": target_language,
                },
                summary=summary,
            )

        except Exception as e:
            logger.error(f"翻译失败: {e}", exc_info=True)
            return SkillResult.fail(f"翻译失败: {str(e)}")

    @staticmethod
    def _is_mostly_chinese(text: str) -> bool:
        chinese_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        return chinese_count > len(text) * 0.3
