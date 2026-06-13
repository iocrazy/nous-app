# Storyboard Workbench Phase 5 — E2E 测试设计

日期：2026-06-13 ｜ 状态：已批准 ｜ 分支：`feature/storyboard-e2e`

## 背景与目标

Storyboard 分镜工作台 Phase 0–4.3（节点系统/图片管道/AI 生成/工具编辑器/全部面板）+ Phase 5.2（性能优化）均已 ship 到 master。Phase 5 唯一遗留 = **5.1 E2E 测试**。

现有 `frontend/e2e/storyboard.spec.ts`（21 个 Playwright 测试）是冒烟级，但弱点严重：
1. 无 auth env → 全 skip；
2. **靠"已存在项目"**：编辑器相关测试 `test.skip(!opened, 'No existing projects to open')`，没现成项目就跳过 —— 不自建数据；
3. **无完整关键路径**：缺 memory TODO 点名的"上传→生成→拆分→导出"全流程；
4. `playwright.config.ts` baseURL 是 stale 的 `localhost:3000`，无 `webServer`（不自动起 vite）；
5. E2E 未接 CI（manual-run only）。

目标：补齐确定性的关键路径 E2E + 干掉 skip 弱点 + 修 config，本轮做成可本地/手动稳定跑的套件。

## 已拍板决策

1. **生成确定性**：Playwright `page.route` stub —— 拦截 AI 生成/拆分的 FastAPI 端点返回固定 fixture。确定、亚秒、零成本、可进 CI。不测真模型（真模型质量不是 E2E 的责任）。
2. **后端边界**：真 dev 后端 + 测试账号。除 AI 生成/拆分两个端点 stub 外，登录/项目 CRUD/storyboard 持久化/上传全打真后端，测真集成。
3. **测试数据**：测试内自建项目（每个 flow 走 New Project 向导建 storyboard 项目，结束清理）。彻底消除 skip 弱点。
4. **CI**：本轮延后。webServer + 无 skip 的设计让后续接 CI 成小尾巴（加 workflow job + secrets）。

## Stub 边界（精确端点）

| 端点 | stub 返回 | 说明 |
|------|----------|------|
| `POST **/api/v1/storyboard/generate/image` | `{success:true, image_url:<fixture>}` | 用同步返回形式（带 image_url）绕开轮询 |
| `POST **/api/v1/storyboard/projects/*/split-image` | 固定 `SplitImageResult`（2–3 帧，指向 fixture 图） | `splitImage()` 的端点 |
| `GET **/api/v1/workflows/*/status` | `{status:'completed', ...}` | 兜底，万一生成走异步轮询路径 |

其余 `/api/v1/storyboard/*`、`/api/v1/projects/*`、auth、upload 全部**不拦截**，打真 dev 后端。

## 前置条件（写进 spec，不自动配置）

E2E 是真后端集成测试，需要：
- **可达后端**：`VITE_API_URL` 指向一个跑着的 FastAPI 后端。NAS 无独立 dev 后端（prod-only），所以本地跑法 = `uv run uvicorn app.main:app --port 8081` 连 dev Supabase（NAS `mediahub-sb-dev`，kong `192.168.50.9:9081`）。
- **匹配的 Supabase**：`VITE_SUPABASE_URL` 指向同一 Supabase（dev NAS kong 9081）。⚠️ worktree-manager 默认生成的 `.env.local` 指向本地 54321（local supabase CLI，**当前没在跑**）——E2E 前必须改成 dev NAS 或本地起的栈。
- **测试账号**：`E2E_EMAIL` / `E2E_PASSWORD` = 该 Supabase 上的一个测试账号（dev 库可安全 signup 建一个）。
- `npm run test:e2e` 由 playwright `webServer` 自动起前端（vite preview）；后端是上面的前置（文档说明，不自动起）。

## 设计

### 1. `playwright.config.ts` 修整

- baseURL：`process.env.BASE_URL || 'http://localhost:4173'`（vite preview 端口；去掉 stale 3000）。
- 加 `webServer`：
  ```ts
  webServer: {
    command: 'npm run build && npm run preview -- --port 4173 --strictPort',
    url: 'http://localhost:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  }
  ```
  `vite preview` 服务 `npm run build` 产出的 `dist/`，确定性强。若 `BASE_URL` 已指向一个跑着的环境，可 `reuseExistingServer` 跳过本地起栈（playwright 在 url 已响应时复用）。
- 其余（timeout/retries/chromium/screenshot/trace）保持。

### 2. Helpers（`frontend/e2e/helpers/`）—— 从内联抽出，聚焦可复用

- `auth.ts` — `loginViaUI(page)`：复用现有登录逻辑（AuthOverlay email+password，等 URL 离开 /login）。`hasAuth` 常量。
- `stubs.ts` — `installStoryboardStubs(page)`：装上表三个 `page.route` 拦截器。fixture 图 = `e2e/fixtures/frame.png`（小 base64 PNG，仓库内）。导出 `FIXTURE_IMAGE_URL`。
- `project.ts` — `createStoryboardProject(page, name): Promise<void>`（走 New Project 向导 → 等编辑器 Back 按钮可见）；`deleteProject(page, name)`（回 /projects → 删该项目，收尾用，失败不致命）。

### 3. `frontend/e2e/storyboard.spec.ts`（重写）

**A. public（无需 auth）** —— 保留：login 页渲染。

**B. authenticated（`test.skip(!hasAuth)`）**，`beforeEach` 登录 + `installStoryboardStubs`：

- **关键路径 `full creative flow`**（Phase 5 核心交付）：
  1. `createStoryboardProject` 建项目 → 进编辑器
  2. 加 Upload 节点，上传 `e2e/fixtures/frame.png`（真上传到 dev）→ 断言图节点出现
  3. 触发某 gen 节点的"生成"（stub 命中）→ 断言 fixture 图出现在节点
  4. 对图跑 split（stub 命中）→ 断言 timeline 出现帧
  5. 打开 Export → 断言导出 dialog/产物
  6. `afterEach` `deleteProject` 清理
- **面板冒烟（升级版）**：每个测试用 `createStoryboardProject` 自建项目（不再 `skip(!opened)`），覆盖：工具栏 5 按钮可见、Character 面板开关、Chat 面板开关、Timeline 开关、Export dialog 开关、Script import dialog 开关。共用 `beforeEach` 建项目 + `afterEach` 清理，避免每个测试重复建。

### 4. 确定性保证

- AI/split 全 stub → 无真模型/成本/flaky/亚秒。
- 自建数据 → 不依赖环境现状。
- 仅 `E2E_EMAIL/PASSWORD` 未设时优雅 skip（真后端 E2E 绕不开真登录）。

### 5. CI（本轮不做，留接口）

spec 记一笔：后续接 CI = `.github/workflows` 加一个 job（`npm ci` → playwright install → `npm run test:e2e`，配 `E2E_EMAIL/PASSWORD` secrets + `VITE_*` 指向 dev）。webServer 已就绪，无 skip，接入只差 secrets + runner。

## 不做（YAGNI）

- 真 AI 生成 / 真模型质量
- CI 集成（本轮）
- 视频生成（memory：映射到 exportImageNode，无独立节点）
- 跨浏览器（按现 config 只 chromium）

## 风险与边界

- **选择器脆弱性**：现有 spec 用 `.w-80.border-l`、`[class*="ProjectCard"]` 这类 class 选择器，岛式 UI 重构（进行中）可能改类名 → E2E 碎。缓解：关键交互优先用 `getByRole`/可见文本，必要处加 `data-testid`（最小侵入，只在选择器实在不稳处加）。
- **上传/导出真打后端**：dev 库会累积测试 resource/项目 → `afterEach` 删项目（resource 走 storyboard 项目 cascade 或单独清）。测试项目名带固定前缀 + 时间戳便于辨识/批量清。
- **stub 与真实响应漂移**：stub 的 `SplitImageResult`/generate 响应形状必须跟真端点一致，否则前端解析失败。实现时对真端点 schema 核对 stub 形状。
