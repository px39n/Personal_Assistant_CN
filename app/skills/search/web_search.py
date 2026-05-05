"""在线搜索 Skill — 通过 SearxNG 搜索互联网获取实时信息。"""

from typing import Any

import httpx
from loguru import logger

from app.config import settings
from app.skills.base import Skill, SkillCategory, SkillContext, SkillResult, skill


@skill(
    name="web_search",
    description="搜索互联网获取实时信息，适用于需要最新数据、新闻、事实查询的场景",
    category=SkillCategory.SEARCH,
    icon="🔍",
    config_schema={
        "type": "object",
        "properties": {
            "max_results": {
                "type": "integer",
                "title": "默认结果数",
                "description": "每次搜索返回的默认最大结果数量",
                "default": 5,
                "minimum": 1,
                "maximum": 20,
            },
            "search_language": {
                "type": "string",
                "title": "搜索语言",
                "description": "偏好的搜索语言",
                "default": "zh-CN",
                "enum": ["zh-CN", "en", "ja", "ko", "auto"],
            },
        },
    },
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词",
            },
            "num_results": {
                "type": "integer",
                "description": "返回结果数量",
                "default": 5,
            },
        },
        "required": ["query"],
    },
)
class WebSearchSkill(Skill):
    """通过 SearxNG 实例进行在线搜索"""

    async def execute(self, context: SkillContext, **kwargs: Any) -> SkillResult:
        query = kwargs.get("query", "")
        num_results = kwargs.get("num_results", 5)

        if not query:
            return SkillResult.fail("搜索关键词不能为空")

        try:
            results = await self._search_searxng(query, num_results)

            if not results:
                return SkillResult(
                    success=True,
                    data=[],
                    summary=f"搜索 '{query}' 未找到相关结果",
                )

            # 构建摘要
            summary_lines = [f"搜索 '{query}' 找到 {len(results)} 条结果:\n"]
            for i, r in enumerate(results, 1):
                summary_lines.append(f"{i}. [{r['title']}]({r['url']})")
                if r.get("content"):
                    summary_lines.append(f"   {r['content'][:200]}")

            return SkillResult(
                success=True,
                data=results,
                summary="\n".join(summary_lines),
                ui_card={
                    "type": "search_results",
                    "query": query,
                    "results": results,
                },
            )

        except Exception as e:
            logger.error(f"搜索失败: {e}", exc_info=True)
            return SkillResult.fail(f"搜索执行失败: {str(e)}")

    async def _search_searxng(self, query: str, num_results: int) -> list[dict]:
        """调用 SearxNG API"""
        url = f"{settings.searxng_url}/search"
        params = {
            "q": query,
            "format": "json",
            "number_of_results": num_results,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        results = []
        for item in data.get("results", [])[:num_results]:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
                "engine": item.get("engine", ""),
            })

        return results
