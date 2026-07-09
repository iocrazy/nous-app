# 真机过场 / Real-Machine Walkthrough 方法

> 目的：在**真实浏览器**上验证已 ship 的 UI / 交互（单元测试测不出的视觉、手感、端到端闭环）。两条路线，按验什么选。

## 路线对比

| | 路线一 Playwright MCP | 路线二 Claude-in-Chrome 扩展 |
|--|--|--|
| 数据 | stub 假数据 / canary 真库 | 真 prod + 真数据 |
| 登录 | e2e stub 免登录 或 canary 账号 | 用户已登录的会话 |
| Agent 能否自动 | ✅ 能 | ❌ 需用户先连扩展 |
| 留下回归 | ✅ 可写成永久 e2e | ❌ 一次性 |
| 适合 | 交互/逻辑正确性、纯前端 | 线上部署后的视觉/真机手感终验 |

**默认走路线一**（Agent 自动、可留回归）；线上真机视觉终验才用路线二。

---

## 路线一：Playwright MCP（推荐，Agent 自动）

工具：`mcp__playwright__browser_*`。驱动 **Playwright 自己控制的 Chromium**（与用户本地 Chrome 无关）。

### 1a. e2e stub 免登录（最快，测纯前端交互）
- 基建：`frontend/e2e/helpers/stubs.ts` 用 `page.addInitScript` 往 localStorage 塞**假 Supabase session**（key 形如 `sb-e2e-auth-token`）+ `page.route` 拦截所有 supabase/API 请求返回假数据 → **免真登录**。
- `frontend/playwright.config.ts` 的 `webServer` 会自动 `npm run build && preview`（port 4173，pinned `VITE_SUPABASE_URL=https://e2e.supabase.co` 等 e2e env）。
- 适合**不依赖真后端**的前端交互（画布拖拽/框选/撤销重做/复制粘贴/连接校验都是纯客户端 store + ReactFlow）。
- 写成 `frontend/e2e/<feature>.spec.ts` = **永久 CI 回归**（现有例子 `e2e/storyboard.spec.ts`）。

### 1b. canary 账号 + 真库（测端到端含真后端）
- **建号**：走 signup（API 或 UI），命名 `<feature>.<purpose>.<timestamp>`（如 `qa_canvasfx_<ts>@example.com` / `smoke.inspiration.p1.<ts>@heygo.cn`），**密码自己生成并记在本次任务临时笔记里**（勿提交、勿写 memory）。
- **prod signup 后 session=null**（要邮箱确认）→ SSH 进 db 容器手动确认：
  ```sql
  UPDATE auth.users SET email_confirmed_at = now() WHERE email = '<canary>';
  ```
- **登录**：Playwright `browser_navigate` 到登录页 → `browser_fill_form` / `browser_type` 填 email+密码 → 提交 → 跑过场。
- **用完删号**（见「清理」）。

### 工具序列（典型一次过场）
1. `mcp__playwright__browser_navigate(url)` — 打开页
2. `mcp__playwright__browser_snapshot()` — 拿可访问性树定位元素（比截图省 token）
3. `mcp__playwright__browser_click / browser_type / browser_fill_form / browser_drag` — 交互
4. `mcp__playwright__browser_take_screenshot()` — 视觉留证
5. `mcp__playwright__browser_run_code_unsafe(js)` — 页内注入脚本（高级：读 store 状态、抓 WebSocket 帧、验 realtime）

### realtime / 协同验证技巧
验 P5 协同那类实时：`browser_run_code_unsafe` + `addInitScript` 注入 **WS 帧监听**，抓 `phx_join` / 消息帧；服务端佐证查 Supabase `realtime.subscription` 表有对应订阅行。

---

## 路线二：Claude-in-Chrome 扩展（线上真机终验）

工具：`mcp__claude-in-chrome__*`（若 deferred 先 ToolSearch 加载核心集）。驱动用户**真实登录的 prod Chrome 会话**。

- **前置**：用户装/开 https://claude.ai/chrome（同一 claude.ai 账号，首次可能要重启 Chrome）。未连时 `tabs_context_mcp` 报「extension not connected」。
- 流程：`tabs_context_mcp`（先拿 tab）→ `tabs_create_mcp` 建新 tab → `navigate` → `computer` / `read_page` 交互。
- 适合**线上部署后**的真机视觉 / 真数据 / 手感终验。

---

## 清理（重要）
- 真机验证建的 canary/smoke 测试账号**用完即删**（admin 用户面板 `192.168.50.9:3097` 或 API），连带其空 team / 画布 / 笔记。
- 命名规则 `<feature>.<purpose>.<timestamp>` 便于日后识别遗留（例：`qa_canvasfx_*`=画布协同 canary，`smoke.inspiration.p1.*`=灵感库冒烟）。
- **别把 canary 密码写进 memory 或提交**。

## 已知坑
- **prod signup 邮箱未确认 → session=null**：db 手动 `email_confirmed_at=now()`。
- **PWA SW 缓存旧 bundle**：视觉过场前先 unregister service worker + `caches.delete` + reload，否则看到的是旧版。
- **Playwright webServer 首次 build 慢**（分钟级），耐心等 preview 起来。
- 路线一是 preview 构建 + stub/canary，**不等于线上真机**；线上视觉/真数据终验仍需路线二。
