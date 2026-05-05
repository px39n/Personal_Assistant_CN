"""Skill 注册中心 — 自动发现、注册、管理所有 Skill。"""

import importlib
import pkgutil
from pathlib import Path
from typing import Optional

from loguru import logger

from app.engine.llm import ToolDefinition
from app.skills.base import Skill, SkillCategory, _SKILL_REGISTRY_MARKER


class SkillRegistry:
    """
    Skill 注册中心。

    - auto_discover(): 扫描 skills 目录，自动导入并注册所有标记了 @skill 的类
    - register() / unregister(): 手动注册/卸载
    - get(): 按名称获取
    - list_tools(): 生成 LLM function calling 用的工具列表
    """

    def __init__(self):
        self._skills: dict[str, Skill] = {}

    async def auto_discover(self, package_path: str = "app.skills") -> None:
        """
        自动扫描指定包下所有子模块，找到标记了 @skill 的类并实例化注册。
        支持嵌套目录（如 app/skills/search/web_search.py）。
        """
        try:
            package = importlib.import_module(package_path)
        except ModuleNotFoundError:
            logger.warning(f"无法导入包 {package_path}，跳过自动发现")
            return

        package_dir = Path(package.__file__).parent

        for module_info in pkgutil.walk_packages(
            path=[str(package_dir)],
            prefix=f"{package_path}.",
        ):
            # 跳过 base, registry 等基础模块
            module_name = module_info.name.split(".")[-1]
            if module_name in ("base", "registry", "__init__"):
                continue

            try:
                module = importlib.import_module(module_info.name)
            except Exception as e:
                logger.warning(f"导入模块 {module_info.name} 失败: {e}")
                continue

            # 扫描模块中所有类，找标记了 @skill 的
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, Skill)
                    and attr is not Skill
                    and getattr(attr, _SKILL_REGISTRY_MARKER, False)
                ):
                    await self.register(attr())

        logger.info(f"自动发现完成，已注册 {len(self._skills)} 个 Skill: {list(self._skills.keys())}")

    async def register(self, skill_instance: Skill) -> None:
        """注册一个 Skill 实例"""
        if not skill_instance.enabled:
            logger.debug(f"Skill {skill_instance.name} 已禁用，跳过注册")
            return

        if skill_instance.name in self._skills:
            logger.warning(f"Skill {skill_instance.name} 已存在，将被覆盖")

        await skill_instance.on_load()
        self._skills[skill_instance.name] = skill_instance
        logger.info(f"已注册 Skill: {skill_instance}")

    async def unregister(self, name: str) -> None:
        """卸载一个 Skill"""
        if name in self._skills:
            await self._skills[name].on_unload()
            del self._skills[name]
            logger.info(f"已卸载 Skill: {name}")

    def get(self, name: str) -> Optional[Skill]:
        """按名称获取 Skill"""
        return self._skills.get(name)

    def list_all(self) -> list[Skill]:
        """获取所有已注册 Skill"""
        return list(self._skills.values())

    def list_by_category(self, category: SkillCategory) -> list[Skill]:
        """按类别获取 Skill"""
        return [s for s in self._skills.values() if s.category == category]

    def get_tool_definitions(self) -> list[ToolDefinition]:
        """生成所有 Skill 的工具定义，用于 LLM function calling"""
        return [
            ToolDefinition(
                name=s.name,
                description=s.description,
                parameters=s.parameters_schema,
            )
            for s in self._skills.values()
            if s.enabled
        ]

    def get_skill_descriptions(self) -> str:
        """生成所有 Skill 的文字描述，用于 prompt 构造"""
        lines = []
        for s in self._skills.values():
            if s.enabled:
                lines.append(f'- "{s.name}": {s.description}')
        return "\n".join(lines)

    @property
    def count(self) -> int:
        return len(self._skills)


# 全局单例
skill_registry = SkillRegistry()
