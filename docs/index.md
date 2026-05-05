# Personal Assistant CN

面向中国市场的个人 AI 助手：**FastAPI** 后端、可插拔 **Skill**、**飞书 / 企业微信** 渠道、**A 股**相关技能与统一数据层。

## 文档导航

| 章节 | 内容 |
|------|------|
| [快速开始](getting-started.md) | 环境、Docker、本地运行 |
| [架构概览](architecture.md) | 渠道、路由、Skill、调度 |
| [部署与运维](deployment.md) | `scripts/`、Docker、注意事项 |
| [数据层与行情](data-and-apis.md) | `market_data`、Tushare、东方财富代理 |
| [前端与边缘代理](frontend-and-proxy.md) | `web/`、`cf-em-proxy/`、`package-lock` |

## 本地预览本站文档

```bash
pip install -e ".[docs]"
mkdocs serve
```

浏览器打开 `http://127.0.0.1:8000`（若端口冲突可加 `-a 127.0.0.1:8001`）。

构建静态站点输出到 `site/`：

```bash
mkdocs build
```
