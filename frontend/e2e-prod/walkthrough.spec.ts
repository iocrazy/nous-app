// e2e-prod/walkthrough.spec.ts
//
// Real-stack smoke walkthrough — real login, real backend, real data. Run
// this once after every frontend deploy (npm run e2e:prod). It exists
// because of a 2026-08 incident where version.json / readyz / CI were all
// green while the actual product was broken: nothing in the release gate
// had ever loaded real data through the real UI. "测试绿 ≠ 真栈正常" is the
// front-end mirror of CLAUDE.md's storage-deploy lesson "读正常 ≠ 服务正常"
// — see CLAUDE.md「部署验收纪律」.
//
// Read-only: this walkthrough never generates, edits, or deletes anything.
// All assertions are VISIBILITY assertions, not bare DOM presence/count —
// the 2026-08-12 canvas incident's own regression test taught this the hard
// way: `.react-flow__node` duplicated-id nodes render in the DOM but
// `visibility:hidden` forever, so `toHaveCount(n)` on the plain locator
// passes on a broken build. Every canvas assertion here checks `:visible`.
//
// Credentials: see helpers.ts (env vars or an out-of-tree file — never the
// repo). Fixture ids: see helpers.ts's WALKTHROUGH_IDS and README.md
// "Fixture data" for what they point at and how they were created.

import { expect, test } from '@playwright/test';
import { loadProdCreds, WALKTHROUGH_IDS } from './helpers';

const { teamId: TEAM_ID, projectId: PROJECT_ID, episodeId: EPISODE_ID, sceneId: SCENE_ID, shotId: SHOT_ID } =
  WALKTHROUGH_IDS;

test('prod walkthrough: login → overview → storyboard → canvas → shot list → script editor → generated inbox', async ({ page }) => {
  const creds = loadProdCreds();

  // i18n defaults to 'zh' with nothing in localStorage (i18n.ts:8) — pin 'en'
  // so the walkthrough's assertions don't accidentally depend on which
  // locale a fresh browser profile happens to land on (also matches how the
  // repo's stubbed e2e suite pins language — e2e/helpers/stubs.ts callers).
  await page.addInitScript(() => {
    try {
      localStorage.setItem('language', 'en');
    } catch {
      /* localStorage unavailable — nothing we can do */
    }
  });

  // ── 1. Login — real UI, real Supabase Auth call (no stubbed routes,
  // no seeded localStorage session: this exercises the actual login path a
  // user goes through, not a shortcut around it). ─────────────────────────
  await page.goto('/login');

  // Preflight: the browser must actually be producing animation frames.
  // Playwright's actionability "stable" gate compares an element's box
  // across two consecutive requestAnimationFrame callbacks, so on a host
  // whose compositor issues no BeginFrames, rAF never fires and EVERY
  // click() dies on `waiting for element to be visible, enabled and
  // stable` — pointing the blame at a perfectly healthy button. That cost
  // a full misdiagnosis once (see playwright.config.ts's launch args for
  // the measurements and the fix). Assert the capability up front so the
  // failure names itself instead of masquerading as a product break.
  const framesFlow = await page.evaluate(
    () =>
      new Promise<boolean>((resolve) => {
        requestAnimationFrame(() => resolve(true));
        setTimeout(() => resolve(false), 3_000);
      }),
  );
  expect(
    framesFlow,
    'Chromium produced no animation frame in 3s — the compositor is not ticking on this host, ' +
      'so every click() will fail the actionability "stable" check regardless of the page. ' +
      'See e2e-prod/playwright.config.ts (launchOptions.args) before suspecting the product.',
  ).toBe(true);

  // Landing page's nav "Log in" (lowercase "in") opens the auth modal —
  // distinct string from the modal's own "Log In" tab/submit labels below,
  // so this is unambiguous even before the modal exists.
  //
  // Deliberately a plain click(): the full actionability contract
  // (visible + enabled + stable + hit-target) is exactly what we want
  // guarding the release gate. `force: true` would make this line pass on
  // a button that is covered by an overlay or disabled — the walkthrough
  // would go green on a login page real users cannot use. The two
  // assertions below state the product contract explicitly, so a future
  // failure separates "the button is wrong" (these fail) from "the
  // browser is not painting" (the preflight above fails).
  const loginTrigger = page.getByText('Log in', { exact: true });
  await expect(loginTrigger).toBeVisible();
  await expect(loginTrigger).toBeEnabled();
  await loginTrigger.click();

  // Email/password inputs are unique on the page once the modal is open
  // (the landing page itself has neither) — no scoping needed.
  await page.locator('input[type="email"]').fill(creds.email);
  const passwordInput = page.locator('input[type="password"]');
  await passwordInput.fill(creds.password);

  // The modal has TWO elements labelled "Log In" — the auth-mode tab
  // (header, above the form) and the actual submit button (form body,
  // below the inputs). Rather than depend on visible text/class (locale-
  // and design-churn-sensitive), walk the DOM structurally: the submit
  // button is the sibling immediately following the password input's own
  // wrapper div — true regardless of locale or label copy.
  const passwordWrapper = passwordInput.locator('..');
  const submitButton = passwordWrapper.locator('xpath=following-sibling::button[1]');
  await submitButton.click();

  // Successful login navigates off /login (LoginPage redirects once
  // isAuthenticated flips true) — this is the login-succeeded signal,
  // not any particular destination (default redirect target is /parser).
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 20_000 });

  // ── 2. Open the test project's workspace. ───────────────────────────────
  await page.goto(`/team/${TEAM_ID}/projects/${PROJECT_ID}`);
  await expect(page.getByTestId('workspace-topbar')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('workspace-sidebar')).toBeVisible();

  // ── 3. Overview accordion — the current episode renders expanded by
  // default (ProjectWorkspace: expandedEpisodeId = currentEpisodeId unless
  // collapsed), so this asserts the accordion's EXPANDED render (workflow
  // strip / node card), not just that the collapsed row exists. ──────────
  await expect(page.getByTestId('ws-overview')).toBeVisible();
  await expect(page.getByTestId(`ep-accordion-row-${EPISODE_ID}`)).toBeVisible();
  await expect(page.getByTestId(`ep-accordion-body-${EPISODE_ID}`)).toBeVisible({ timeout: 15_000 });

  // ── 4. Sidebar → storyboard module (三视图主工作面, view one: scene
  // board). Expanding the episode tree is a separate click from opening the
  // module — mirrors e2e/projects-workspace.spec.ts's known-good sequence. ─
  await page.getByTestId('ws-module-episodes').click();
  await expect(page.getByTestId('ws-ep-card')).toBeVisible();
  await page.getByTestId('ws-ep-storyboard').click();

  await expect(page.getByTestId('episode-storyboard-page')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId('episode-view-tabs')).toBeVisible();
  // View one: the seeded scene's column, and its shot card, both visible.
  await expect(page.getByTestId(`scene-column-${SCENE_ID}`)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId(`shot-card-${SHOT_ID}`)).toBeVisible();

  // ── 5. Canvas tab — the real storyboard React Flow canvas (not the
  // materials-canvas library). This is the exact surface the 2026-08-12
  // incident broke: nodes existed in the DOM but were permanently
  // `visibility:hidden`. `.first().toBeVisible()` + a `:visible` count
  // poll is the regression-shaped assertion, not `toHaveCount` on the bare
  // (possibly-hidden) node locator. ────────────────────────────────────────
  await page.locator('[data-view="canvas"]').click();
  const visibleNodes = page.locator('.react-flow__node:visible');
  await expect(page.locator('.react-flow__node').first()).toBeVisible({ timeout: 20_000 });
  await expect.poll(() => visibleNodes.count(), { timeout: 20_000 }).toBeGreaterThan(0);

  // ── 6. Shot List tab (view three) — the flat per-shot table. ────────────
  await page.locator('[data-view="shotlist"]').click();
  await expect(page.getByTestId('ep-shotlist-table')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId(`ep-shotlist-row-${SHOT_ID}`)).toBeVisible();

  // ── 7. Back to the script module — the inline-mounted editor shell. ─────
  await page.getByTestId('ws-ep-script').click();
  await expect(page.locator('[data-editor-shell]')).toBeVisible({ timeout: 15_000 });

  // ── 8. Library → Generated inbox (P1). Deliberately makes NO claim about
  // what is in it: this account's inbox may legitimately be empty, and an
  // assertion on cards would turn "nothing generated lately" into a red
  // deploy. What is being smoked is that the rail entry is reachable and the
  // view mounts against the real `/api/v1/generated` — the failure this
  // catches is a blank surface, which is exactly what shipped on 2026-08-11.
  await page.goto(`/team/${TEAM_ID}/resources`);
  await page.getByRole('button', { name: /^Generated/ }).click();
  const generated = page.getByTestId('generated-view');
  await expect(generated).toBeVisible({ timeout: 15_000 });
  await expect(generated.getByRole('heading', { name: 'Generated' })).toBeVisible();
  // The tabs are the view's own chrome, not data — they render whether or not
  // the inbox has a single row, so a visible tablist separates "mounted and
  // empty" from "mounted and broken".
  await expect(generated.getByRole('tab', { name: /^Unreviewed/ })).toBeVisible();
});
