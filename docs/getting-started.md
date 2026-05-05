# 快速开始

## 依赖

- Python **3.11+**
- （推荐）Docker / Docker Compose — 运行 PostgreSQL、Redis、SearXNG 与应用

## 配置

1. 复制环境变量模板（若仓库提供 `.env.example`）为 `.env`。
2. 必填项通常包括：`DATABASE_URL`、`REDIS_URL`、LLM 相关、`TUSHARE_TOKEN`、飞书/企微密钥等 —— 以 `app/config.py` 为准。

## Docker Compose（推荐）

在项目根目录：

```bash
docker compose up -d --build
```

应用默认监听容器内 `8000`，映射见 `docker-compose.yml`。

## 本地开发（不含 Docker 数据库）

```bash
pip install -e .
uvicorn app.main:app --reload --port 8000
```

若仅内存模式或简化后端，需自行对齐 `memory_store` 与数据库配置（见 `app/engine/memory.py`）。

## 测试

```bash
pip install -e ".[dev]"
pytest tests/ -v
```
