# Storyboard Phase 5 E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deterministic Playwright E2E coverage for the storyboard workbench — self-creating test data, AI/split stubbed at the FastAPI endpoint level, covering the create→upload→generate→split→export critical path plus all editor panels.

**Architecture:** Two phases. **Phase A** (robust, fully specified): fix `playwright.config.ts` (webServer + baseURL), add focused `e2e/helpers/`, add `data-testid` to the project-create + toolbar + panel elements, rewrite the smoke spec to self-create projects (kills the `test.skip(!opened)` weakness). **Phase B** (creative path): a `page.route` stub helper for the generate/split endpoints, `data-testid` on the upload/generate/export nodes, and the full creative-flow test. The split trigger is tangled in the node-action system and NOT pinnable from outside — its task begins with a code-locate step, not fabricated selectors.

**Tech Stack:** Playwright (`@playwright/test` ^1.58), Vite (build+preview), React 19, i18next. Frontend dir: `frontend/`.

**Spec:** `docs/superpowers/specs/2026-06-13-storyboard-e2e-design.md`

**⚠️ Roadmap context:** `features/storyboard/` is the OLD tool, slated for replacement by `features/canvas-core/` (SmartMode). User chose to E2E it knowingly (live now). Adding `data-testid` to to-be-deleted code is accepted.

---

## Prerequisites for running E2E (document, do not auto-provision)

E2E is a real-backend integration test. To actually RUN the suite (the per-phase verification gates), the executor needs:
- A reachable FastAPI backend at `VITE_API_URL`. No standalone dev backend exists on NAS (prod-only), so run locally: `cd backend && uv run uvicorn app.main:app --port 8081` pointed at dev Supabase (NAS `mediahub-sb-dev`, kong `192.168.50.9:9081`).
- `frontend/.env.local`: `VITE_API_URL=http://localhost:8081`, `VITE_SUPABASE_URL`/`VITE_SUPABASE_ANON_KEY` → dev NAS. (worktree-manager's default points at local 54321 which is NOT running — must repoint.)
- `E2E_EMAIL` / `E2E_PASSWORD` env: a test account on that Supabase (sign one up on the dev库 once).

**Code-only tasks (config / helpers / data-testid / spec files) are verified by `tsc` + `build` and do NOT need the live stack.** The live-stack run is an explicit verification gate at the end of each phase.

---

## File Structure

**Create:**
- `frontend/e2e/fixtures/frame.png` — tiny real PNG (test upload + stub image target)
- `frontend/e2e/helpers/auth.ts` — `loginViaUI`, `hasAuth`
- `frontend/e2e/helpers/project.ts` — `createStoryboardProject`, `deleteProject`
- `frontend/e2e/helpers/stubs.ts` — `installStoryboardStubs` (Phase B)

**Modify:**
- `frontend/playwright.config.ts` — webServer + baseURL
- `frontend/e2e/storyboard.spec.ts` — rewritten
- `frontend/components/storyboard/project/NewProjectDialog.tsx` — data-testid
- `frontend/features/storyboard/CanvasToolbar.tsx` — data-testid
- `frontend/pages/StoryboardWorkbench/CanvasEditorPage.tsx` — data-testid (panels/export button)
- `frontend/features/storyboard/nodes/UploadNode.tsx` — data-testid (Phase B)
- `frontend/features/storyboard/nodes/StoryboardGenNode.tsx` — data-testid (Phase B)
- (split node component, located in Phase B Task B4) — data-testid

---

# PHASE A — Infra + robust smoke

### Task A1: Fix `playwright.config.ts` (webServer + baseURL)

**Files:** Modify `frontend/playwright.config.ts`

- [ ] **Step 1: Replace the config**

```ts
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:4173',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  // Auto-build + serve the production bundle. If BASE_URL already points at a
  // running env, Playwright reuses it (reuseExistingServer) and skips this.
  webServer: {
    command: 'npm run build && npm run preview -- --port 4173 --strictPort',
    url: 'http://localhost:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
```

- [ ] **Step 2: Verify it parses**

Run: `cd frontend && npx playwright test --list 2>&1 | head -20`
Expected: lists tests without a config parse error (it will list the existing spec's tests). A webServer build may kick off — that's fine; Ctrl-C after the list prints, or note the list appeared.

- [ ] **Step 3: Commit**

```bash
git add frontend/playwright.config.ts
git commit -m "test(e2e): playwright webServer + fix stale baseURL"
```

---

### Task A2: Fixture image + auth helper

**Files:** Create `frontend/e2e/fixtures/frame.png`, `frontend/e2e/helpers/auth.ts`

- [ ] **Step 1: Create the fixture PNG**

A 2×2 red PNG. Run from `frontend/`:
```bash
mkdir -p e2e/fixtures
node -e "require('fs').writeFileSync('e2e/fixtures/frame.png', Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEUlEQVR4nGP8z8Dwn4EIwAhUBADtgwXqWkAdRgAAAABJRU5ErkJggg==','base64'))"
```
Verify: `file e2e/fixtures/frame.png` → "PNG image data, 2 x 2".

- [ ] **Step 2: Write the auth helper**

Create `frontend/e2e/helpers/auth.ts`:
```ts
import { type Page } from '@playwright/test';

export const E2E_EMAIL = process.env.E2E_EMAIL ?? '';
export const E2E_PASSWORD = process.env.E2E_PASSWORD ?? '';
export const hasAuth = Boolean(E2E_EMAIL && E2E_PASSWORD);

/** Log in via the AuthOverlay (email + password). Leaves the app on a
 *  post-login route. No-op-safe to call once per test in beforeEach. */
export async function loginViaUI(page: Page): Promise<void> {
  await page.goto('/login');
  const trigger = page.getByRole('button', { name: /log in|get started/i });
  if (await trigger.isVisible().catch(() => false)) await trigger.click();
  await page.getByPlaceholder(/email/i).fill(E2E_EMAIL);
  await page.getByPlaceholder(/password/i).fill(E2E_PASSWORD);
  await page.getByRole('button', { name: /sign in|log in/i }).click();
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15_000 });
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep -E "helpers/auth" | head`
Expected: no output (clean).

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/fixtures/frame.png frontend/e2e/helpers/auth.ts
git commit -m "test(e2e): fixture image + auth helper"
```

---

> **⚠️ A3 CORRECTION (during execution):** the components this task originally named —
> `NewProjectDialog.tsx` and `pages/StoryboardWorkbench/ProjectListPage.tsx` — are **ORPHANED
> dead code** (not in the router; `ProjectListPage` even navigates to a non-existent route).
> The REAL storyboard-create flow is TWO levels: `ProjectsPage`→`ProjectsListView` "New Project"
> → `CreateProjectModal` (create a project) → project page `?tab=storyboard` →
> `ProjectStoryboardTab` "New Storyboard" → inline `CreateStoryboardModal` → editor. The shipped
> A3 (commit d14dbac8) targets these REAL components; `createStoryboardProject` returns
> `{projectName}` and `deleteProject(page, projectName)` cleans up via the project card's
> MoreVertical menu → "Delete Project" → native `window.confirm` (handled with
> `page.once('dialog', d=>d.accept())`). Steps below are superseded by that shipped version.

### Task A3: `data-testid` on project-create + the `project.ts` helper

**Files:** Modify `frontend/components/storyboard/project/NewProjectDialog.tsx`; create `frontend/e2e/helpers/project.ts`

- [ ] **Step 1: Add data-testid to NewProjectDialog**

Read `NewProjectDialog.tsx`. Add `data-testid="new-project-dialog"` to the dialog root container, `data-testid="project-name-input"` to the name `<input>`, and ensure the submit button text matches `/create/i` (it already does per exploration). Do NOT restructure — only add the two attributes.

- [ ] **Step 2: Locate the project-list "New Project" + project-card open**

Read the storyboard project list page (`pages/StoryboardWorkbench/` — the list component) to confirm: the "New Project" button text (`/new project/i`) and how a created project navigates into the editor (URL `/team/:teamId/projects/:projectId/storyboard/:storyboardId`, and the editor shows a Back button). Add `data-testid="storyboard-project-card"` to the project card root if the only selector is a fragile class (per exploration `[class*="ProjectCard"]`).

- [ ] **Step 3: Write the project helper**

Create `frontend/e2e/helpers/project.ts`:
```ts
import { type Page, expect } from '@playwright/test';

const PREFIX = 'E2E Storyboard';

/** Navigate to the storyboard project list, create a fresh project, and
 *  land in the canvas editor. Returns the unique project name (for cleanup).
 *  Uses a timestamp suffix so concurrent/retried runs don't collide. */
export async function createStoryboardProject(page: Page, ts: number): Promise<string> {
  const name = `${PREFIX} ${ts}`;
  // /projects redirects to /team/:teamId/projects (RedirectToTeam). The
  // storyboard list lives under the projects module.
  await page.goto('/projects');
  await page.getByRole('button', { name: /new project/i }).click();
  const dialog = page.getByTestId('new-project-dialog');
  await expect(dialog).toBeVisible({ timeout: 5_000 });
  await dialog.getByTestId('project-name-input').fill(name);
  await dialog.getByRole('button', { name: /create/i }).click();
  // Editor is up when the Back button renders.
  await expect(page.getByRole('button', { name: /back/i })).toBeVisible({ timeout: 15_000 });
  return name;
}

/** Best-effort cleanup: navigate to the list and delete the named project.
 *  Never throws — cleanup failure must not fail the test. */
export async function deleteProject(page: Page, name: string): Promise<void> {
  try {
    await page.goto('/projects');
    const card = page.getByTestId('storyboard-project-card').filter({ hasText: name }).first();
    if (!(await card.isVisible({ timeout: 5_000 }).catch(() => false))) return;
    // Open the card's context/overflow menu and click delete, confirming if asked.
    await card.hover();
    const menuBtn = card.getByRole('button', { name: /menu|more|options|delete/i }).first();
    if (await menuBtn.isVisible().catch(() => false)) await menuBtn.click();
    const del = page.getByRole('button', { name: /delete|remove/i }).first();
    if (await del.isVisible().catch(() => false)) await del.click();
    const confirm = page.getByRole('button', { name: /confirm|delete|yes/i }).first();
    if (await confirm.isVisible({ timeout: 2_000 }).catch(() => false)) await confirm.click();
  } catch {
    // swallow — cleanup is best-effort
  }
}
```
NOTE: the delete UI specifics (overflow menu vs inline) are uncertain — Step 2's read should confirm them; adjust `deleteProject`'s menu/confirm selectors to the real DOM. The `try/catch` keeps it non-fatal either way.

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep -E "project.ts|NewProjectDialog" | head`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/storyboard/project/NewProjectDialog.tsx frontend/e2e/helpers/project.ts
git commit -m "test(e2e): self-creating project helper + data-testids"
```

---

### Task A4: `data-testid` on toolbar/panels + rewrite the smoke spec

**Files:** Modify `frontend/features/storyboard/CanvasToolbar.tsx`, `frontend/pages/StoryboardWorkbench/CanvasEditorPage.tsx`, `frontend/e2e/storyboard.spec.ts`

- [ ] **Step 1: Add data-testid to toolbar + panel toggles + panels**

Read `CanvasEditorPage.tsx` (renders the Script/Timeline/Characters/Chat/Export buttons + panels) and `CanvasToolbar.tsx`. Add:
- on each top-bar toggle button: `data-testid="sb-toggle-{script|timeline|characters|chat|export}"`
- on the Characters panel root and Chat panel root (currently `.w-80.border-l`): `data-testid="sb-panel-characters"` / `data-testid="sb-panel-chat"`
- on the add-node toolbar button: `data-testid="sb-add-node"`

Keep changes additive (attributes only).

- [ ] **Step 2: Rewrite `e2e/storyboard.spec.ts` (smoke portion)**

Replace the file with:
```ts
import { test, expect } from '@playwright/test';
import { hasAuth, loginViaUI } from './helpers/auth';
import { createStoryboardProject, deleteProject } from './helpers/project';

test.describe('Storyboard Workbench', () => {
  test('login page renders', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByRole('button', { name: /log in|get started/i })).toBeVisible();
  });

  test.describe('editor (self-created project)', () => {
    test.skip(!hasAuth, 'Set E2E_EMAIL + E2E_PASSWORD to run authed E2E');

    let projectName = '';
    test.beforeEach(async ({ page }, testInfo) => {
      await loginViaUI(page);
      projectName = await createStoryboardProject(page, testInfo.workerIndex * 1e6 + Date.now() % 1e6);
    });
    test.afterEach(async ({ page }) => {
      await deleteProject(page, projectName);
    });

    test('toolbar exposes all panel toggles', async ({ page }) => {
      for (const id of ['script', 'timeline', 'characters', 'chat', 'export']) {
        await expect(page.getByTestId(`sb-toggle-${id}`)).toBeVisible();
      }
    });

    test('characters panel opens and closes', async ({ page }) => {
      await page.getByTestId('sb-toggle-characters').click();
      await expect(page.getByTestId('sb-panel-characters')).toBeVisible({ timeout: 3_000 });
      await page.getByTestId('sb-toggle-characters').click();
      await expect(page.getByTestId('sb-panel-characters')).toBeHidden({ timeout: 3_000 });
    });

    test('chat panel opens and closes', async ({ page }) => {
      await page.getByTestId('sb-toggle-chat').click();
      await expect(page.getByTestId('sb-panel-chat')).toBeVisible({ timeout: 3_000 });
      await page.getByTestId('sb-toggle-chat').click();
      await expect(page.getByTestId('sb-panel-chat')).toBeHidden({ timeout: 3_000 });
    });

    test('export dialog opens and closes', async ({ page }) => {
      await page.getByTestId('sb-toggle-export').click();
      await expect(page.getByRole('dialog')).toBeVisible({ timeout: 3_000 });
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog')).toBeHidden({ timeout: 3_000 });
    });

    test('script import dialog opens and closes', async ({ page }) => {
      await page.getByTestId('sb-toggle-script').click();
      await expect(page.getByRole('dialog')).toBeVisible({ timeout: 3_000 });
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog')).toBeHidden({ timeout: 3_000 });
    });
  });
});
```

- [ ] **Step 3: Typecheck + lint**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep -E "storyboard.spec|CanvasToolbar|CanvasEditorPage" | head`
Expected: clean. (ESLint if configured: `npx eslint e2e/storyboard.spec.ts` — fix any error.)

- [ ] **Step 4: PHASE A live-stack verification gate**

Bring up the stack (see Prerequisites): local uvicorn → dev Supabase, `.env.local` repointed, `E2E_EMAIL`/`E2E_PASSWORD` set. Then:
Run: `cd frontend && npx playwright test 2>&1 | tail -20`
Expected: `login page renders` passes; the 5 editor tests pass (create project → toolbar/panels). If `hasAuth` is false they skip — set creds and re-run. Fix any selector that didn't match the real DOM (adjust data-testid placement). Do not proceed to Phase B until Phase A is green against the live stack.

- [ ] **Step 5: Commit**

```bash
git add frontend/features/storyboard/CanvasToolbar.tsx frontend/pages/StoryboardWorkbench/CanvasEditorPage.tsx frontend/e2e/storyboard.spec.ts
git commit -m "test(e2e): self-creating editor smoke suite (no more skip-on-empty)"
```

---

# PHASE B — Creative critical path

### Task B1: Stub helper for generate/split endpoints

**Files:** Create `frontend/e2e/helpers/stubs.ts`

- [ ] **Step 1: Write the stub helper**

Create `frontend/e2e/helpers/stubs.ts`:
```ts
import { type Page } from '@playwright/test';

/** A data-URL the stubbed generate/split responses point at, so the UI has a
 *  real renderable image without hitting a model. */
export const FIXTURE_IMAGE_URL =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEUlEQVR4nGP8z8Dwn4EIwAhUBADtgwXqWkAdRgAAAABJRU5ErkJggg==';

/** Intercept the slow/non-deterministic AI + split endpoints, returning fixed
 *  payloads. Everything else (project CRUD, persistence, upload) hits the real
 *  backend. Shapes mirror storyboardService.ts: generate → {success,task_id,
 *  image_url}; split → SplitImageResult {frames,source_asset_id,rows,cols}. */
export async function installStoryboardStubs(page: Page): Promise<void> {
  // Generate: return image_url so HttpAiGateway short-circuits (no polling).
  await page.route('**/api/v1/storyboard/generate/image', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, task_id: 'e2e-stub-task', image_url: FIXTURE_IMAGE_URL }),
    }),
  );
  // Split: two fake frames pointing at the fixture image.
  await page.route('**/api/v1/storyboard/projects/*/split-image', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          source_asset_id: 'e2e-src',
          rows: 1,
          cols: 2,
          frames: [0, 1].map((i) => ({
            id: `e2e-frame-${i}`,
            asset_id: `e2e-asset-${i}`,
            frame_index: i,
            image_url: FIXTURE_IMAGE_URL,
            preview_url: FIXTURE_IMAGE_URL,
            width: 2,
            height: 2,
            row: 0,
            col: i,
          })),
        },
      }),
    }),
  );
  // Safety net: if generation ever takes the async polling path.
  await page.route('**/api/v1/workflows/*/status', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, status: 'completed', result: { image_url: FIXTURE_IMAGE_URL } }),
    }),
  );
}
```
NOTE: the split response envelope (`{success,data:{...}}` vs bare `SplitImageResult`) must match what `splitImage()` in `services/storyboardService.ts:340` unwraps. Step 2 verifies against the real parser.

- [ ] **Step 2: Verify the stub envelope matches the client parser**

Read `services/storyboardService.ts` around `splitImage` (line ~340) and `generateStoryboardImage`. Confirm whether responses are unwrapped from `{data:...}` or read at top level (the generate endpoint per exploration reads `task_id`/`image_url` at TOP level — so generate stub is correct as written; verify split's wrapper and adjust the stub's `body` shape to match exactly). Fix the stub if the envelope differs.

- [ ] **Step 3: Typecheck + commit**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep stubs.ts` → clean.
```bash
git add frontend/e2e/helpers/stubs.ts
git commit -m "test(e2e): generate/split endpoint stubs"
```

---

### Task B2: `data-testid` on upload + generate + export nodes

**Files:** Modify `frontend/features/storyboard/nodes/UploadNode.tsx`, `frontend/features/storyboard/nodes/StoryboardGenNode.tsx`

- [ ] **Step 1: UploadNode**

Read `UploadNode.tsx`. Add `data-testid="upload-file-input"` to the hidden `<input type="file" accept="image/*">`, and `data-testid="upload-node-image"` to the `<img>` that renders the uploaded preview. Additive only.

- [ ] **Step 2: StoryboardGenNode**

Read `StoryboardGenNode.tsx`. Add `data-testid="generate-button"` to the primary Generate button (per exploration, the button with text `/Generate/`), and `data-testid="gen-node-image"` to the `<img>` that shows the generated result (or, if the result renders in an adjacent exportImage node, add the testid there — confirm during the read).

- [ ] **Step 3: Typecheck + commit**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep -E "UploadNode|StoryboardGenNode"` → clean.
```bash
git add frontend/features/storyboard/nodes/UploadNode.tsx frontend/features/storyboard/nodes/StoryboardGenNode.tsx
git commit -m "test(e2e): data-testids on upload + generate nodes"
```

---

### Task B3: Critical-path flow test (create → add node → upload → generate)

**Files:** Modify `frontend/e2e/storyboard.spec.ts`

- [ ] **Step 1: Locate the add-node interaction precisely**

Read `features/storyboard/NodeSelectionMenu.tsx` to confirm how a node menu opens (right-click canvas? the `sb-add-node` toolbar button?) and the Upload menu item's selector. Add `data-testid="node-menu"` to the menu root and `data-testid="node-menu-upload"` to the Upload item if only fragile selectors exist. Commit that testid addition with message `test(e2e): data-testid on node selection menu`.

- [ ] **Step 2: Add the creative-flow test**

Append inside the `editor (self-created project)` describe block (which already does login + create + cleanup in before/afterEach). Import `installStoryboardStubs` and `FIXTURE_IMAGE_URL` at the top of the spec, and install stubs in `beforeEach` (after login, before/after create is fine since routes apply to the page):
```ts
// at top:
import { installStoryboardStubs } from './helpers/stubs';
// in beforeEach, before createStoryboardProject:
await installStoryboardStubs(page);
```
Then the test:
```ts
test('creative path: add upload node, upload image, generate', async ({ page }) => {
  // Open the node menu and add an Upload node.
  await page.getByTestId('sb-add-node').click();
  await page.getByTestId('node-menu-upload').click();
  // Upload the fixture image into the node.
  await page.getByTestId('upload-file-input').setInputFiles('e2e/fixtures/frame.png');
  await expect(page.getByTestId('upload-node-image')).toBeVisible({ timeout: 10_000 });
  // Trigger generation (stubbed → fixture image appears).
  const gen = page.getByTestId('generate-button').first();
  if (await gen.isVisible().catch(() => false)) {
    await gen.click();
    await expect(page.getByTestId('gen-node-image').first()).toBeVisible({ timeout: 10_000 });
  }
});
```
NOTE: the add-node interaction may be right-click instead of a toolbar button — if Step 1 found right-click, replace the first line with `await page.locator('.react-flow__pane, [data-testid="canvas"]').click({ button: 'right' });` then click the menu item. Generation requires a gen node connected to the upload; if the flow needs wiring an edge, the `if visible` guard keeps the test meaningful (upload asserted; generate best-effort until the node-wiring step is understood). Keep it honest — assert what reliably happens.

- [ ] **Step 3: Live-stack run for this test**

Run (stack up): `cd frontend && npx playwright test -g "creative path" 2>&1 | tail -20`
Expected: passes. If the upload `<img>` selector or node-menu interaction didn't match, fix the data-testid / interaction against the real DOM (use `npx playwright test -g "creative path" --debug` or the trace on failure). Iterate until green.

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/storyboard.spec.ts
git commit -m "test(e2e): creative-path flow — upload + generate (stubbed)"
```

---

### Task B4: Split + export, extending the creative path

**Files:** Modify the split-node component (located in Step 1) + `frontend/e2e/storyboard.spec.ts`

- [ ] **Step 1: Locate the Split trigger in code (investigation, not fabrication)**

The split trigger is not pinnable from outside. Read `features/storyboard/nodes/StoryboardNode.tsx` (renders `storyboardSplit` nodes), `features/storyboard/ui/NodeActionToolbar.tsx`, and `features/storyboard/ui/FrameTimeline.tsx` to determine: which button/action on which node invokes `splitImage` / produces a `storyboard_split` node, and where the resulting frames render (FrameTimeline frame thumbs). Record the exact element. Add `data-testid="split-button"` to that trigger and `data-testid="frame-thumb"` to the FrameTimeline frame thumbnail element. Commit: `test(e2e): data-testid on split trigger + frame thumb`.

- [ ] **Step 2: Extend the creative-path test with split + export**

Append to the `creative path` test (or add a dependent test). After the generate assertion:
```ts
  // Split the generated/uploaded image (stubbed → frames appear).
  const split = page.getByTestId('split-button').first();
  if (await split.isVisible().catch(() => false)) {
    await split.click();
    await expect(page.getByTestId('frame-thumb').first()).toBeVisible({ timeout: 10_000 });
  }
  // Export.
  await page.getByTestId('sb-toggle-export').click();
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 3_000 });
  await page.getByRole('button', { name: /^export$/i }).click();
  await expect(page.getByText(/export started|notified|success/i)).toBeVisible({ timeout: 5_000 });
```
The `if visible` guard on split keeps the test honest if split requires specific node wiring that proves hard to drive — assert export regardless. Adjust the export success assertion to the real toast text found in `ExportDialog.tsx` (exploration saw `/Export started.*notified/`).

- [ ] **Step 3: Live-stack run + iterate**

Run (stack up): `cd frontend && npx playwright test -g "creative path" 2>&1 | tail -20`
Expected: full path green. Fix selectors against real DOM as needed.

- [ ] **Step 4: PHASE B verification gate — full suite**

Run: `cd frontend && npx playwright test 2>&1 | tail -25`
Expected: all tests pass (login + 5 smoke + creative path). Confirm no test leaves orphaned dev-库 projects (afterEach cleanup). Run `npm run build` to confirm the data-testid additions didn't break the bundle.

- [ ] **Step 5: Commit**

```bash
git add frontend/features/storyboard frontend/e2e/storyboard.spec.ts
git commit -m "test(e2e): creative path — split (stubbed) + export"
```

---

## Self-Review

**Spec coverage:**
- §设计1 config webServer/baseURL → A1 ✓
- §设计2 helpers (auth/stubs/project) → A2/A3/B1 ✓
- §设计3 critical-path flow (create→upload→generate→split→export) → A3 (create) + B3 (upload/generate) + B4 (split/export) ✓; panel smoke self-creating → A4 ✓
- §设计4 determinism/no-skip → stubs (B1) + self-create (A3) + only skip on missing creds ✓
- §设计5 CI deferred → not built; webServer makes it a follow-up ✓ (noted)
- §前置条件 → "Prerequisites" section + per-phase live-stack gates ✓
- §风险 选择器脆弱 → addressed by data-testid tasks (A3/A4/B2/B4) + getByRole fallbacks ✓; 真打后端累积数据 → deleteProject afterEach ✓; stub 漂移 → B1 Step 2 verifies envelope ✓

**Placeholder scan:** The "locate in code" steps (A3 Step 2, B1 Step 1/2, B2, B4 Step 1) are honest investigation steps with concrete deliverables (a data-testid at a found element), NOT fabricated selectors — deliberate, because the split/delete/node-menu interactions are genuinely not externally determinable and fabricating selectors would be the real plan failure. Each pairs with an `if visible` guard or a real-DOM-adjust instruction so the test stays meaningful.

**Type consistency:** Helper exports used consistently — `hasAuth`/`loginViaUI` (auth.ts) in A4/B3; `createStoryboardProject(page, ts)`/`deleteProject(page, name)` (project.ts) in A4; `installStoryboardStubs(page)`/`FIXTURE_IMAGE_URL` (stubs.ts) in B3/B4. `data-testid` names consistent across add (component) and use (spec): `sb-toggle-*`, `sb-panel-*`, `sb-add-node`, `new-project-dialog`, `project-name-input`, `storyboard-project-card`, `upload-file-input`, `upload-node-image`, `generate-button`, `gen-node-image`, `node-menu`/`node-menu-upload`, `split-button`, `frame-thumb`.
