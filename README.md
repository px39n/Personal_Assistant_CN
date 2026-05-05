# Personal Assistant CN

面向中国使用场景的个人 **AI 助手后端**：可插拔 **Skill**、多 **消息渠道**（Web / 飞书 / 企业微信）、**A 股与金融数据**能力，以及记忆、搜索与定时任务。

|  |  |
|--|--|
| **在线文档** | [https://px39n.github.io/Personal_Assistant_CN/](https://px39n.github.io/Personal_Assistant_CN/) |
| **运行时** | Python 3.11+，推荐 Docker Compose |
| **核心框架** | FastAPI · SQLAlchemy/asyncpg · Redis · APScheduler |

---

## 功能概览

- **对话与路由**：LLM 结合 function calling，由 `Router` 选择合适 Skill，支持流式输出（Web/SSE 等）。
- **渠道集成**：`Web`、`Feishu`、`WeCom` 统一经 `Dispatcher` 进入同一套引擎；飞书长连接、企微回调等按 `app/channels/` 扩展。
- **Skill 体系**：天气、翻译、搜索、知识库、浏览器自动化、**A 股**（行情、图表、预警、持仓等）等，在 `app/skills/` 注册与发现。
- **记忆与 RAG**：持久化记忆（PostgreSQL + Redis），可选向量检索与文档管道（见 `app/engine/memory.py`、`vectorstore.py`）。
- **定时任务**：交易日历、预警扫描、推送等由 `app/engine/scheduler.py` 与配置开关驱动。
- **可选前端**：`web/` 为 Next.js 管理/聊天界面；核心能力不依赖前端即可通过 HTTP API 使用。

更细的流程与模块职责见 **[架构概览](docs/architecture.md)**（或在线文档同页）。

---

## 技术栈（摘要）

| 层级 | 主要选型 |
|------|-----------|
| API / 实时 | FastAPI，Uvicorn，`sse-starlette`，WebSockets |
| LLM | OpenAI 兼容接口（`openai` SDK），可配置多家供应商 Base URL |
| 数据 | PostgreSQL + pgvector，Redis，async SQLAlchemy |
| 集成 | 飞书 `lark-oapi`，金融数据 Tushare / AkShare / yfinance，东方财富经可选 **HTTP 代理** |
| 运维 | Docker Compose，SearxNG（可选），MkDocs Material 文档 |

完整依赖见 [`pyproject.toml`](pyproject.toml)。

---

## 仓库结构

```text
app/                 # FastAPI 应用：API、渠道、引擎、Skill、静态页
web/                 # Next.js 前端（可选）
cf-em-proxy/         # 东方财富等 API 的边缘代理（Cloudflare Worker / Deno）
scripts/             # 部署与健康检查脚本（SSH + Docker）
deploy/              # 环境模板（deploy.env、生产 env 示例）
docs/                # MkDocs 文档源
tests/               # pytest
```

---

## 快速开始

### 使用 Docker Compose（推荐）

```bash
cp .env.example .env
# 编辑 .env：至少配置 LLM、数据库/Redis（与 compose 一致）、可选 Tushare / 飞书 / 企微

docker compose up -d --build
```

应用默认映射 **http://127.0.0.1:8000**。静态页示例：`/static/index.html`、`/static/skills.html` 等。

### 仅本地跑 API（自建 PG/Redis）

```bash
pip install -e .
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

详细步骤、测试命令与常见坑见 **[快速开始](docs/getting-started.md)**。

---

## 配置说明

- 所有敏感项通过 **环境变量** 或根目录 **`.env`** 注入（**勿提交**）。模板：[`.env.example`](.env.example)。
- 字段与默认值以 **`app/config.py`** 为准（`pydantic-settings`）。
- 生产环境合并密钥到 **`.env.production`** 的辅助脚本：`python scripts/merge_env_to_production.py`，模板见 `deploy/env.production.template`。

---

## 部署与运维

| 脚本 | 用途 |
|------|------|
| [`scripts/deploy_sync.py`](scripts/deploy_sync.py) | **日常**：打包 `app/` → 上传 → 容器内热更新 + 重启 + 冒烟检查（**不重 build 镜像**）。 |
| [`scripts/deploy_rebuild.py`](scripts/deploy_rebuild.py) | Dockerfile / Compose / 依赖变更：远端 **build** 并拉起 stack。 |
| [`scripts/remote_shell.py`](scripts/remote_shell.py) | SSH 执行单条远端命令（查日志、`docker ps` 等）。 |

SSH 主机与口令放入仓库根目录 **`.deploy.env`**（已由 `.gitignore` 忽略），模板：**[`deploy/deploy.env.template`](deploy/deploy.env.template)**；亦可导出同名环境变量。

---

## 文档（本地预览）

```bash
pip install -e ".[docs]"
mkdocs serve -a 127.0.0.1:8001
```

浏览器打开 **http://127.0.0.1:8001**（避免与后端 `uvicorn :8000` 冲突）。静态导出：`mkdocs build` → 输出目录 **`site/`**。

**GitHub Pages**：仓库 **Settings → Pages → Source**: **GitHub Actions**；workflow 名为 **Deploy docs to GitHub Pages**。也可用本机：`gh workflow run "Deploy docs to GitHub Pages" -R px39n/Personal_Assistant_CN --ref main`。

---

## 安全与合规

- **永远不要**提交 `.env`、`.deploy.env`、真实 API Key / 令牌。
- 东方财富类接口若部署在境外或不稳定网络，建议使用 **`cf-em-proxy/`** 并配置 `EM_PROXY_URL`。
- 将仓库改为 **Public** 前，务必确认历史中无泄密；服务端 SSH 建议使用密钥登录并轮换口令。

---

## 相关链接

- 边缘代理说明：**[`cf-em-proxy/README.md`](cf-em-proxy/README.md)**
- 前端交接与结构：**[`web/README.md`](web/README.md)**

如需改进文档或报错，直接在仓库提 **Issue** 即可。
