# GitHub Pages 部署说明

本项目用 **MkDocs Material** 构建静态文档，并由 **GitHub Actions**（`.github/workflows/deploy-docs.yml`）在推送后构建并上传到 GitHub Pages。

## 1. 打开 Pages

1. 打开仓库：**[Settings](https://github.com/px39n/Personal_Assistant_CN/settings)** → **Pages**
2. **Build and deployment** → **Source** 选择 **GitHub Actions**（不要选 branch / Deploy from branch）

保存后仓库才允许 `deploy-pages` 创建部署。

## 2. 私有仓库与免费账号的限制

在个人免费账号下，**私有仓库不能使用 GitHub Pages**（会看到部署 API 404 / `Creating Pages deployment failed`）。

你可任选其一：

- 把 **`Personal_Assistant_CN` 仓库改为 Public**，再开启上面第 1 步；或  
- 使用 **GitHub Pro**（及更高套餐）；或  
- 不把文档站上 GitHub：本地 `mkdocs build` 后把 `site/` 丢到任意静态托管（Cloudflare Pages、Vercel、对象存储等）。

## 3. 手动重跑部署

已开启 Pages 且仓库符合规则后：

- **Actions** → **Deploy docs to GitHub Pages** → 选最新一次 run → **Re-run all jobs**  
- 或在 **Actions** 里选该 workflow → **Run workflow**

若最近一次提交只改了应用代码或未命中 workflow 的路径，可能不会自动构建——用 **Run workflow** 即可补齐一次部署。

## 4. 文档站地址（项目站）

开启成功后一般为：

**https://px39n.github.io/Personal_Assistant_CN/**

（仓库名区分大小写时以 GitHub 显示为准。）
