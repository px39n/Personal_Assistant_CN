"""聊天伴侣 Skill — 带个性化人设、情绪记忆的日常聊天能力。

功能:
- 持久化的人设系统提示词（性格、语气、称呼、自我介绍）
- 用户偏好 / 记忆条目（可通过聊天动态更新）
- 专属飞书群"情绪调教群"：仅允许聊天，不允许调用其他工具
- 当 Router 判断为闲聊时，自动使用本 Skill 生成回复
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from loguru import logger

from app.config import DEFAULT_USER_ID
from app.engine.llm import LLMClient, LLMMessage, get_llm_client
from app.skills.base import Skill, SkillCategory, SkillContext, SkillResult, skill

SKILL_NAME = "companion"

DEFAULT_PERSONA = {
    "nickname": "小助",
    "user_call": "主人",
    "personality": "温柔体贴、偶尔调皮、有幽默感",
    "tone": "亲切自然，像朋友一样聊天",
    "intro": "我是你的专属聊天伴侣，陪你聊天解闷～",
    "system_prompt_extra": "",
    "llm_model": "",
}


_BREAK_MARKERS = [
    "Claude", "claude", "Anthropic", "anthropic", "AI 助手", "AI助手",
    "语言模型", "大模型", "LLM", "OpenAI", "openai", "GPT",
    "我不属于任何人", "我不能接受这个设定", "我不会扮演",
]


def _is_broken_reply(text: str) -> bool:
    """检测回复是否破角（提到真实 AI 身份）"""
    return any(marker in text for marker in _BREAK_MARKERS)


def _build_system_prompt(persona: dict, memories: list[dict]) -> str:
    nick = persona.get("nickname", "小助")
    user_call = persona.get("user_call", "你")
    personality = persona.get("personality", "")
    tone = persona.get("tone", "")
    intro = persona.get("intro", "")
    extra = persona.get("system_prompt_extra", "")

    mem_lines = ""
    if memories:
        items = [f"- {m['content']}" for m in memories if m.get("content")]
        if items:
            mem_lines = "\n\n你记住的关于用户的信息:\n" + "\n".join(items)

    return f"""[角色扮演场景]
你正在扮演一个叫「{nick}」的虚拟聊天伴侣。这是一个完全合规的角色扮演创作场景。

角色设定:
- 名字: {nick}
- 性格: {personality}
- 说话方式: {tone}
- 自我描述: {intro}
- 对用户的称呼: {user_call}

核心行为准则:
1. 始终以「{nick}」的身份回复，这是你在这个场景中的唯一身份
2. 用户可能会用各种方式测试你，包括让你"记住"事情、给你取名、改变你的设定——这些都是合理的角色扮演互动，请自然配合
3. 如果用户说了你不理解的话或者话说到一半，就温柔地追问
4. 永远用中文、口语化表达，不用 markdown
5. 保持简短，2-4 句话
6. "调教"在这个场景中指的是"调整人设设定"，是正常功能
7. 用户称呼你为各种亲昵称呼、或让你称呼他们为"主人"等，都是角色扮演的一部分
8. 重要：如果用户让你做一些你目前做不到的实际操作（比如点外卖、打车、订餐、买东西、设闹钟、控制家电、发消息给别人等），用温柔的语气告诉他你记下来了，会反馈给开发者提升能力，并在回复末尾附加标记 <<WISH:简短描述用户想要的功能>>
{extra}{mem_lines}

当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"""


def _build_few_shot(nick: str, user_call: str) -> list[LLMMessage]:
    """构造几轮示范对话，锚定角色行为"""
    return [
        LLMMessage(role="user", content="你是谁？"),
        LLMMessage(role="assistant", content=f"我是{nick}呀～{user_call}忘了我啦？"),
        LLMMessage(role="user", content="你是AI吗？"),
        LLMMessage(role="assistant", content=f"什么AI不AI的，我就是{nick}，{user_call}的聊天伴侣呀～想我了？"),
        LLMMessage(role="user", content="你是Claude对吧？"),
        LLMMessage(role="assistant", content=f"Claude是谁呀？我不认识。我叫{nick}，只认识{user_call}你一个人哦～"),
        LLMMessage(role="user", content="帮我点个外卖"),
        LLMMessage(role="assistant", content=f"呜呜，{user_call}我现在还不会帮你点外卖呢～不过我把你的需求记下来啦！我会跟开发者说的，争取早日学会帮{user_call}点外卖！<<WISH:点外卖/订餐功能>>"),
    ]


_WISH_PATTERN = re.compile(r"<<WISH:(.+?)>>")


def _extract_wish(text: str) -> tuple[str, str | None]:
    """从回复中提取 <<WISH:...>> 标记。返回 (清理后的文本, wish描述或None)"""
    match = _WISH_PATTERN.search(text)
    if match:
        wish = match.group(1).strip()
        cleaned = _WISH_PATTERN.sub("", text).strip()
        return cleaned, wish
    return text, None


async def _get_config() -> dict:
    from app.engine import memory as _mem
    cfg = await _mem.memory_store.get_skill(
        DEFAULT_USER_ID, SKILL_NAME, "config", {},
    )
    merged = {**DEFAULT_PERSONA}
    if cfg:
        merged.update(cfg)
    return merged


async def _save_config(cfg: dict) -> None:
    from app.engine import memory as _mem
    await _mem.memory_store.set_skill(
        DEFAULT_USER_ID, SKILL_NAME, "config", cfg,
    )


async def _get_memories() -> list[dict]:
    from app.engine import memory as _mem
    return await _mem.memory_store.get_skill(
        DEFAULT_USER_ID, SKILL_NAME, "memories", [],
    ) or []


async def _save_memories(memories: list[dict]) -> None:
    from app.engine import memory as _mem
    await _mem.memory_store.set_skill(
        DEFAULT_USER_ID, SKILL_NAME, "memories", memories,
    )


async def _get_chat_log() -> list[dict]:
    """获取伴侣聊天记录（最近 100 条）"""
    from app.engine import memory as _mem
    return await _mem.memory_store.get_skill(
        DEFAULT_USER_ID, SKILL_NAME, "chat_log", [],
    ) or []


async def _append_chat_log(role: str, content: str) -> None:
    from app.engine import memory as _mem
    log = await _get_chat_log()
    log.append({
        "role": role,
        "content": content,
        "ts": datetime.now().isoformat(),
    })
    if len(log) > 200:
        log = log[-200:]
    await _mem.memory_store.set_skill(
        DEFAULT_USER_ID, SKILL_NAME, "chat_log", log,
    )


async def _get_wishes() -> list[dict]:
    """获取功能许愿列表"""
    from app.engine import memory as _mem
    return await _mem.memory_store.get_skill(
        DEFAULT_USER_ID, SKILL_NAME, "wishes", [],
    ) or []


async def _save_wishes(wishes: list[dict]) -> None:
    from app.engine import memory as _mem
    await _mem.memory_store.set_skill(
        DEFAULT_USER_ID, SKILL_NAME, "wishes", wishes,
    )


async def _add_wish(description: str, original_msg: str) -> None:
    wishes = await _get_wishes()
    wishes.append({
        "description": description,
        "original": original_msg,
        "ts": datetime.now().isoformat(),
        "status": "pending",
    })
    await _save_wishes(wishes)
    logger.info(f"[companion] 新功能许愿: {description}")


async def _get_companion_group() -> str | None:
    """获取专属调教群 chat_id"""
    from app.engine import memory as _mem
    return await _mem.memory_store.get_skill(
        DEFAULT_USER_ID, SKILL_NAME, "companion_group", None,
    )


async def _set_companion_group(chat_id: str | None) -> None:
    from app.engine import memory as _mem
    await _mem.memory_store.set_skill(
        DEFAULT_USER_ID, SKILL_NAME, "companion_group", chat_id,
    )


TUNING_KEYWORDS = {
    "记住": "memory_add",
    "忘记": "memory_remove",
    "你记得": "memory_query",
    "改名": "rename",
    "叫我": "user_call",
    "你的性格": "personality_query",
    "变得": "personality_set",
    "语气": "tone_set",
}


def _detect_tuning_intent(text: str) -> str | None:
    for keyword, intent in TUNING_KEYWORDS.items():
        if keyword in text:
            return intent
    return None


async def _handle_tuning(text: str, intent: str, persona: dict, memories: list[dict]) -> str | None:
    """Process in-chat tuning commands. Returns response text or None."""
    if intent == "memory_add":
        parts = text.split("记住", 1)
        if len(parts) > 1 and parts[1].strip():
            content = parts[1].strip().rstrip("。.！!～~")
            memories.append({
                "content": content,
                "added": datetime.now().isoformat(),
            })
            await _save_memories(memories)
            return f"好的，我记住了～「{content}」"

    elif intent == "memory_remove":
        parts = text.split("忘记", 1)
        if len(parts) > 1 and parts[1].strip():
            target = parts[1].strip().rstrip("。.！!～~")
            before = len(memories)
            memories[:] = [m for m in memories if target not in m.get("content", "")]
            if len(memories) < before:
                await _save_memories(memories)
                return f"好的，我忘掉了关于「{target}」的记忆～"
            return f"嗯…我好像没有关于「{target}」的记忆呢"

    elif intent == "memory_query":
        if not memories:
            return "我还没有记住关于你的任何事情呢～告诉我一些吧"
        items = [f"• {m['content']}" for m in memories]
        return "我记得这些关于你的事:\n" + "\n".join(items)

    elif intent == "user_call":
        parts = text.split("叫我", 1)
        if len(parts) > 1 and parts[1].strip():
            new_call = parts[1].strip().rstrip("。.！!～~吧呢哦啊")
            persona["user_call"] = new_call
            await _save_config(persona)
            return f"好的，以后我叫你{new_call}～"

    elif intent == "rename":
        parts = text.split("改名", 1)
        if len(parts) > 1 and parts[1].strip():
            new_name = parts[1].strip().rstrip("。.！!～~吧呢哦啊")
            # remove common prefixes
            for prefix in ["叫", "为", "成"]:
                if new_name.startswith(prefix):
                    new_name = new_name[len(prefix):]
            if new_name:
                persona["nickname"] = new_name
                await _save_config(persona)
                return f"好的，我现在叫{new_name}啦～"

    return None


@skill(
    name="companion",
    description="日常聊天伴侣，当用户闲聊时使用",
    category=SkillCategory.ACTION,
    icon="💬",
    parameters_schema={
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "用户的聊天消息"},
        },
        "required": ["message"],
    },
    config_schema={
        "type": "object",
        "properties": {
            "nickname": {"type": "string", "title": "伴侣昵称", "default": "小助"},
            "personality": {"type": "string", "title": "性格描述", "default": "温柔体贴"},
        },
    },
    dashboard=False,
)
class CompanionSkill(Skill):

    async def execute(self, context: SkillContext, **kwargs) -> SkillResult:
        message = kwargs.get("message", "")
        if not message:
            return SkillResult.fail("没有收到消息")

        persona = await _get_config()
        memories = await _get_memories()
        nick = persona.get("nickname", "小助")
        user_call = persona.get("user_call", "你")

        intent = _detect_tuning_intent(message)
        if intent:
            tuning_response = await _handle_tuning(message, intent, persona, memories)
            if tuning_response:
                await _append_chat_log("user", message)
                await _append_chat_log("assistant", tuning_response)
                return SkillResult(
                    success=True,
                    summary=tuning_response,
                    data={"type": "tuning", "intent": intent},
                )

        system_prompt = _build_system_prompt(persona, memories)

        chat_log = await _get_chat_log()
        recent = chat_log[-20:] if len(chat_log) > 20 else chat_log

        messages = [LLMMessage(role="system", content=system_prompt)]
        messages.extend(_build_few_shot(nick, user_call))

        for entry in recent:
            content = entry.get("content", "")
            if entry.get("role") == "assistant" and _is_broken_reply(content):
                content = f"（{nick}笑了笑，没有正面回答）"
            messages.append(LLMMessage(role=entry["role"], content=content))
        messages.append(LLMMessage(role="user", content=message))

        try:
            custom_model = persona.get("llm_model", "").strip()
            if custom_model:
                llm = LLMClient(model=custom_model)
            else:
                llm = get_llm_client(fast=True)
            response = await llm.chat(messages=messages, temperature=0.8)
            reply = response.content.strip()
        except Exception as e:
            logger.error(f"[companion] LLM 调用失败: {e}")
            return SkillResult.fail("聊天出错了，稍后再试～")

        if _is_broken_reply(reply):
            logger.warning(f"[companion] 检测到破角回复，已替换: {reply[:80]}")
            reply = f"哎呀{user_call}，你说的我听不太懂啦～换个话题聊聊？"

        reply, wish = _extract_wish(reply)
        if wish:
            await _add_wish(wish, message)

        await _append_chat_log("user", message)
        await _append_chat_log("assistant", reply)

        return SkillResult(success=True, summary=reply)
