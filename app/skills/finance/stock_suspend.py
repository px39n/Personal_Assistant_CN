"""A 股停复牌查询 Skill — 查找最近停牌和复牌的股票。"""

from datetime import datetime, timedelta
from typing import Any

import tushare as ts
from loguru import logger

from app.config import settings
from app.skills.base import Skill, SkillCategory, SkillContext, SkillResult, skill

PERIOD_OPTIONS = {
    "today": 0,
    "3d": 3,
    "7d": 7,
    "14d": 14,
    "30d": 30,
}


_name_cache: dict[str, str] = {}
_cache_time: float = 0


def _load_name_map() -> dict[str, str]:
    """加载 ts_code → 股票名称 映射（通过统一数据层）"""
    import time as _time
    global _name_cache, _cache_time
    if _name_cache and (_time.time() - _cache_time < 3600):
        return _name_cache
    try:
        from app.data import market_data
        df = market_data.get_stock_list()
        _name_cache = dict(zip(df["ts_code"], df["name"]))
        _cache_time = _time.time()
        return _name_cache
    except Exception as e:
        logger.warning(f"加载股票名称失败: {e}")
        return _name_cache or {}


@skill(
    name="stock_suspend",
    description=(
        "查询 A 股最近的停牌和复牌股票列表。"
        "适用于用户问'最近有哪些股票复牌了'、'哪些股票停牌了'、'停复牌信息'等场景。"
    ),
    category=SkillCategory.STOCK,
    icon="⏸️",
    config_schema={
        "type": "object",
        "properties": {
            "default_lookback_days": {
                "type": "integer",
                "title": "默认回看天数",
                "description": "未指定时间范围时，默认查最近多少天",
                "default": 7,
                "minimum": 1,
                "maximum": 30,
            },
        },
    },
    parameters_schema={
        "type": "object",
        "properties": {
            "query_type": {
                "type": "string",
                "enum": ["resumed", "suspended", "both"],
                "description": "查询类型: resumed=最近复牌, suspended=当前停牌, both=两者都查",
            },
            "days": {
                "type": "string",
                "description": "查最近多少天内的数据（1-30），如 '7'",
            },
        },
        "required": [],
    },
)
class StockSuspendSkill(Skill):

    async def on_load(self) -> None:
        if not settings.tushare_token:
            logger.warning("TUSHARE_TOKEN 未配置，stock_suspend 技能将不可用")

    async def execute(self, context: SkillContext, **kwargs: Any) -> SkillResult:
        if not settings.tushare_token:
            return SkillResult.fail("TUSHARE_TOKEN 未配置。请在 .env 中填写 Tushare token。")

        query_type = (
            kwargs.get("query_type") or kwargs.get("type") or "both"
        )
        days = int(
            kwargs.get("days") or kwargs.get("lookback")
            or self.cfg("default_lookback_days", 7)
        )
        days = max(1, min(days, 30))

        try:
            import asyncio
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: self._query(query_type, days))
            return result
        except Exception as e:
            logger.error(f"停复牌查询失败: {e}", exc_info=True)
            return SkillResult.fail(f"查询失败: {str(e)}")

    def _query(self, query_type: str, days: int) -> SkillResult:
        from app.data import market_data
        name_map = _load_name_map()

        sections = []
        all_data = {}

        if query_type in ("resumed", "both"):
            df = market_data.get_suspend(suspend_type="R", days=days)
            resumed = []
            for _, row in df.iterrows():
                code = row["ts_code"]
                name = name_map.get(code, code)
                date = row["trade_date"]
                resumed.append({"code": code, "name": name, "date": date})

            all_data["resumed"] = resumed
            if resumed:
                lines = [f"最近 {days} 天复牌股票（{len(resumed)} 只）:"]
                for s in resumed:
                    lines.append(f"  {s['name']}（{s['code']}）{s['date'][:4]}-{s['date'][4:6]}-{s['date'][6:]} 复牌")
                sections.append("\n".join(lines))
            else:
                sections.append(f"最近 {days} 天没有复牌股票。")

        if query_type in ("suspended", "both"):
            df = market_data.get_suspend(suspend_type="S", days=days)
            codes_seen = set()
            suspended = []
            for _, row in df.iterrows():
                code = row["ts_code"]
                if code in codes_seen:
                    continue
                codes_seen.add(code)
                name = name_map.get(code, code)
                date = row["trade_date"]
                suspended.append({"code": code, "name": name, "date": date})

            all_data["suspended"] = suspended
            if suspended:
                lines = [f"最近 {days} 天停牌股票（{len(suspended)} 只）:"]
                for s in suspended[:30]:
                    lines.append(f"  {s['name']}（{s['code']}）{s['date'][:4]}-{s['date'][4:6]}-{s['date'][6:]} 停牌")
                if len(suspended) > 30:
                    lines.append(f"  …及其他 {len(suspended) - 30} 只")
                sections.append("\n".join(lines))
            else:
                sections.append(f"最近 {days} 天没有停牌股票。")

        summary = "\n\n".join(sections)
        return SkillResult(success=True, data=all_data, summary=summary)
