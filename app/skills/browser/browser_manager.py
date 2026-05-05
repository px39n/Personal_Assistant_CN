"""Browser Manager — shared Playwright browser instance lifecycle management.

Provides a singleton async context manager for browser pages.
All browser-related skills share this manager to avoid spawning multiple browsers.
"""

import asyncio
from typing import Optional

from loguru import logger


class BrowserManager:
    """Manages a shared Playwright Chromium browser instance."""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def ensure_browser(self):
        """Lazily start browser on first use."""
        async with self._lock:
            if self._browser and self._browser.is_connected():
                return

            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            logger.info("Playwright Chromium browser launched (headless)")

    async def new_page(self):
        """Create a new browser page (tab)."""
        await self.ensure_browser()
        context = await self._browser.new_context(
            viewport={"width": 1280, "height": 720},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = await context.new_page()
        return page

    async def shutdown(self):
        """Close browser and playwright."""
        async with self._lock:
            if self._browser:
                await self._browser.close()
                self._browser = None
                logger.info("Browser closed")
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
                logger.info("Playwright stopped")

    @property
    def is_running(self) -> bool:
        return self._browser is not None and self._browser.is_connected()


# Global singleton
browser_manager = BrowserManager()
