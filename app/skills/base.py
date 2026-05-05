"""Skill 基类与装饰器。每个 Skill 继承此基类，通过 @skill 装饰器自动注册。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


CATEGORY_META: dict[str, dict] = {
    "search":    {"label": "搜索", "color": "#3b82f6"},
    "knowledge": {"label": "知识", "color": "#8b5cf6"},
    "research":  {"label": "研究", "color": "#6366f1"},
    "action":    {"label": "日常", "color": "#10b981"},
    "compute":   {"label": "计算", "color": "#f59e0b"},
    "output":    {"label": "输出", "color": "#ec4899"},
    "browser":   {"label": "浏览器", "color": "#06b6d4"},
    "stock":     {"label": "A股股票", "color": "#ef5350"},
}


class SkillCategory(str, Enum):
    """Skill 分类"""
    SEARCH = "search"
    KNOWLEDGE = "knowledge"
    RESEARCH = "research"
    ACTION = "action"
    COMPUTE = "compute"
    OUTPUT = "output"
    BROWSER = "browser"
    STOCK = "stock"


@dataclass
class SkillResult:
    """Skill 执行结果"""
    success: bool = True
    data: Any = None
    summary: str = ""
    error: Optional[str] = None
    ui_card: Optional[dict] = None

    @staticmethod
    def fail(error: str) -> "SkillResult":
        return SkillResult(success=False, error=error)


@dataclass
class SkillMemoryEntry:
    """Skill 级记忆条目"""
    key: str
    value: Any
    skill_name: str
    user_id: Optional[str] = None


@dataclass
class SkillContext:
    """传给 Skill.execute 的上下文"""
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None
    chat_history: list = field(default_factory=list)
    user_preferences: dict = field(default_factory=dict)
    skill_memory: dict = field(default_factory=dict)
    global_memory: dict = field(default_factory=dict)
    location: Optional[str] = None


class Skill(ABC):
    """
    Skill 基类。所有 skill 必须继承此类。

    子类需要实现:
    - execute(): 核心执行逻辑
    - 类属性: name, description, category, parameters_schema
    """

    # 子类必须定义
    name: str = ""
    description: str = ""
    category: SkillCategory = SkillCategory.SEARCH
    parameters_schema: dict = {}

    # 可选覆盖
    enabled: bool = True
    version: str = "0.1.0"
    icon: str = "🔧"
    config_schema: dict = {}
    dashboard: bool = False

    @abstractmethod
    async def execute(self, context: SkillContext, **kwargs) -> SkillResult:
        """执行 skill 核心逻辑。kwargs 对应 parameters_schema 定义的参数。"""
        ...

    async def on_load(self) -> None:
        """Skill 加载时的初始化钩子（可选覆盖）"""
        pass

    async def on_unload(self) -> None:
        """Skill 卸载时的清理钩子（可选覆盖）"""
        pass

    # ── 配置管理 ──────────────────────────────

    def get_config(self) -> dict:
        """获取当前配置（懒初始化）"""
        if not hasattr(self, "_config"):
            self._config: dict = {}
            for key, prop in self.config_schema.get("properties", {}).items():
                if "default" in prop:
                    self._config[key] = prop["default"]
        return dict(self._config)

    def update_config(self, updates: dict) -> dict:
        """更新配置，仅允许 config_schema 中定义的 key"""
        cfg = self.get_config()
        allowed = self.config_schema.get("properties", {})
        for key, value in updates.items():
            if key in allowed:
                self._config[key] = value
        return self.get_config()

    def cfg(self, key: str, default: Any = None) -> Any:
        """快速读取某个配置项"""
        return self.get_config().get(key, default)

    # ── 序列化 ──────────────────────────────

    def get_detail(self) -> dict:
        """返回完整详情（给 UI 用）"""
        cat_meta = CATEGORY_META.get(self.category.value, {})
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "category_label": cat_meta.get("label", self.category.value),
            "category_color": cat_meta.get("color", "#888"),
            "icon": self.icon,
            "version": self.version,
            "enabled": self.enabled,
            "config": self.get_config(),
            "config_schema": self.config_schema,
            "parameters_schema": self.parameters_schema,
        }

    def get_summary(self) -> dict:
        """返回摘要信息（给列表用）"""
        cat_meta = CATEGORY_META.get(self.category.value, {})
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "category_label": cat_meta.get("label", self.category.value),
            "category_color": cat_meta.get("color", "#888"),
            "icon": self.icon,
            "version": self.version,
            "enabled": self.enabled,
            "has_config": bool(self.config_schema.get("properties")),
            "has_dashboard": self.dashboard,
        }

    def get_tool_definition(self) -> dict:
        """生成 OpenAI function calling 格式的工具定义"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters_schema,
        }

    def __repr__(self) -> str:
        return f"<Skill: {self.name} [{self.category.value}] v{self.version}>"


# ── 装饰器 ──────────────────────────────────
_SKILL_REGISTRY_MARKER = "__skill_registered__"


def skill(
    name: str,
    description: str,
    category: SkillCategory,
    parameters_schema: Optional[dict] = None,
    enabled: bool = True,
    icon: str = "🔧",
    config_schema: Optional[dict] = None,
    dashboard: bool = False,
):
    """
    装饰器，标记一个类为可注册的 Skill。

    用法:
        @skill(
            name="web_search",
            description="搜索互联网获取实时信息",
            category=SkillCategory.SEARCH,
            icon="🔍",
            config_schema={
                "type": "object",
                "properties": {
                    "max_results": {
                        "type": "integer",
                        "title": "最大结果数",
                        "default": 5,
                    },
                },
            },
            parameters_schema={...},
        )
        class WebSearchSkill(Skill):
            async def execute(self, context, **kwargs):
                ...
    """

    def decorator(cls):
        if not issubclass(cls, Skill):
            raise TypeError(f"{cls.__name__} 必须继承 Skill 基类")
        cls.name = name
        cls.description = description
        cls.category = category
        cls.parameters_schema = parameters_schema or {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "用户查询"},
            },
            "required": ["query"],
        }
        cls.enabled = enabled
        cls.icon = icon
        cls.config_schema = config_schema or {}
        cls.dashboard = dashboard
        setattr(cls, _SKILL_REGISTRY_MARKER, True)
        return cls

    return decorator
