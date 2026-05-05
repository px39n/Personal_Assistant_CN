"""汇率查询 Skill — 查询实时汇率并进行货币换算，带记忆偏好。"""

from typing import Any, Optional

import httpx
from loguru import logger

from app.engine.memory import memory_store
from app.skills.base import Skill, SkillCategory, SkillContext, SkillResult, skill


@skill(
    name="currency_exchange",
    description="查询实时汇率并进行货币换算。支持 CNY（人民币）、USD（美元）、EUR（欧元）、JPY（日元）、GBP（英镑）、HKD（港币）等全球货币。",
    category=SkillCategory.ACTION,
    icon="💱",
    parameters_schema={
        "type": "object",
        "properties": {
            "amount": {
                "type": "number",
                "description": "要换算的金额",
                "default": 1,
            },
            "from_currency": {
                "type": "string",
                "description": "源货币代码，如 USD、CNY、EUR、JPY",
                "default": "USD",
            },
            "to_currency": {
                "type": "string",
                "description": "目标货币代码，如 CNY、USD、EUR、JPY",
                "default": "CNY",
            },
        },
        "required": ["from_currency", "to_currency"],
    },
)
class CurrencyExchangeSkill(Skill):
    """使用免费汇率 API 进行货币换算"""

    # 使用 exchangerate-api.com 的免费接口（无需 API Key）
    BASE_URL = "https://open.er-api.com/v6/latest"

    async def execute(self, context: SkillContext, **kwargs: Any) -> SkillResult:
        amount = float(kwargs.get("amount", 1))
        from_cur = kwargs.get("from_currency", "USD").upper()
        to_cur = kwargs.get("to_currency", "CNY").upper()
        user_id = context.user_id or "web_user"

        # 如果用户没有指定目标货币，尝试从 Skill 记忆中读取
        if not kwargs.get("to_currency"):
            preferred = context.skill_memory.get("preferred_to_currency")
            if preferred:
                to_cur = preferred.upper()

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.BASE_URL}/{from_cur}")
                if resp.status_code != 200:
                    return SkillResult.fail(f"无法获取 {from_cur} 的汇率数据")

                data = resp.json()
                if data.get("result") != "success":
                    return SkillResult.fail(f"汇率查询失败: {data.get('error-type', '未知错误')}")

                rates = data.get("rates", {})
                if to_cur not in rates:
                    return SkillResult.fail(
                        f"不支持的目标货币: {to_cur}。常用货币: CNY, USD, EUR, JPY, GBP, HKD, KRW"
                    )

                rate = rates[to_cur]
                converted = round(amount * rate, 2)

                # 记录到 Skill 记忆
                if user_id:
                    await memory_store.set_skill(
                        user_id, self.name, "last_from_currency", from_cur
                    )
                    await memory_store.set_skill(
                        user_id, self.name, "last_to_currency", to_cur
                    )

                summary = (
                    f"💱 {amount} {from_cur} = **{converted} {to_cur}**\n"
                    f"汇率: 1 {from_cur} = {rate} {to_cur}\n"
                    f"数据来源: Open Exchange Rates (实时)"
                )

                return SkillResult(
                    success=True,
                    data={
                        "amount": amount,
                        "from_currency": from_cur,
                        "to_currency": to_cur,
                        "rate": rate,
                        "converted": converted,
                    },
                    summary=summary,
                )

        except Exception as e:
            logger.error(f"汇率查询失败: {e}", exc_info=True)
            return SkillResult.fail(f"汇率查询失败: {str(e)}")
