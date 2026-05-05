# 架构概览

## 入口

- **`app/main.py`**：FastAPI 应用、`lifespan` 里初始化数据库、Redis、渠道、**APScheduler** 定时任务。
- **`app/config.py`**：Pydantic Settings，从 `.env` 读取配置。

## 消息路径

```
用户 → Channel(Web/Feishu/WeCom) → Dispatcher → Router(LLM 选 Skill 或直连)
       → Skill.execute → 回复 Channel
```

- **`app/channels/`**：各渠道适配；统一转成内部消息格式。
- **`app/engine/dispatcher.py`**：分发；部分场景（如伴侣调教群）绕过路由直达 Skill。
- **`app/engine/router.py`**：LLM + function calling，选择要执行的 Skill。
- **`app/skills/`**：具体能力；`SkillRegistry` 自动发现注册。

## 记忆与检索

- **`app/engine/memory.py`**：`MemoryStore` / `PersistentMemoryStore`（Redis + PostgreSQL）。
- **`app/engine/vectorstore.py`**：文档向量与 RAG（若启用）。

## 定时任务

- **`app/engine/scheduler.py`**：持仓推送、预警、全市场扫描等；依赖交易日历与配置开关。

## 统一数据层

- **`app/data/market.py`**（`market_data`）：业务语义接口 `get_price`、`get_fund_flow`、`get_main_force` 等 —— 详见 [数据层与行情](data-and-apis.md)。
