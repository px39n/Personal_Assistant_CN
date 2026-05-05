"""测试三层记忆系统。"""

import pytest

from app.engine.memory import MemoryStore


class TestMemoryStore:
    def setup_method(self):
        self.store = MemoryStore()

    @pytest.mark.asyncio
    async def test_global_memory(self):
        await self.store.set_global("user1", "name", "张三")
        assert await self.store.get_global("user1", "name") == "张三"
        assert await self.store.get_global("user1", "missing") is None
        assert await self.store.get_global("user1", "missing", "default") == "default"

    @pytest.mark.asyncio
    async def test_global_memory_update(self):
        await self.store.set_global("user1", "city", "北京")
        await self.store.set_global("user1", "city", "上海")
        assert await self.store.get_global("user1", "city") == "上海"

    @pytest.mark.asyncio
    async def test_skill_memory(self):
        await self.store.set_skill("user1", "flight_booking", "airline", "南航")
        assert await self.store.get_skill("user1", "flight_booking", "airline") == "南航"
        assert await self.store.get_skill("user1", "flight_booking", "missing") is None
        assert await self.store.get_skill("user1", "other_skill", "airline") is None

    @pytest.mark.asyncio
    async def test_session_memory(self):
        await self.store.set_session("conv1", "topic", "订机票")
        assert await self.store.get_session("conv1", "topic") == "订机票"
        await self.store.clear_session("conv1")
        assert await self.store.get_session("conv1", "topic") is None

    @pytest.mark.asyncio
    async def test_chat_history(self):
        await self.store.add_message("conv1", "user", "你好")
        await self.store.add_message("conv1", "assistant", "你好！")
        history = await self.store.get_chat_history("conv1")
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["content"] == "你好！"

    @pytest.mark.asyncio
    async def test_chat_history_limit(self):
        for i in range(10):
            await self.store.add_message("conv1", "user", f"消息 {i}")
        history = await self.store.get_chat_history("conv1", limit=3)
        assert len(history) == 3
        assert history[0]["content"] == "消息 7"

    @pytest.mark.asyncio
    async def test_build_skill_context(self):
        await self.store.set_global("user1", "city", "北京")
        await self.store.set_skill("user1", "web_search", "preferred_engine", "google")
        await self.store.add_message("conv1", "user", "搜索天气")
        ctx = await self.store.build_skill_context("user1", "web_search", "conv1")
        assert ctx["global_memory"]["city"] == "北京"
        assert ctx["skill_memory"]["preferred_engine"] == "google"
        assert len(ctx["chat_history"]) == 1

    @pytest.mark.asyncio
    async def test_get_all_global(self):
        await self.store.set_global("user1", "a", 1)
        await self.store.set_global("user1", "b", 2)
        all_mem = await self.store.get_all_global("user1")
        assert all_mem == {"a": 1, "b": 2}
