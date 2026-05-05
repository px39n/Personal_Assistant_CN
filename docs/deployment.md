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

## SearxNG

`docker-compose.yml` 若包含 SearxNG，实例配置目录见 `.gitignore` 中的 `config/searxng/`（按你本地生成策略处理）。
