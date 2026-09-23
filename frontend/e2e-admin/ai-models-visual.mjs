// admin 的 AI Models 页视觉探针 —— 手工跑，不在 CI 里。
//
// 为什么这个测 admin 的脚本住在 frontend/ 下：playwright 只装在 frontend，而
// Node 按**脚本自己所在的目录**解析依赖，放 admin/e2e/ 就 import 不到。给 admin
// 加 devDependency 也不行 —— 它的 Dockerfile 是多阶段（生产镜像只拿 dist/），
// 但 builder 阶段的 `npm ci` 会装 devDeps，playwright 的 postinstall 要拉一百多
// MB 浏览器，而构建机走 5G 计费网络（原因写在 admin/Dockerfile 的注释里）。
// 所以它跟 e2e/ 与 e2e-prod/ 并列，做第三个独立目录。
//
// 为什么存在：`admin/` 没有任何测试框架，也不在 `scripts/ci-changed-areas.sh`
// 的映射里，所以 `ci.yml` 根本不跑它；`deploy-gpu.yml` 只会 docker build 一下。
// 在这个脚本之前，改 admin 的验证止于 `tsc --noEmit` + `vite build` —— 两者都
// 不会打开页面。2026-09-22 第一次跑它就查出两个缺陷（侧栏还印着 MediaHub
// Admin、jimeng-local 两行的模型名渲染成一片空白）。
//
// 为什么不走登录：生产只有一个 admin 角色账号，那是真人用户的账号。给测试账号
// 发 admin 是往生产加一个常驻越权面，不值当。所以桩打在三条**真实边界**上：
//
//   1. localStorage 的 sb-<ref>-auth-token —— supabase-js 的 getSession() 读它
//   2. ${VITE_SUPABASE_URL}/rest/v1/user_profiles —— AuthProvider 的角色判定
//   3. ${VITE_API_URL}/api/v1/admin/nous-models{,/protocols} —— 页面数据
//
// ⚠️ fixture 必须是**真实 wire 形状**，不能手写（见 CLAUDE.md「边界 mock 必须
// 用真实 JSON 形状」）。取法是在生产后端里跑真正的序列化函数：
//
//   ssh gpupc 'docker exec -w /app nous-backend /app/.venv/bin/python - ' <<'PY'
//   import asyncio, json, sys
//   import app.api.admin.nous_model_router          # noqa
//   R = sys.modules["app.api.admin.nous_model_router"]
//   from app.services.ai.provider_protocols import all_protocols
//   from app.schemas.nous_model import ProviderProtocolItem, ProviderProtocolListResponse
//   async def main():
//       rows = await R.get_nous_model_repository().list_all()
//       priced = await R.load_priced_models()
//       models = [R._to_response(r, price_coverage=R.price_coverage_for(r, priced))
//                 .model_dump(mode="json") for r in rows]
//       protos = ProviderProtocolListResponse(protocols=[
//           ProviderProtocolItem(key=p.key, label=p.label, description=p.description,
//                                model_types=list(p.model_types), aliases=list(p.aliases),
//                                is_default=p.is_default, credential_kind=p.credential_kind)
//           for p in all_protocols()]).model_dump(mode="json")
//       print(json.dumps({"models": models, "protocols": protos}, ensure_ascii=False))
//   asyncio.run(main())
//   PY
//
//   注意 `sys.modules[...]` 不能换成 `from app.api.admin import nous_model_router`
//   —— 那个名字在包的 __init__ 里被重新绑成了 APIRouter 对象，拿不到模块。
//
// 跑法（playwright 装在 frontend/，admin 不引它以免进生产镜像）：
//
//   # 1) 起 dev server —— SUPABASE_URL 必须是 *.supabase.co 形状，
//   #    supabase-js 按其中的 project ref 派生 storageKey
//   cd admin && VITE_SUPABASE_URL=https://stubref.supabase.co \
//     VITE_SUPABASE_ANON_KEY=sb_publishable_stub VITE_API_URL=http://stub.local \
//     npm run dev -- --port 5199 --strictPort
//   # 2) 另开一个终端
//   cd frontend && ADMIN_FIXTURE=/tmp/admin_fixture.json node e2e-admin/ai-models-visual.mjs
//
// 输出：/tmp/admin-ai-models.png（整页）+ stdout 的页面纯文本。**要看图**，
// 文本看不出空白渲染、灰阶、徽章颜色这类问题。
import { chromium } from 'playwright';
import { readFileSync } from 'node:fs';

const FIXTURE = process.env.ADMIN_FIXTURE || '/tmp/admin_fixture.json';
const BASE = process.env.ADMIN_BASE || 'http://localhost:5199';
const OUT = process.env.ADMIN_SHOT || '/tmp/admin-ai-models.png';

const FIX = JSON.parse(readFileSync(FIXTURE, 'utf8'));

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1680, height: 1200 } });
const page = await ctx.newPage();

const errors = [];
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message));

const json = (body) => ({
  status: 200,
  contentType: 'application/json',
  headers: { 'access-control-allow-origin': '*' },
  body: JSON.stringify(body),
});

// ⚠️ Playwright 的 route 是**后注册的先匹配**。兜底必须先注册、具体的后注册，
// 否则 `**/rest/v1/**` 会把 user_profiles 盖成空数组 —— 角色判定拿不到 admin，
// 页面直接跳 /login，而且不报任何错。这个顺序踩过一次。
await page.route('**/auth/v1/**', (r) => r.fulfill(json({})));
await page.route('**/rest/v1/**', (r) => r.fulfill(json([])));
await page.route('**/rest/v1/user_profiles*', (r) =>
  r.fulfill(json([{ username: 'admin', avatar_url: null, role: 'admin' }])));
await page.route('**/api/v1/admin/nous-models', (r) => r.fulfill(json(FIX.models)));
await page.route('**/api/v1/admin/nous-models/protocols', (r) => r.fulfill(json(FIX.protocols)));

const expiresAt = Math.floor(Date.now() / 1000) + 86400;
await page.addInitScript((exp) => {
  const user = {
    id: '00000000-0000-4000-8000-000000000001',
    email: 'probe@local',
    aud: 'authenticated', role: 'authenticated',
    app_metadata: {}, user_metadata: {}, created_at: new Date().toISOString(),
  };
  const session = {
    access_token: 'stub-token', refresh_token: 'stub-refresh',
    token_type: 'bearer', expires_in: 86400, expires_at: exp, user,
  };
  // getSession() 这条主路径必须命中：AuthProvider 订阅 onAuthStateChange，
  // 初始事件带 null 会把 localStorage 回落路径刚设好的 user 清掉。
  localStorage.setItem('sb-stubref-auth-token', JSON.stringify(session));
}, expiresAt);

await page.goto(`${BASE}/ai`, { waitUntil: 'domcontentloaded' });
// AuthProvider 的 getSession() 最多等 2s 才回落，页面再拉两个接口
await page.waitForTimeout(6000);

const url = page.url();
if (url.endsWith('/login')) {
  console.error('FAILED: 停在 /login —— 角色桩没生效，先查 route 注册顺序与 storageKey');
  await browser.close();
  process.exit(1);
}

await page.screenshot({ path: OUT, fullPage: true });
console.log('screenshot:', OUT);
console.log('console errors:', errors.length);
errors.slice(0, 10).forEach((e) => console.log('  ', e.slice(0, 200)));
console.log('--- page text ---');
console.log(await page.evaluate(() => document.body.innerText));

await browser.close();
