"""持仓快报 Skill — 管理关注股票列表，一键查看全部行情。

用户通过对话管理自己的关注列表:
- "添加亨通光电" / "关注芯原股份"
- "移除嘉美包装" / "取消关注xxx"
- "看看我的持仓" / "持仓快报"
- "我的关注列表"

关注列表存在 Skill 记忆里，每个用户独立。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import tushare as ts
from loguru import logger

from app.config import settings
from app.engine.memory import memory_store
from app.skills.base import Skill, SkillCategory, SkillContext, SkillResult, skill
from app.skills.finance.stock_chart import _get_pro, _load_stock_map, _resolve, _code_cache

WATCHLIST_KEY = "watchlist"
PUSH_CONFIG_KEY = "push_config"

_CST = timezone(timedelta(hours=8))


def _now_cst() -> datetime:
    return datetime.now(_CST)


_last_prices: dict[str, float] = {}

def _fetch_index_summary_realtime() -> str:
    """通过统一数据层获取大盘指数摘要。"""
    from app.data import market_data

    indices = market_data.get_index()
    parts: list[str] = []
    for idx in indices:
        close = idx.get("close")
        pct = idx.get("pct_chg")
        label = idx.get("name", "")
        if close is not None and pct is not None:
            arrow = "🔴" if pct >= 0 else "🟢"
            parts.append(f"{arrow}{label} {close:.0f} {'+' if pct >= 0 else ''}{pct:.2f}%")
    return "  ".join(parts) if parts else "大盘数据暂不可用"

# ── 盘中播报上下文 ────────────────────────────
# 每个交易日积累，收盘/新日自动清空

_day_context: list[dict] = []
_day_context_date: str = ""          # "2026-03-09" — 当前上下文属于哪一天

_news_cache: list[dict] = []         # [{stock, title, snippet}, ...]
_news_cache_time: datetime | None = None
_news_significant_prices: dict[str, float] = {}  # 上次搜新闻时的价格

PUSH_PRESETS = {
    "open_only": {"label": "仅开盘 (9:25)", "minutes": []},
    "30min": {"label": "每30分钟", "minutes": [30]},
    "1h": {"label": "每1小时", "minutes": [60]},
    "3h": {"label": "每3小时", "minutes": [180]},
    "off": {"label": "关闭推送", "minutes": None},
}

# ── 播报引擎 ─────────────────────────────────

_COMMENTARY_SYSTEM = """\
你是一位专业的A股盘中播报员。每一条播报都是写给"此刻刚打开消息"的读者——\
他们可能没看过今天之前的任何推送。

核心原则：
- 每条播报是一篇**自包含的当日简析**：概括今天到目前为止的整体走势和关键变化
- 像一个懂行的朋友在聊天，不是机器人报数据
- 重点是"判断"和"值得关注的点"，不要复读价格数字（用户能看到价格表）
- 如果有消息面/新闻驱动，简短点出原因
- 3-4句话，简洁有力

禁止：
- 不要说"上次我提到"、"之前播报过"之类的话——读者没看过前面的推送
- 不要用 Markdown 链接（飞书卡片不渲染）
- 不需要写时间、标题，直接进入分析

你会收到今天之前所有播报的历史记录作为内部参考，用它来让你的判断更精准，\
但输出内容必须对第一次看到的人完全可读。
"""


def _reset_day_context_if_needed() -> None:
    """新交易日自动清空上下文。"""
    global _day_context, _day_context_date, _news_cache, _news_cache_time
    global _news_significant_prices
    today = _now_cst().strftime("%Y-%m-%d")
    if _day_context_date != today:
        _day_context = []
        _day_context_date = today
        _news_cache = []
        _news_cache_time = None
        _news_significant_prices = {}


def _should_refresh_news(current_prices: dict[str, float]) -> bool:
    """判断是否需要重新搜索新闻。"""
    if not _news_cache_time:
        return True
    elapsed = (_now_cst() - _news_cache_time).total_seconds()
    if elapsed > 7200:  # >2 小时
        return True
    for code, price in current_prices.items():
        prev = _news_significant_prices.get(code)
        if prev and prev > 0 and abs(price - prev) / prev > 0.02:
            return True
    return False


async def _search_stock_news(watchlist: list[dict], top_n: int = 3) -> list[dict]:
    """用 SearXNG 搜索关注股票的当日新闻。"""
    global _news_cache, _news_cache_time, _news_significant_prices
    results: list[dict] = []
    names = [item["name"] for item in watchlist[:8]]
    query = " OR ".join(f"{n} 股票" for n in names) + " 今日"
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(
                f"{settings.searxng_url}/search",
                params={"q": query, "format": "json", "number_of_results": 10},
            )
            resp.raise_for_status()
            data = resp.json()
        for item in data.get("results", [])[:10]:
            title = item.get("title", "")
            snippet = item.get("content", "")
            if any(n in title or n in snippet for n in names):
                results.append({
                    "title": title[:80],
                    "snippet": snippet[:150],
                })
                if len(results) >= top_n * len(names):
                    break
        _news_cache = results
        _news_cache_time = _now_cst()
    except Exception as e:
        logger.warning(f"[播报] 新闻搜索失败: {e}")
    return results


async def generate_commentary(
    watchlist: list[dict],
    price_lines: str,
    current_prices: dict[str, float],
    *,
    is_open: bool = False,
) -> str:
    """生成盘中播报文字（3-4句话）。

    Args:
        watchlist: 关注列表
        price_lines: 已格式化的价格文字（给 LLM 参考）
        current_prices: {ts_code: price}
        is_open: 是否开盘推送
    """
    from app.engine.llm import LLMMessage, get_llm_client

    _reset_day_context_if_needed()

    if _should_refresh_news(current_prices):
        await _search_stock_news(watchlist)
        _news_significant_prices.update(current_prices)
    news = _news_cache

    now = _now_cst()
    time_str = now.strftime("%H:%M")
    push_type = "开盘推送" if is_open else "盘中推送"

    # 构建内部历史参考（LLM 可以看，但不能在输出中引用）
    history = ""
    if _day_context:
        parts = []
        for entry in _day_context:
            prices_snap = ", ".join(
                f"{c}: {p:.2f}" for c, p in list(entry.get("prices", {}).items())[:6]
            )
            parts.append(f"[{entry['time']}] 价格({prices_snap}) → {entry['commentary']}")
        history = "\n".join(parts)

    news_text = "暂无相关新闻"
    if news:
        news_text = "\n".join(
            f"- {n['title']}: {n['snippet']}" for n in news[:8]
        )

    user_msg = (
        f"当前时间: {time_str}（{push_type}）\n\n"
        f"当前价格数据:\n{price_lines}\n\n"
        f"今日相关新闻:\n{news_text}"
    )
    if history:
        user_msg = (
            f"【内部参考 — 今日早前播报记录，用于辅助判断，不要在输出中提及'看过前面'】\n"
            f"{history}\n\n{user_msg}"
        )

    messages = [
        LLMMessage(role="system", content=_COMMENTARY_SYSTEM),
        LLMMessage(role="user", content=user_msg),
    ]

    try:
        llm = get_llm_client(fast=True)
        resp = await llm.chat(messages=messages, temperature=0.6, max_tokens=300)
        commentary = resp.content.strip()
    except Exception as e:
        logger.error(f"[播报] LLM 调用失败: {e}")
        commentary = ""

    if commentary:
        _day_context.append({
            "time": time_str,
            "prices": dict(current_prices),
            "commentary": commentary,
        })

    return commentary


@skill(
    name="portfolio",
    description=(
        "管理股票关注列表（watchlist）和行情快报。这是一个关注列表，不是真实持仓，"
        "只需要股票名称或代码，不需要数量、价格、成本等信息。支持：\n"
        "1. 添加关注（'添加亨通光电'、'关注嘉美包装'、'增加持仓XX'）— 只需股票名\n"
        "2. 移除关注（'移除嘉美包装'）\n"
        "3. 查看关注列表（'我的关注列表'、'我的持仓'）\n"
        "4. 行情快报（'持仓快报'）\n"
        "5. 修改推送频率（'改成每半小时推送'）\n"
        "用户说'添加/增加/关注XX'时直接调用，不要追问数量或价格。"
    ),
    category=SkillCategory.STOCK,
    icon="💼",
    config_schema={
        "type": "object",
        "properties": {
            "default_push_frequency": {
                "type": "string",
                "title": "默认推送频率",
                "description": "新用户的默认推送频率",
                "default": "open_only",
                "enum": ["open_only", "30min", "1h", "3h", "off"],
            },
        },
    },
    parameters_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "remove", "list", "report", "set_frequency"],
                "description": (
                    "操作类型: add=添加关注, remove=移除关注, "
                    "list=查看关注列表, report=持仓快报, "
                    "set_frequency=修改推送频率"
                ),
            },
            "stock_name": {
                "type": "string",
                "description": "股票名称或代码（add/remove 时需要）。可以用逗号或空格分隔多只股票，如'亨通光电,芯原股份,嘉美包装'",
            },
            "frequency": {
                "type": "string",
                "enum": ["open_only", "30min", "1h", "3h", "off"],
                "description": "推送频率（set_frequency 时需要）",
            },
        },
        "required": ["action"],
    },
)
class PortfolioSkill(Skill):

    def _freq_card(self, push_config: dict) -> dict:
        """生成频率按钮的 ui_card 标记，供渠道层渲染"""
        freq = push_config.get("frequency", "open_only")
        return {"type": "freq_buttons", "current_freq": freq}

    async def execute(self, context: SkillContext, **kwargs: Any) -> SkillResult:
        if not settings.tushare_token:
            return SkillResult.fail("TUSHARE_TOKEN 未配置")

        raw_action = kwargs.get("action", "report")
        action = self._normalize_action(raw_action)
        stock_name = (
            kwargs.get("stock_name") or kwargs.get("symbol")
            or kwargs.get("name") or kwargs.get("stock") or ""
        ).strip()
        frequency = kwargs.get("frequency", "")
        user_id = context.user_id or "anonymous"

        watchlist: list = context.skill_memory.get(WATCHLIST_KEY, [])
        push_config: dict = context.skill_memory.get(PUSH_CONFIG_KEY, {})

        if action == "add":
            result = await self._add(user_id, watchlist, stock_name)
        elif action == "remove":
            result = await self._remove(user_id, watchlist, stock_name)
        elif action == "list":
            result = self._list(watchlist, push_config)
        elif action == "report":
            result = await self._report(watchlist)
        elif action == "set_frequency":
            result = await self._set_frequency(user_id, frequency, push_config)
            push_config = await memory_store.get_skill(user_id, self.name, PUSH_CONFIG_KEY) or push_config
        else:
            result = SkillResult.fail(f"未知操作: {raw_action}")

        if not result.ui_card:
            result.ui_card = self._freq_card(push_config)

        return result

    @staticmethod
    def _normalize_action(action: str) -> str:
        """将 LLM 生成的各种 action 名称映射到标准名"""
        a = action.lower().replace("-", "_").replace(" ", "_")
        if any(k in a for k in ("add", "watch", "follow", "关注")):
            return "add"
        if any(k in a for k in ("remove", "delete", "unwatch", "unfollow", "取消")):
            return "remove"
        if any(k in a for k in ("list", "show", "查看", "列表")):
            return "list"
        if any(k in a for k in ("report", "summary", "快报", "持仓")):
            return "report"
        if any(k in a for k in ("freq", "push", "interval", "推送", "频率", "adjust", "modify", "change", "set", "update")):
            return "set_frequency"
        return action

    async def _add(self, user_id: str, watchlist: list, name: str) -> SkillResult:
        if not name:
            return SkillResult.fail("请提供要添加的股票名称或代码")

        import re
        names = re.split(r"[,，\s、]+", name)
        names = [n.strip() for n in names if n.strip()]

        added = []
        skipped = []
        failed = []

        for n in names:
            ts_code, stock_name = _resolve(n)
            if not ts_code:
                failed.append(n)
                continue

            exists = any(item["code"] == ts_code for item in watchlist)
            if exists:
                skipped.append(stock_name)
                continue

            watchlist.append({"code": ts_code, "name": stock_name})
            added.append(f"{stock_name}（{ts_code}）")

        if added:
            await memory_store.set_skill(user_id, self.name, WATCHLIST_KEY, watchlist)

        lines = []
        if added:
            lines.append(f"已添加 {len(added)} 只: {', '.join(added)}")
        if skipped:
            lines.append(f"已在列表中: {', '.join(skipped)}")
        if failed:
            lines.append(f"未找到: {', '.join(failed)}")
        lines.append(f"当前关注 {len(watchlist)} 只股票。")

        return SkillResult(success=True, summary="\n".join(lines))

    async def _remove(self, user_id: str, watchlist: list, name: str) -> SkillResult:
        if not name:
            return SkillResult.fail("请提供要移除的股票名称或代码")

        ts_code, stock_name = _resolve(name)
        target_code = ts_code

        if not target_code:
            for item in watchlist:
                if name in item["name"] or item["name"] in name:
                    target_code = item["code"]
                    stock_name = item["name"]
                    break

        if not target_code:
            return SkillResult.fail(f"关注列表中没有「{name}」")

        before = len(watchlist)
        watchlist = [w for w in watchlist if w["code"] != target_code]

        if len(watchlist) == before:
            return SkillResult.fail(f"关注列表中没有「{name}」")

        await memory_store.set_skill(user_id, self.name, WATCHLIST_KEY, watchlist)

        return SkillResult(
            success=True,
            summary=f"已移除 {stock_name}（{target_code}）。\n当前关注 {len(watchlist)} 只股票。",
        )

    def _list(self, watchlist: list, push_config: dict) -> SkillResult:
        if not watchlist:
            return SkillResult(
                success=True,
                summary="你的关注列表为空。\n试试说「添加平安银行」来添加股票。",
            )

        freq_key = push_config.get("frequency", "open_only")
        freq_label = PUSH_PRESETS.get(freq_key, {}).get("label", freq_key)

        lines = [f"关注列表（{len(watchlist)} 只）  推送: {freq_label}\n"]
        for i, item in enumerate(watchlist, 1):
            lines.append(f"  {i}. {item['name']}（{item['code']}）")
        lines.append(f"\n修改推送频率可说「改成每半小时推送」或「关闭推送」")

        return SkillResult(success=True, data=watchlist, summary="\n".join(lines))

    async def _set_frequency(self, user_id: str, frequency: str, push_config: dict) -> SkillResult:
        if frequency not in PUSH_PRESETS:
            options = "、".join(p["label"] for p in PUSH_PRESETS.values())
            return SkillResult.fail(f"不支持的频率。可选: {options}")

        push_config["frequency"] = frequency
        await memory_store.set_skill(user_id, self.name, PUSH_CONFIG_KEY, push_config)

        label = PUSH_PRESETS[frequency]["label"]
        return SkillResult(
            success=True,
            summary=f"推送频率已修改为: {label}",
        )

    async def _report(self, watchlist: list) -> SkillResult:
        if not watchlist:
            return SkillResult(
                success=True,
                summary="关注列表为空，无法生成快报。\n试试说「添加亨通光电」来添加股票。",
            )

        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: self._fetch_report(watchlist))
        return result

    def _fetch_report(self, watchlist: list, *, intraday: bool = False) -> SkillResult:
        """获取持仓快报（实时行情 via AKShare stock_zh_a_spot_em）。

        Args:
            intraday: True 时为盘中推送，会附加「区间涨跌」（vs 上次推送价格）。

        返回的 data 里增加 current_prices 供播报引擎使用。
        """
        from app.data import market_data

        now = _now_cst()
        title = "盘中快报" if intraday else "持仓快报"
        lines = [f"{title}（{now.strftime('%m/%d %H:%M')}）\n"]
        up_count = 0
        down_count = 0
        errors = []
        current_prices: dict[str, float] = {}

        for item in watchlist:
            ts_code = item["code"]
            name = item["name"]
            try:
                snap = market_data.get_price(ts_code, days=0)
                if not snap:
                    errors.append(name)
                    continue
                q = snap[0]

                price = q.get("price", q.get("close", 0))
                pct = q.get("pct_chg", 0)
                current_prices[ts_code] = price

                arrow = "🔴" if pct >= 0 else "🟢"
                if pct >= 0:
                    up_count += 1
                else:
                    down_count += 1

                line = f"  {arrow} {name}  {price:.2f}  今日{'+' if pct >= 0 else ''}{pct:.2f}%"

                if intraday and ts_code in _last_prices:
                    prev = _last_prices[ts_code]
                    if prev > 0:
                        delta_pct = (price - prev) / prev * 100
                        if abs(delta_pct) >= 1.0:
                            line += f"  ⚡{'+' if delta_pct >= 0 else ''}{delta_pct:.2f}%"
                        elif abs(delta_pct) >= 0.01:
                            d_arrow = "⬆" if delta_pct > 0 else ("⬇" if delta_pct < 0 else "➡")
                            line += f"  {d_arrow}{'+' if delta_pct >= 0 else ''}{delta_pct:.2f}%"

                lines.append(line)

            except Exception as e:
                logger.warning(f"获取 {name}({ts_code}) 行情失败: {e}")
                errors.append(name)

        if errors:
            lines.append(f"\n  ⚠️ {', '.join(errors)} 数据获取失败")

        try:
            idx_line = _fetch_index_summary_realtime()
            lines.append(f"\n{idx_line}")
        except Exception:
            lines.append(f"\n大盘数据获取失败")

        _last_prices.update(current_prices)

        return SkillResult(
            success=True,
            data={
                "watchlist": watchlist,
                "up": up_count,
                "down": down_count,
                "current_prices": current_prices,
            },
            summary="\n".join(lines),
        )
