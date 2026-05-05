import httpx
from typing import Optional
from app.skills.base import Skill, SkillCategory, SkillContext, SkillResult, skill
from app.engine.memory import memory_store

@skill(
    name="weather",
    description="查询指定城市或当前位置的实时天气和预报。如果用户没有提供城市，会尝试从记忆中读取默认城市。",
    category=SkillCategory.ACTION,
    icon="🌤️",
    parameters_schema={
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市名称，例如：北京、Shanghai、New York。如果不确定可以留空。",
            },
        },
        "required": [],
    }
)
class WeatherSkill(Skill):
    async def execute(self, context: SkillContext, city: Optional[str] = None, **kwargs) -> SkillResult:
        user_id = context.user_id or "web_user"
        
        # 1. 决定要查询的城市
        target_city = city
        
        # 如果没有提供城市，尝试从记忆中读取
        if not target_city:
            # 优先级 1: Skill 专属记忆中的默认城市
            target_city = context.skill_memory.get("default_city")
            
        if not target_city:
            # 优先级 2: 用户全局偏好中的位置 (location)
            target_city = context.global_memory.get("location")
            
        if not target_city:
            return SkillResult.fail(
                "请告诉我你想查询哪个城市的天气？\n"
                "💡 提示：你可以在【个人记忆】中设置 `location`，或者直接告诉我你的常驻城市，我会自动记住它。"
            )
            
        # 如果用户显式提供了城市，将其记录到 Skill 专属记忆中
        if city and user_id:
            # 记录最近查询的城市
            await memory_store.set_skill(user_id, self.name, "last_queried_city", city)
            
            # 如果目前没有设置默认城市，自动将其设为默认
            if not context.skill_memory.get("default_city"):
                await memory_store.set_skill(user_id, self.name, "default_city", city)

        # 2. 调用外部 API 查询天气 (这里使用 wttr.in 的 JSON 接口)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"https://wttr.in/{target_city}?format=j1", timeout=10.0)
                if resp.status_code != 200:
                    return SkillResult.fail(f"无法获取 {target_city} 的天气数据，服务可能暂时不可用。")
                
                data = resp.json()
                current = data["current_condition"][0]
                temp_c = current["temp_C"]
                weather_desc = current["weatherDesc"][0]["value"]
                humidity = current["humidity"]
                wind_speed = current["windspeedKmph"]
                feels_like = current["FeelsLikeC"]
                
                summary = (
                    f"{target_city} 当前天气：{weather_desc}，"
                    f"气温 {temp_c}°C (体感 {feels_like}°C)，"
                    f"湿度 {humidity}%，风速 {wind_speed}km/h。"
                )
                
                return SkillResult(
                    success=True,
                    data=current,
                    summary=summary
                )
        except Exception as e:
            return SkillResult.fail(f"天气查询发生错误: {str(e)}")
