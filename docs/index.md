# Personal Assistant CN · 文档

本仓库是一套可自托管的 **个人 AI 助手平台**：以 **FastAPI** 为核心，配合可插拔 **Skill**、**飞书 / 企业微信** 与 **Web** 渠道、**PostgreSQL / Redis** 记忆与检索，以及对 **A 股与金融数据源**的统一封装。

若你第一次在代码库里找路：先读本页的「阅读路径」，再按角色跳转到对应章节。

---

## 适合谁读

| 角色 | 建议顺序 |
|------|-----------|
| **想最快跑起来** | [快速开始](getting-started.md) → `docker compose up` 与 `.env` 必填项 |
| **要接入飞书 / 企微** | [架构概览](architecture.md) 里「消息路径」→ `app/channels/`、各渠道 API |
| **要写新 Skill 或改路由** | [架构概览](architecture.md) → `app/engine/router.py`、`app/skills/registry.py` |
| **要部署或找公网入口** | [部署与运维](deployment.md)：已写 **`http://43.143.114.183:8000/`** 等当前实例与各静态页路径；另含 `scripts/`、Docker、`.deploy.env` |
| **要接行情 / 代理** | [数据层与行情](data-and-apis.md) → `market_data`、`EM_PROXY_URL` |
| **要维护前端或 Workers** | [前端与边缘代理](frontend-and-proxy.md) → `web/`、`cf-em-proxy/` |

---

## 本项目解决什么问题

- **单一后端，多渠道**：同一套会话与 Skill 逻辑，不必为每个 IM 重写业务。
- **技能可扩展**：新能力以 Skill 为单位添加，由 LLM 或规则调度执行。
- **国内数据与工作流**：A 股、预警、持仓推送等与定时任务、`market_data` 语义层对齐。
- **可观测的部署路径**：Docker Compose 开发与脚本化远端同步，适合个人或小团队 VPS。

局限与边界（如实说明）：本项目不是「开箱即用的 SaaS」，需要你自行配置 LLM Key、数据库与渠道密钥；体量与可靠性需按自有环境加固。

---

## 文档章节说明

以下为各 Markdown 页面的职责简述，便于搜索与跳转。

### [GitHub Pages](github-pages.md)

静态文档托管到 **`*.github.io`** 的步骤：**Pages 源选 GitHub Actions**、workflow 手动触发与常见 404（未开 Actions 源、私有仓限制等）。

### [快速开始](getting-started.md)

Python 版本、`.env` 模板、`docker compose` 与裸跑 `uvicorn`、pytest 入口。

### [架构概览](architecture.md)

`main.py` 生命周期、`Channel → Dispatcher → Router → Skill` 数据流、记忆、向量库、调度器与 **`app/data/market.py`** 在架构中的位置。

### [部署与运维](deployment.md)

`deploy_sync` / `deploy_rebuild` / `remote_shell` 的差异、SSH 与环境文件、Docker 缓存与 `__pycache__` 注意点。

### [数据层与行情](data-and-apis.md)

Tushare、东方财富、代理与 `market_data` 接口约定；与 Skill 的依赖关系。

### [前端与边缘代理](frontend-and-proxy.md)

Next.js 目录约定、`package-lock.json` 与可复现安装、Cloudflare Worker / Deno 代理与 `EM_PROXY_URL`。

---

## 本地预览本站

```bash
pip install -e ".[docs]"
mkdocs serve -a 127.0.0.1:8001
```

预览地址一般为 **http://127.0.0.1:8001**（与本地 API 默认 `8000` 错开）。

构建静态站点（输出到项目根目录 `site/`，默认已 gitignore）：

```bash
mkdocs build
```

**线上阅读**：与仓库同名的 GitHub Pages 地址见根目录 [README.md](https://github.com/px39n/Personal_Assistant_CN#personal-assistant-cn) 中的在线文档链接。
