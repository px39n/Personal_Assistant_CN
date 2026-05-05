# 部署与运维

## 脚本一览（`scripts/`）

| 脚本 | 用途 |
|------|------|
| `scripts/deploy_sync.py` | **默认**：语法检查 → 打包 `app/` → 上传 → 容器内替换代码 → 重启 → 健康检查。**不重 build 镜像**。 |
| `scripts/deploy_rebuild.py` | 修改 **Dockerfile / docker-compose / 依赖** 后：打 tarball → 远端 `docker compose up -d --build`。 |
| `scripts/remote_shell.py` | SSH 执行一条远程命令（查日志、`docker ps` 等）。 |

在项目根目录执行，例如：

```bash
python scripts/deploy_sync.py
```

!!! note "部署 SSH"

    将 `deploy/deploy.env.template` 复制为仓库根目录 **`.deploy.env`**（已 `.gitignore`），填写 `DEPLOY_HOST` / `DEPLOY_USER` / `DEPLOY_PASSWORD`；或在 shell 中导出同名环境变量。勿将真实口令提交到 Git。

## Docker 与缓存

- 生产环境使用 volume 挂载 `./app:/code/app` 时，主机上可能残留 **`__pycache__`**，导致旧字节码与源码不一致。
- 部署脚本会在同步前清理容器内 `__pycache__`；本地亦建议开启 `PYTHONDONTWRITEBYTECODE=1`（见 Dockerfile / compose）。

## 健康检查

- `GET /health`：应用与 `memory_store` 类型等。
- 部署脚本可对仪表盘相关接口做冒烟检测（详见脚本内步骤）。

<a id="server-access"></a>

## 上线后访问（网页与 API）

默认 **`docker-compose.yml`** 把应用映射为 **宿主机 `8000` → 容器 `8000`**。

### 本仓库作者当前公网实例（已部署）

与根目录 **`DEPLOY_HOST=43.143.114.183`**、`docker compose` 端口 **`8000:8000`** 一致——即**已从公网可直接访问**，不是占位说明：

| 说明 | URL |
|------|-----|
| **入口（Personal Assistant Web 聊天）** | [http://43.143.114.183:8000](http://43.143.114.183:8000/) |
| 健康检查 | [http://43.143.114.183:8000/health](http://43.143.114.183:8000/health) |
| Web 聊天（静态路径） | [http://43.143.114.183:8000/static/index.html](http://43.143.114.183:8000/static/index.html) |
| Skills | [http://43.143.114.183:8000/static/skills.html](http://43.143.114.183:8000/static/skills.html) |
| Router | [http://43.143.114.183:8000/static/router.html](http://43.143.114.183:8000/static/router.html) |
| Companion | [http://43.143.114.183:8000/static/companion.html](http://43.143.114.183:8000/static/companion.html) |
| A 股 Dashboard | [http://43.143.114.183:8000/static/dashboards/dashboard.html](http://43.143.114.183:8000/static/dashboards/dashboard.html) |
| 股票预警 | [http://43.143.114.183:8000/static/dashboards/stock_alert.html](http://43.143.114.183:8000/static/dashboards/stock_alert.html) |

若你迁移服务器或改了 IP/域名，请同步改本节与 README 表里地址。

### 自行部署时使用（占位符）

把 **`http://<主机>:8000`** 换成你的 **公网 IP 或域名**：

| 说明 | URL（自行替换 `<主机>`） |
|------|--------------------------|
| 根路由 | `http://<主机>:8000/` |
| 健康检查 | `http://<主机>:8000/health` |
| Web 聊天 | `http://<主机>:8000/static/index.html` |
| Skills | `http://<主机>:8000/static/skills.html` |
| Router | `http://<主机>:8000/static/router.html` |
| Companion | `http://<主机>:8000/static/companion.html` |
| A 股 Dashboard | `http://<主机>:8000/static/dashboards/dashboard.html` |
| 股票预警 | `http://<主机>:8000/static/dashboards/stock_alert.html` |

!!! tip "与 `.deploy.env` 对齐"

    本地 **`DEPLOY_HOST`** 应与你在浏览器里用的 **`<主机>`** 一致（若前面有 HTTPS 反代或其它端口映射，则以实际对外 URL 为准）。

若在服务器前挂了 **Nginx / Caddy** 只做 `443 → 容器 8000`，则对外应为 `https://<域名>/...`，路径仍可沿用 **`/health`**、**/static/** 等。

可选 **Next.js 前端（`web/`）** 若单独进程监听（例如 `3000`），则地址为 **`http://<主机>:3000`**，与后端 **8000** 不同；本节默认描述的是 **`app`** 这一套静态页与 API。

## SearxNG

`docker-compose.yml` 若包含 SearxNG，实例配置目录见 `.gitignore` 中的 `config/searxng/`（按你本地生成策略处理）。
