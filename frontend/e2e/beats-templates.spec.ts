// Beats M3 — applying a methodology template end to end through the stub
// harness: the empty-state guide card opens the Apply wizard, choosing Save the
// Cat at a 60-second target must POST 15 beats (midpoint at 30s) and render 15
// cards. No real backend — the beats collection is a mutable in-memory fixture
// so the post-apply reload reflects the freshly created rows.

import { expect, test } from '@playwright/test';
import { SCRIPT_ID, SCRIPT_URL, setupScriptStubs, wireScene, type WireElement } from './helpers/script-stubs';

/** Seeded zoom: 6 px per timeline second → the midpoint card's left is exact. */
const PX_PER_SEC = 6;
/** 53-bit-safe base for the ids the POST handler mints (see script-stubs). */
const CREATED_ID_BASE = 323456789200000;

const els: WireElement[] = [{ id: 'el_2000001', type: 'action', text: 'A cold open.' }];

async function setupTemplates(page: import('@playwright/test').Page) {
  await setupScriptStubs(page, {
    scenes: [wireScene({ id: 323456789100000, sortOrder: 1, location: 'ROOFTOP', elements: els })],
    format: 'hollywood',
  });

  await page.addInitScript(
    ([viewKey, zoomKey, zoom]) => {
      try {
        localStorage.setItem('language', 'en');
        localStorage.setItem(viewKey as string, 'arrangement');
        localStorage.setItem(zoomKey as string, zoom as string);
      } catch {
        /* localStorage unavailable */
      }
    },
    [`editor.beatsView.${SCRIPT_ID}`, `editor.beatsZoom.${SCRIPT_ID}`, String(PX_PER_SEC)] as const,
  );

  // Mutable beats collection: starts empty; each POST appends a created row so
  // the post-apply GET reload returns exactly what the wizard generated.
  const beats: Record<string, unknown>[] = [];
  const created: Record<string, unknown>[] = [];

  await page.route(`**/api/v1/scripts/${SCRIPT_ID}/beats`, async (route) => {
    const method = route.request().method();
    if (method === 'POST') {
      const body = JSON.parse(route.request().postData() || '{}');
      created.push(body);
      const row = {
        id: CREATED_ID_BASE + created.length,
        script_id: Number(SCRIPT_ID),
        title: body.title ?? 'Beat',
        summary: body.summary ?? null,
        scene_ids: [],
        sort_order: created.length * 1000,
        start_sec: body.start_sec ?? null,
        duration_sec: body.duration_sec ?? null,
        beat_role: body.beat_role ?? null,
        color: body.color ?? null,
      };
      beats.push(row);
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, data: row }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true, data: beats }),
    });
  });

  // Target-length PATCH (PUT /scripts/projects/{id}) — the wizard persists the
  // chosen 60s. Fulfil generically since the app ignores the echoed body here.
  await page.route(`**/api/v1/scripts/projects/${SCRIPT_ID}`, async (route) => {
    if (route.request().method() === 'PUT') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, data: { id: SCRIPT_ID, target_duration_sec: 60 } }),
      });
      return;
    }
    await route.fallback(); // GET handled by setupScriptStubs
  });

  return { created };
}

async function openArrangement(page: import('@playwright/test').Page) {
  await page.goto(SCRIPT_URL);
  await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
  await page.locator('.mh-rail-module', { hasText: 'Beats' }).click();
  await page.waitForSelector('[data-testid="beats-arrangement-empty"]', { timeout: 10_000 });
}

test('applying Save the Cat at 60s creates 15 beats with the midpoint at 30s', async ({ page }) => {
  const { created } = await setupTemplates(page);
  await openArrangement(page);

  // Empty-state guide card → wizard (Save the Cat preselected).
  await page.locator('[data-testid="beats-template-empty-card"][data-key="save_the_cat"]').click();
  const wizard = page.locator('[data-testid="beats-template-wizard"]');
  await expect(wizard).toBeVisible();

  // Template step → length step.
  await wizard.locator('[data-testid="beats-template-next"]').click();
  // Pick the 60s preset, then Generate (no existing beats → Next is Generate).
  await wizard.locator('[data-testid="beats-template-preset"][data-sec="60"]').click();
  await wizard.locator('[data-testid="beats-template-next"]').click();

  // 15 beats POSTed, midpoint at 30s (50% of 60) — the wire payload is the
  // source of truth for the anchor math (card pixel x is lane-packed/pushed).
  await expect.poll(() => created.length).toBe(15);
  const midpoint = created.find((b) => b.beat_role === 'save_the_cat.midpoint');
  expect(midpoint?.start_sec).toBe(30);
  // The midpoint carries its methodology title + guidance (i18n resolved).
  expect(midpoint?.title).toBe('Midpoint');

  // 15 cards render after the post-apply reload.
  await expect.poll(async () => page.locator('[data-testid="arr-card"]').count()).toBe(15);
});
