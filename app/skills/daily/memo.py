"""备忘录 Skill — 用 Skill 级记忆存储和检索用户的备忘/待办事项。"""

import time
from typing import Any

from loguru import logger

from app.engine.memory import memory_store
from app.skills.base import Skill, SkillCategory, SkillContext, SkillResult, skill


@skill(
    name="memo",
    description="管理个人备忘录和待办事项。支持添加、查看、删除备忘。适用于用户说'帮我记住XX'、'我的备忘录'、'删除第X条备忘'等场景。",
    category=SkillCategory.ACTION,
    icon="📝",
    parameters_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "list", "delete"],
                "description": "操作类型: add=添加备忘, list=查看所有备忘, delete=删除备忘",
            },
            "content": {
                "type": "string",
                "description": "备忘内容（add 时必填）",
            },
            "index": {
                "type": "integer",
                "description": "要删除的备忘序号（delete 时必填，从1开始）",
            },
        },
        "required": ["action"],
    },
)
class MemoSkill(Skill):
    """使用 Skill 级记忆存储备忘录"""

    MEMO_KEY = "memos"

    async def execute(self, context: SkillContext, **kwargs: Any) -> SkillResult:
        action = kwargs.get("action", "list")
        user_id = context.user_id or "web_user"

        # 从 Skill 记忆中加载现有备忘
        memos: list = context.skill_memory.get(self.MEMO_KEY, [])

        if action == "add":
            return await self._add_memo(user_id, memos, kwargs.get("content", ""))
        elif action == "list":
            return self._list_memos(memos)
        elif action == "delete":
            return await self._delete_memo(user_id, memos, kwargs.get("index"))
        else:
            return SkillResult.fail(f"未知操作: {action}")

    async def _add_memo(self, user_id: str, memos: list, content: str) -> SkillResult:
        if not content.strip():
            return SkillResult.fail("备忘内容不能为空")

        memo_entry = {
            "content": content.strip(),
            "created_at": time.strftime("%Y-%m-%d %H:%M"),
        }
        memos.append(memo_entry)

        await memory_store.set_skill(user_id, self.name, self.MEMO_KEY, memos)

        return SkillResult(
            success=True,
            data=memo_entry,
            summary=f"已添加备忘: {content.strip()}\n当前共 {len(memos)} 条备忘。",
        )

    def _list_memos(self, memos: list) -> SkillResult:
        if not memos:
            return SkillResult(
                success=True,
                data=[],
                summary="你还没有任何备忘录。可以说'帮我记住XX'来添加。",
            )

        lines = ["**你的备忘录:**\n"]
        for i, memo in enumerate(memos, 1):
            lines.append(f"{i}. {memo['content']}  (添加于 {memo.get('created_at', '未知')})")

        return SkillResult(
            success=True,
            data=memos,
            summary="\n".join(lines),
        )

    async def _delete_memo(self, user_id: str, memos: list, index: Any) -> SkillResult:
        if index is None:
            return SkillResult.fail("请指定要删除的备忘序号（从1开始）")

        idx = int(index)
        if idx < 1 or idx > len(memos):
            return SkillResult.fail(f"序号 {idx} 超出范围（共 {len(memos)} 条备忘）")

        removed = memos.pop(idx - 1)
        await memory_store.set_skill(user_id, self.name, self.MEMO_KEY, memos)

        return SkillResult(
            success=True,
            data=removed,
            summary=f"已删除备忘: {removed['content']}\n剩余 {len(memos)} 条备忘。",
        )
