"""日期时间 Skill — 提供当前时间、日期计算、时区转换等功能。"""

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.skills.base import Skill, SkillCategory, SkillContext, SkillResult, skill


@skill(
    name="datetime_tool",
    description="获取当前日期时间、计算日期差、时区转换。适用于询问今天几号、星期几、两个日期相差几天等场景",
    category=SkillCategory.COMPUTE,
    icon="🕐",
    config_schema={
        "type": "object",
        "properties": {
            "default_timezone": {
                "type": "string",
                "title": "默认时区",
                "description": "未指定时区时使用的默认值",
                "default": "Asia/Shanghai",
            },
        },
    },
    parameters_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["now", "diff", "convert"],
                "description": "操作类型: now=当前时间, diff=日期差, convert=时区转换",
            },
            "timezone": {
                "type": "string",
                "description": "时区，如 Asia/Shanghai, America/New_York",
                "default": "Asia/Shanghai",
            },
            "date1": {
                "type": "string",
                "description": "日期1 (YYYY-MM-DD 格式)，用于 diff 计算",
            },
            "date2": {
                "type": "string",
                "description": "日期2 (YYYY-MM-DD 格式)，用于 diff 计算",
            },
        },
        "required": ["action"],
    },
)
class DatetimeToolSkill(Skill):
    """日期时间工具"""

    async def execute(self, context: SkillContext, **kwargs: Any) -> SkillResult:
        action = kwargs.get("action", "now")
        tz_name = kwargs.get("timezone", "Asia/Shanghai")

        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("Asia/Shanghai")
            tz_name = "Asia/Shanghai"

        if action == "now":
            return self._get_now(tz, tz_name)
        elif action == "diff":
            return self._calc_diff(kwargs.get("date1", ""), kwargs.get("date2", ""))
        elif action == "convert":
            return self._get_now(tz, tz_name)
        else:
            return SkillResult.fail(f"未知操作: {action}")

    def _get_now(self, tz: ZoneInfo, tz_name: str) -> SkillResult:
        now = datetime.now(tz)
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday = weekdays[now.weekday()]

        info = {
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "weekday": weekday,
            "timezone": tz_name,
            "timestamp": int(now.timestamp()),
        }

        summary = f"当前时间（{tz_name}）: {info['datetime']} {weekday}"
        return SkillResult(success=True, data=info, summary=summary)

    def _calc_diff(self, date1_str: str, date2_str: str) -> SkillResult:
        if not date1_str or not date2_str:
            return SkillResult.fail("日期差计算需要提供 date1 和 date2 (YYYY-MM-DD)")

        try:
            d1 = datetime.strptime(date1_str, "%Y-%m-%d")
            d2 = datetime.strptime(date2_str, "%Y-%m-%d")
        except ValueError as e:
            return SkillResult.fail(f"日期格式错误: {e}，请使用 YYYY-MM-DD")

        diff = abs((d2 - d1).days)
        info = {
            "date1": date1_str,
            "date2": date2_str,
            "diff_days": diff,
            "diff_weeks": diff // 7,
            "diff_months_approx": round(diff / 30.44, 1),
        }

        summary = f"{date1_str} 与 {date2_str} 相差 {diff} 天（约 {info['diff_weeks']} 周）"
        return SkillResult(success=True, data=info, summary=summary)
