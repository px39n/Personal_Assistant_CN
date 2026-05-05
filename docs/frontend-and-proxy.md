# 前端与边缘代理

## 静态管理界面（`app/static/`）

内置 HTML 页面：聊天、Skills、Dashboard、Router、伴侣设置等，由 FastAPI 挂载静态资源访问，**无单独构建步骤**（与 `web/` 分离）。

## Next.js 前端（`web/`）

独立前端工程，使用 **npm** / **Node**：

```bash
cd web
npm ci
npm run dev
```

### `package-lock.json` 是什么？

npm 的**依赖锁定文件**，记录依赖树的精确版本，保证 `npm ci` 可复现安装。

- **建议提交到 Git**，不要将 `package-lock.json` 加入 `.gitignore`（除非你刻意改用 pnpm/yarn 并采用相应 lockfile）。

## 东方财富边缘代理（`cf-em-proxy/`）

云服务器访问东方财富 `push2` 接口时，常遇到阻断或非标准 JSON 响应。通过 **`EM_PROXY_URL`** 配置边缘 Worker（Cloudflare / Deno Deploy 等）转发请求。

详见 **`cf-em-proxy/README.md`**。
