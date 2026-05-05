"""测试 Skill 注册机制和自动发现。"""

import pytest

from app.skills.base import Skill, SkillCategory, SkillContext, SkillResult, skill
from app.skills.registry import SkillRegistry


@skill(
    name="test_echo",
    description="测试用 echo skill",
    category=SkillCategory.SEARCH,
    parameters_schema={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
)
class EchoSkill(Skill):
    async def execute(self, context: SkillContext, **kwargs) -> SkillResult:
        query = kwargs.get("query", "")
        return SkillResult(success=True, data=query, summary=f"Echo: {query}")


@skill(
    name="test_disabled",
    description="禁用的 skill",
    category=SkillCategory.ACTION,
    enabled=False,
)
class DisabledSkill(Skill):
    async def execute(self, context: SkillContext, **kwargs) -> SkillResult:
        return SkillResult.fail("should not run")


class TestSkillRegistry:
    @pytest.fixture
    def registry(self):
        return SkillRegistry()

    @pytest.mark.asyncio
    async def test_register_and_get(self, registry):
        echo = EchoSkill()
        await registry.register(echo)
        assert registry.count == 1
        assert registry.get("test_echo") is echo

    @pytest.mark.asyncio
    async def test_disabled_skill_not_registered(self, registry):
        disabled = DisabledSkill()
        await registry.register(disabled)
        assert registry.count == 0
        assert registry.get("test_disabled") is None

    @pytest.mark.asyncio
    async def test_unregister(self, registry):
        echo = EchoSkill()
        await registry.register(echo)
        assert registry.count == 1
        await registry.unregister("test_echo")
        assert registry.count == 0

    @pytest.mark.asyncio
    async def test_list_by_category(self, registry):
        echo = EchoSkill()
        await registry.register(echo)
        search_skills = registry.list_by_category(SkillCategory.SEARCH)
        assert len(search_skills) == 1
        action_skills = registry.list_by_category(SkillCategory.ACTION)
        assert len(action_skills) == 0

    @pytest.mark.asyncio
    async def test_tool_definitions(self, registry):
        echo = EchoSkill()
        await registry.register(echo)
        tools = registry.get_tool_definitions()
        assert len(tools) == 1
        assert tools[0].name == "test_echo"
        assert tools[0].description == "测试用 echo skill"

    @pytest.mark.asyncio
    async def test_skill_descriptions(self, registry):
        echo = EchoSkill()
        await registry.register(echo)
        desc = registry.get_skill_descriptions()
        assert "test_echo" in desc
        assert "测试用 echo skill" in desc

    @pytest.mark.asyncio
    async def test_execute_skill(self, registry):
        echo = EchoSkill()
        await registry.register(echo)
        ctx = SkillContext(user_id="test_user")
        result = await echo.execute(ctx, query="hello")
        assert result.success is True
        assert result.data == "hello"
        assert result.summary == "Echo: hello"

    @pytest.mark.asyncio
    async def test_auto_discover(self, registry):
        """测试自动发现 — 应该能找到 web_search skill"""
        await registry.auto_discover("app.skills")
        assert registry.count >= 1
        assert registry.get("web_search") is not None
