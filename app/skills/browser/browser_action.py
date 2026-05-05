"""Browser Action Skill — navigate, screenshot, extract content, click, fill forms.

This is the primary browser automation skill that allows the AI to interact
with web pages through natural language commands.
"""

import base64
import os
import time
from typing import Any, Optional

from loguru import logger

from app.engine.memory import memory_store
from app.skills.base import Skill, SkillCategory, SkillContext, SkillResult, skill
from app.skills.browser.browser_manager import browser_manager


@skill(
    name="browser_action",
    enabled=False,
    description=(
        "Control a headless browser to interact with web pages. "
        "Supports: navigate to URL, take screenshot, extract page text/links, "
        "click elements, fill form fields, and execute JavaScript. "
        "Use this when the user wants to automate browser tasks, "
        "check a webpage visually, or interact with web applications."
    ),
    category=SkillCategory.BROWSER,
    icon="🖥️",
    parameters_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["navigate", "screenshot", "extract", "click", "fill", "evaluate"],
                "description": (
                    "Action to perform: "
                    "navigate=open a URL, "
                    "screenshot=capture page screenshot, "
                    "extract=get page text and links, "
                    "click=click an element by selector, "
                    "fill=fill a form field, "
                    "evaluate=run JavaScript"
                ),
            },
            "url": {
                "type": "string",
                "description": "URL to navigate to (for navigate action)",
            },
            "selector": {
                "type": "string",
                "description": "CSS selector for click/fill actions",
            },
            "value": {
                "type": "string",
                "description": "Value to fill (for fill action) or JS code (for evaluate)",
            },
        },
        "required": ["action"],
    },
)
class BrowserActionSkill(Skill):
    """Headless browser automation via Playwright."""

    async def execute(self, context: SkillContext, **kwargs: Any) -> SkillResult:
        action = kwargs.get("action", "")
        user_id = context.user_id or "web_user"

        try:
            if action == "navigate":
                return await self._navigate(kwargs.get("url", ""), user_id)
            elif action == "screenshot":
                return await self._screenshot(user_id)
            elif action == "extract":
                return await self._extract(user_id)
            elif action == "click":
                return await self._click(kwargs.get("selector", ""), user_id)
            elif action == "fill":
                return await self._fill(
                    kwargs.get("selector", ""), kwargs.get("value", ""), user_id
                )
            elif action == "evaluate":
                return await self._evaluate(kwargs.get("value", ""), user_id)
            else:
                return SkillResult.fail(f"Unknown browser action: {action}")
        except Exception as e:
            logger.error(f"Browser action failed: {e}", exc_info=True)
            return SkillResult.fail(f"Browser operation failed: {str(e)}")

    async def _get_or_create_page(self, user_id: str):
        """Get the user's current page or create a new one."""
        # Store page reference in skill memory for session continuity
        page = await browser_manager.new_page()
        return page

    async def _navigate(self, url: str, user_id: str) -> SkillResult:
        if not url:
            return SkillResult.fail("Please provide a URL to navigate to")

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        page = await self._get_or_create_page(user_id)
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            title = await page.title()
            status = response.status if response else "unknown"

            # Take a screenshot after navigation
            screenshot_bytes = await page.screenshot(type="png", full_page=False)
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode()

            # Extract visible text (first 2000 chars)
            text_content = await page.evaluate(
                "() => document.body ? document.body.innerText.slice(0, 2000) : ''"
            )

            await page.context.close()

            summary = (
                f"Opened: **{title}**\n"
                f"URL: {url}\n"
                f"Status: {status}\n\n"
                f"Page preview (first 500 chars):\n{text_content[:500]}..."
            )

            return SkillResult(
                success=True,
                data={
                    "url": url,
                    "title": title,
                    "status": status,
                    "text_preview": text_content[:2000],
                    "screenshot_base64": screenshot_b64,
                },
                summary=summary,
                ui_card={
                    "type": "browser_screenshot",
                    "title": title,
                    "url": url,
                    "screenshot": screenshot_b64,
                },
            )
        except Exception as e:
            await page.context.close()
            raise

    async def _screenshot(self, user_id: str) -> SkillResult:
        page = await self._get_or_create_page(user_id)
        try:
            title = await page.title()
            url = page.url
            screenshot_bytes = await page.screenshot(type="png", full_page=False)
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
            await page.context.close()

            return SkillResult(
                success=True,
                data={
                    "title": title,
                    "url": url,
                    "screenshot_base64": screenshot_b64,
                },
                summary=f"Screenshot captured: {title} ({url})",
                ui_card={
                    "type": "browser_screenshot",
                    "title": title,
                    "url": url,
                    "screenshot": screenshot_b64,
                },
            )
        except Exception as e:
            await page.context.close()
            raise

    async def _extract(self, user_id: str) -> SkillResult:
        page = await self._get_or_create_page(user_id)
        try:
            title = await page.title()
            url = page.url

            # Extract text content
            text = await page.evaluate(
                "() => document.body ? document.body.innerText.slice(0, 5000) : ''"
            )

            # Extract links
            links = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a[href]'))
                    .slice(0, 20)
                    .map(a => ({text: a.innerText.trim().slice(0, 80), href: a.href}))
                    .filter(l => l.text && l.href.startsWith('http'));
            }""")

            await page.context.close()

            links_text = "\n".join(
                [f"- [{l['text']}]({l['href']})" for l in links[:10]]
            )

            summary = (
                f"**{title}** ({url})\n\n"
                f"Content (first 2000 chars):\n{text[:2000]}\n\n"
                f"Links found ({len(links)}):\n{links_text}"
            )

            return SkillResult(
                success=True,
                data={
                    "title": title,
                    "url": url,
                    "text": text,
                    "links": links,
                },
                summary=summary,
            )
        except Exception as e:
            await page.context.close()
            raise

    async def _click(self, selector: str, user_id: str) -> SkillResult:
        if not selector:
            return SkillResult.fail("Please provide a CSS selector to click")

        page = await self._get_or_create_page(user_id)
        try:
            await page.click(selector, timeout=10000)
            await page.wait_for_load_state("domcontentloaded", timeout=10000)

            title = await page.title()
            url = page.url
            screenshot_bytes = await page.screenshot(type="png", full_page=False)
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
            await page.context.close()

            return SkillResult(
                success=True,
                data={
                    "clicked": selector,
                    "new_title": title,
                    "new_url": url,
                    "screenshot_base64": screenshot_b64,
                },
                summary=f"Clicked `{selector}`. Page is now: **{title}** ({url})",
                ui_card={
                    "type": "browser_screenshot",
                    "title": title,
                    "url": url,
                    "screenshot": screenshot_b64,
                },
            )
        except Exception as e:
            await page.context.close()
            raise

    async def _fill(self, selector: str, value: str, user_id: str) -> SkillResult:
        if not selector:
            return SkillResult.fail("Please provide a CSS selector for the input field")
        if not value:
            return SkillResult.fail("Please provide a value to fill in")

        page = await self._get_or_create_page(user_id)
        try:
            await page.fill(selector, value, timeout=10000)

            title = await page.title()
            await page.context.close()

            return SkillResult(
                success=True,
                data={"selector": selector, "value": value},
                summary=f"Filled `{selector}` with '{value}' on **{title}**",
            )
        except Exception as e:
            await page.context.close()
            raise

    async def _evaluate(self, js_code: str, user_id: str) -> SkillResult:
        if not js_code:
            return SkillResult.fail("Please provide JavaScript code to execute")

        page = await self._get_or_create_page(user_id)
        try:
            result = await page.evaluate(js_code)
            await page.context.close()

            return SkillResult(
                success=True,
                data={"result": result},
                summary=f"JavaScript executed. Result: {str(result)[:500]}",
            )
        except Exception as e:
            await page.context.close()
            raise

    async def on_load(self) -> None:
        """Pre-warm: don't launch browser until first use."""
        logger.info("BrowserActionSkill loaded (browser will start on first use)")

    async def on_unload(self) -> None:
        """Shutdown browser when skill unloads."""
        await browser_manager.shutdown()
