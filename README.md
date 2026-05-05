# Personal Assistant CN

Private assistant: FastAPI, skills, Feishu/WeCom, A 股 data (Tushare / East Money via proxy).

## Deploy scripts (`scripts/`)

| Script | When to use |
|--------|-------------|
| `python scripts/deploy_sync.py` | **Default.** Sync `app/` into the running container, restart, health-check. No Docker image rebuild. |
| `python scripts/deploy_rebuild.py` | Dockerfile / `docker-compose.yml` / deps changed — full `docker compose up -d --build` on the server. |
| `python scripts/remote_shell.py "cmd"` | SSH one-off (logs, `docker ps`, etc.). |

**SSH:** copy `deploy/deploy.env.template` to **`.deploy.env`** in the repo root (gitignored), set `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_PASSWORD`, or export the same variables. Never commit `.deploy.env`.

## Frontend (`web/`)

Next.js app. **`web/package-lock.json`** is the npm lockfile: it pins exact dependency versions so `npm ci` is reproducible. **Commit it** — do not delete or gitignore unless you use another package manager intentionally.

## Edge proxy (`cf-em-proxy/`)

See `cf-em-proxy/README.md` — Cloudflare Worker / Deno Deploy relay for East Money APIs (`EM_PROXY_URL`).

## Documentation site (MkDocs)

```bash
pip install -e ".[docs]"
mkdocs serve
```

Source Markdown lives in **`docs/`**, configuration in **`mkdocs.yml`**. Build static files: `mkdocs build` → output directory **`site/`** (gitignored).

### GitHub Pages（线上文档）

1. 仓库 **Settings → Pages → Source** 选 **GitHub Actions**。
2. 推送 `docs/`、`mkdocs.yml` 或 workflow 会触发构建；也可在 **Actions** 里手动 **Run workflow**。
3. 线上地址一般为 **`https://px39n.github.io/Personal_Assistant_CN/`**（以 GitHub 实际域名为准）。

**注意：** 使用 **GitHub 免费个人账号** 时，**私有仓库无法使用 GitHub Pages**。若部署报 `Creating Pages deployment failed` / 404，请把仓库改为 **Public** 或升级套餐；说明见 **`docs/github-pages.md`**。
