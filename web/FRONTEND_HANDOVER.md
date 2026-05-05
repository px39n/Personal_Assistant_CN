# 前端交接文档 (给 Gemini 3.1 的开发指南)

你好，Gemini 3.1！我是 Cascade。我已经为这个项目（Personal Assistant CN）搭建了基础的前端架构和聊天功能。
现在由你接手继续完善 `/knowledge`, `/skills`, 和 `/memory` 等页面的具体设计与实现。

## 技术栈
- **框架**: Next.js 16 (App Router)
- **语言**: TypeScript
- **样式**: Tailwind CSS v4 + `shadcn/ui` (已配置暗色主题)
- **图标**: `lucide-react`
- **Markdown渲染**: `react-markdown` + `remark-gfm` + `@tailwindcss/typography`

## 目录结构 & 路由
- `src/app/layout.tsx` & `layout-client.tsx`: 根布局，包含左侧全局导航菜单 (`MainSidebar`)。
- `src/app/chat/page.tsx`: 核心对话界面（已完成 SSE 流式对接、Markdown 渲染、对话记录等）。
- `src/app/knowledge/page.tsx`: **[待开发]** 知识库 RAG 页面。
- `src/app/skills/page.tsx`: **[待开发]** 技能中心，展示后端加载的 Tool/Agent 能力。
- `src/app/memory/page.tsx`: **[待开发]** 个人偏好和全局对话记忆管理页面。

## 后端 API 接口 (已通过 Next.js rewrites 代理到 `/api/*`)
后端运行在 `127.0.0.1:8000`，Next.js 会自动把前端的 `/api/*` 请求转发过去。

1. **对话 API**
   - `POST /api/chat`: 发送消息，支持 `stream: true` (返回 SSE 流) 和 `stream: false`。
     - 请求体: `{ "message": "...", "user_id": "web_user", "conversation_id": "...", "stream": true }`
   - `GET /api/conversations/{conversation_id}/history`: 获取某个会话的历史消息记录。

2. **Skill API**
   - `GET /api/skills`: 获取当前系统加载的所有 Skill 列表。
     - 返回格式: `{ "count": 5, "skills": [ { "name": "...", "description": "...", "category": "...", "version": "...", "enabled": true } ] }`

3. **知识库 API (RAG)**
   - 后端已实现知识库向量检索，相关的上传、管理 API 端点需要在开发 `/knowledge` 页面时与后端同步定义（目前后端包含 `documents` 的上传与查询逻辑）。

4. **记忆 API**
   - 后端实现了三层记忆（全局、Session、Skill）。开发 `/memory` 页面时可新增 REST 端点来 CURD 全局记忆（通过 `memory_store` 暴露）。

## 开发建议
1. 使用 `shadcn/ui` 的组件来保持风格统一（如 `Card`, `Button`, `Table`, `Dialog` 等），你可以使用 `npx shadcn@latest add [component]` 安装新组件。
2. 尽量复用 `src/lib/types.ts` 中的 TS 接口库。
3. 如果需要增加后端接口，可以直接让用户切换回我（Cascade）来写 Python 代码，或者你也可以直接修改 `app/api/` 下的 FastAPI 路由。

祝你编码愉快！
