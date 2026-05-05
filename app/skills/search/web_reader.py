"""网页读取 Skill — 给定 URL，抓取并提取网页正文内容。"""

import re
from typing import Any

import httpx
from loguru import logger

from app.skills.base import Skill, SkillCategory, SkillContext, SkillResult, skill


@skill(
    name="web_reader",
    description="读取指定网页的内容，提取正文文字。适用于用户给了一个链接想了解内容的场景",
    category=SkillCategory.SEARCH,
    icon="📄",
    config_schema={
        "type": "object",
        "properties": {
            "default_max_length": {
                "type": "integer",
                "title": "默认最大字数",
                "description": "提取网页内容的默认最大字符数",
                "default": 5000,
                "minimum": 500,
                "maximum": 50000,
            },
            "timeout": {
                "type": "number",
                "title": "超时时间(秒)",
                "description": "HTTP 请求超时时间",
                "default": 15,
                "minimum": 5,
                "maximum": 60,
            },
        },
    },
    parameters_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要读取的网页 URL",
            },
            "max_length": {
                "type": "integer",
                "description": "返回内容的最大字符数",
                "default": 5000,
            },
        },
        "required": ["url"],
    },
)
class WebReaderSkill(Skill):
    """通过 HTTP 抓取网页，提取正文内容"""

    # 常见的需要移除的 HTML 标签
    STRIP_TAGS = {"script", "style", "nav", "footer", "header", "aside", "iframe", "noscript"}

    async def execute(self, context: SkillContext, **kwargs: Any) -> SkillResult:
        url = kwargs.get("url", "")
        max_length = kwargs.get("max_length", 5000)

        if not url:
            return SkillResult.fail("URL 不能为空")

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            html = await self._fetch_page(url)
            text = self._extract_text(html)

            if not text.strip():
                return SkillResult(
                    success=True,
                    data={"url": url, "content": ""},
                    summary=f"网页 {url} 未提取到有效内容（可能是纯 JS 渲染页面）",
                )

            # 截断
            if len(text) > max_length:
                text = text[:max_length] + "\n\n...[内容已截断]"

            return SkillResult(
                success=True,
                data={"url": url, "content": text, "length": len(text)},
                summary=f"网页 {url} 内容（{len(text)} 字）:\n\n{text}",
                ui_card={
                    "type": "web_content",
                    "url": url,
                    "preview": text[:500],
                },
            )

        except httpx.HTTPStatusError as e:
            return SkillResult.fail(f"HTTP 错误 {e.response.status_code}: {url}")
        except httpx.ConnectError:
            return SkillResult.fail(f"无法连接到 {url}")
        except httpx.TimeoutException:
            return SkillResult.fail(f"请求超时: {url}")
        except Exception as e:
            logger.error(f"网页读取失败: {e}", exc_info=True)
            return SkillResult.fail(f"网页读取失败: {str(e)}")

    async def _fetch_page(self, url: str) -> str:
        """抓取网页 HTML"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.text

    def _extract_text(self, html: str) -> str:
        """从 HTML 提取纯文本（轻量实现，不依赖 BeautifulSoup）"""
        # 移除不需要的标签及其内容
        for tag in self.STRIP_TAGS:
            html = re.sub(rf"<{tag}[\s>].*?</{tag}>", "", html, flags=re.DOTALL | re.IGNORECASE)

        # 移除 HTML 注释
        html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)

        # 将 <br>, <p>, <div>, <li>, <h1>-<h6> 等块级元素转为换行
        html = re.sub(r"<(?:br|p|div|li|h[1-6]|tr|blockquote)[^>]*>", "\n", html, flags=re.IGNORECASE)

        # 移除所有剩余 HTML 标签
        html = re.sub(r"<[^>]+>", "", html)

        # 解码常见 HTML 实体
        entities = {
            "&amp;": "&", "&lt;": "<", "&gt;": ">",
            "&quot;": '"', "&#39;": "'", "&nbsp;": " ",
            "&mdash;": "—", "&ndash;": "–", "&hellip;": "…",
        }
        for entity, char in entities.items():
            html = html.replace(entity, char)

        # 清理多余空白
        lines = []
        for line in html.split("\n"):
            line = line.strip()
            if line:
                lines.append(line)

        return "\n".join(lines)
