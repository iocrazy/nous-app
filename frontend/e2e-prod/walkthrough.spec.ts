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

test('prod walkthrough: login → overview → storyboard → canvas → library panel → shot list → script editor → project materials → generated inbox → assets codex', async ({ page }) => {
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

  // ── 5b. The asset card (P4). An asset-library reference is now a node type
  // like any other, and it is the P4 surface most exposed to the 2026-08-12
  // failure mode: it renders from a detail fetch made per card, so a broken
  // scope or a 500 leaves a card that is in the DOM and shows nothing.
  //
  // This board may legitimately have no asset card on it, so the assertion is
  // a DISJUNCTION — an asset card is visible, or the canvas says it is empty,
  // or the ordinary nodes above are what this board holds. Which branch was
  // taken is ANNOTATED rather than swallowed: a step that can only pass is a
  // step that reports success without checking anything, and the annotation is
  // what lets a reader of the run see whether an asset card was actually
  // exercised (CLAUDE.md 验收纪律 — probes must be falsifiable, and one that
  // cannot fail must at least say so).
  const assetCard = page.locator('[data-testid="smart-asset-node"]:visible');
  const emptyCanvas = page.getByText('This canvas is empty');
  const sawAssetCard = await assetCard
    .first()
    .waitFor({ state: 'visible', timeout: 5_000 })
    .then(() => true)
    .catch(() => false);
  if (sawAssetCard) {
    await expect(assetCard.first()).toBeVisible();
  } else {
    // No asset card here. The board must still be in one of the two states a
    // working canvas can be in — nodes drawn, or the empty hint shown.
    const emptyShown = await emptyCanvas
      .waitFor({ state: 'visible', timeout: 2_000 })
      .then(() => true)
      .catch(() => false);
    expect(emptyShown || (await visibleNodes.count()) > 0).toBe(true);
  }
  test.info().annotations.push({
    type: 'p4-asset-card',
    description: sawAssetCard
      ? 'an asset card was visible on the episode canvas'
      : 'no asset card on this board — the P4 card was NOT exercised',
  });

  // ── 5c. The canvas Library panel (§4.10). NOT on the storyboard canvas
  // above: `LibraryPanel` mounts behind `isSmartFamily(kind)` and 'storyboard'
  // is deliberately outside that family (see CanvasPage.tsx and canvas-core
  // types.ts) — lite/storyboard panel access is staged to P3. So this step
  // goes to a real smart canvas through the workspace's own Canvas module,
  // the same two clicks a user makes.
  //
  // The card is chosen BY KIND, never by position. That module's list is
  // unfiltered and ordered newest-edited first
  // (`canvas_repository.list_for_project`), and the episode's system
  // storyboard canvas is project-scoped and therefore in it — so `.first()`
  // would open a canvas the panel does not mount on whenever that canvas
  // happened to be touched last, and a healthy build would go red for a
  // reason that has nothing to do with the Library. A gate whose red does not
  // mean a regression is a gate people learn to wave through.
  //
  // Opened with the `L` shortcut rather than the top-bar chip on purpose: the
  // chip is Standard-canvas-and-entity-board chrome, while `L` is wired for
  // the whole smart family, so this passes whichever of the six kinds below
  // the card turns out to be. It is also §4.2's own entry point.
  //
  // Read-only throughout: open the panel, switch segment, type in the search
  // box, close it. Nothing is placed and nothing is saved.
  //
  // DISJUNCTION + annotation, the same shape as 5b: a project with no
  // smart-family canvas is a legitimate state, and asserting a card would
  // turn "nothing sketched here" into a red deploy. Either such a card is
  // there and the panel gets exercised, or the module mounted and showed what
  // it has — a blank surface, the failure this walkthrough exists for, is
  // neither.
  await page.getByTestId('ws-module-canvas').click();
  // Exactly the kinds `isSmartFamily` accepts (features/canvas-core/types.ts).
  // A kind added there without being added here costs coverage, not a red run.
  const SMART_FAMILY_KINDS = ['smart', 'lite', 'character', 'location', 'prop', 'costume'];
  const smartCards = page.locator(
    SMART_FAMILY_KINDS.map(
      (k) => `[data-testid="workspace-canvas-card"][data-canvas-kind="${k}"]:visible`,
    ).join(', '),
  );
  const sawCanvasCard = await smartCards
    .first()
    .waitFor({ state: 'visible', timeout: 15_000 })
    .then(() => true)
    .catch(() => false);
  if (sawCanvasCard) {
    await smartCards.first().click();
    await expect(page.locator('.react-flow').first()).toBeVisible({ timeout: 20_000 });
    // `useCanvasShortcuts` listens on window, so no click into the surface is
    // needed — and a pane click would change the selection on someone's real
    // canvas, which this walkthrough does not do.
    await page.keyboard.press('l');
    await expect(page.getByTestId('library-panel')).toBeVisible({ timeout: 15_000 });
    // Uploads: the one segment that reads a store every account has.
    await page.getByTestId('library-segment-uploads').click();
    await page.getByTestId('library-search').fill('a');
    // The consequence line is the grid's own chrome — it renders whether or
    // not the search matched anything, so a visible one separates "mounted
    // and empty" from "mounted and broken", and it is §4.6's acceptance.
    await expect(page.getByTestId('library-consequence')).toBeVisible();
    await page.getByTestId('library-close').click();
    await expect(page.getByTestId('library-panel')).toBeHidden();
  } else {
    // No smart-family card. The module must still have MOUNTED, which is two
    // legitimate states: it says the list is empty, or it drew cards and none
    // of them is a kind the panel mounts on. A blank surface is neither.
    const anyCard = page.locator('[data-testid="workspace-canvas-card"]:visible');
    const emptyShown = await page
      .getByTestId('workspace-canvas-empty')
      .waitFor({ state: 'visible', timeout: 5_000 })
      .then(() => true)
      .catch(() => false);
    expect(emptyShown || (await anyCard.count()) > 0).toBe(true);
  }
  test.info().annotations.push({
    type: 'library-panel',
    description: sawCanvasCard
      ? 'the Library panel opened on a smart-family canvas and its search box responded'
      : 'no smart-family canvas in this project — the Library panel was NOT exercised',
  });

  // Back to the storyboard module for the remaining episode steps.
  await page.goto(`/team/${TEAM_ID}/projects/${PROJECT_ID}`);
  await expect(page.getByTestId('workspace-topbar')).toBeVisible({ timeout: 30_000 });
  await page.getByTestId('ws-module-episodes').click();
  await expect(page.getByTestId('ws-ep-card')).toBeVisible();
  await page.getByTestId('ws-ep-storyboard').click();
  await expect(page.getByTestId('episode-storyboard-page')).toBeVisible({ timeout: 15_000 });

  // ── 6. Shot List tab (view three) — the flat per-shot table. ────────────
  await page.locator('[data-view="shotlist"]').click();
  await expect(page.getByTestId('ep-shotlist-table')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId(`ep-shotlist-row-${SHOT_ID}`)).toBeVisible();

  // ── 7. Back to the script module — the inline-mounted editor shell. ─────
  await page.getByTestId('ws-ep-script').click();
  await expect(page.locator('[data-editor-shell]')).toBeVisible({ timeout: 15_000 });

  // ── 8. Sidebar → the project's Characters materials page (P3). These four
  // modules (Characters / Locations / Props / Costumes) moved off the
  // project-local `project_characters` / `project_lib_entities` tables onto
  // the asset library: the page is now a VIEW over `asset_project_refs`,
  // served by `GET /api/v1/projects/{id}/assets`.
  //
  // Same contract as steps 9 and 10 below: NO claim about contents. A project
  // with nothing linked yet is a legitimate state, and asserting on cards
  // would turn "no cast imported yet" into a red deploy. The falsifiable
  // signal is the LINKED COUNT — `project-assets-count` renders only on the
  // loaded branch (the panel's error branch swaps it for `project-assets-
  // error` instead), so a visible count separates "mounted and empty" from
  // "the project-scoped read failed". That distinction is the whole reason
  // the panel keeps `loadError` separate from an empty list.
  await page.getByTestId('ws-module-characters').click();
  await expect(page.getByTestId('project-assets-panel')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId('project-assets-count')).toBeVisible();

  // ── 9. Library → Generated inbox (P1). Deliberately makes NO claim about
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

  // ── 10. Library → Assets codex (P2). Same contract as step 9: NO claim
  // about contents. This account's codex may legitimately be empty, and
  // asserting on cards would turn "no assets yet" into a red deploy. What is
  // smoked is that the rail entry is reachable and the shelf mounts against
  // the real `/api/v1/assets` — a blank surface is the failure this catches.
  //
  // Exact name, no `.first()`: the chevron beside this entry is named
  // "Expand Assets", so "Assets" addresses exactly one button. A positional
  // workaround here would start clicking the chevron — collapsing the group
  // instead of navigating — the first time DOM order changed, and the failure
  // would look like "the shelf did not mount".
  await page.getByRole('button', { name: 'Assets', exact: true }).click();
  const codex = page.getByTestId('asset-shelf');
  await expect(codex).toBeVisible({ timeout: 15_000 });
  await expect(codex.getByRole('heading', { name: 'Assets' })).toBeVisible();
  // The type tablist is the shelf's own chrome, not data — it renders whether
  // or not a single asset exists, so a visible "All" tab separates "mounted
  // and empty" from "mounted and broken".
  await expect(codex.locator('[role="tab"][data-tab-type="all"]')).toBeVisible();
});
