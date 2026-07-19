// Beats M3.5 — user custom templates end to end through the stub harness:
// arrange a beat, "Save as template" (which POSTs the reverse-computed anchors),
// then re-open the Apply wizard and confirm the saved template shows up and
// re-applies to concrete beats. No real backend — beats + templates are mutable
// in-memory fixtures so reloads reflect what was just written.

import { expect, test } from '@playwright/test';
import { SCRIPT_ID, SCRIPT_URL, setupScriptStubs, wireScene, type WireElement } from './helpers/script-stubs';

/** Seeded zoom: 6 px per timeline second. */
const PX_PER_SEC = 6;
/** 53-bit-safe base for the ids the POST handlers mint (see script-stubs). */
const CREATED_ID_BASE = 323456789300000;

const els: WireElement[] = [{ id: 'el_2000001', type: 'action', text: 'A cold open.' }];

async function setupCustom(page: import('@playwright/test').Page) {
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

  // One arranged beat present from the start (50%–100% of a 60s target) so the
  // Save button is enabled and the derived template has a predictable anchor.
  const beats: Record<string, unknown>[] = [
    {
      id: CREATED_ID_BASE,
      script_id: Number(SCRIPT_ID),
      title: 'Open',
      summary: null,
      scene_ids: [],
      sort_order: 1000,
      start_sec: 30,
      duration_sec: 30,
      beat_role: null,
      color: null,
    },
  ];
  const createdBeats: Record<string, unknown>[] = [];

  await page.route(`**/api/v1/scripts/${SCRIPT_ID}/beats`, async (route) => {
    if (route.request().method() === 'POST') {
      const body = JSON.parse(route.request().postData() || '{}');
      createdBeats.push(body);
      const row = {
        id: CREATED_ID_BASE + 100 + createdBeats.length,
        script_id: Number(SCRIPT_ID),
        title: body.title ?? 'Beat',
        summary: body.summary ?? null,
        scene_ids: [],
        sort_order: (beats.length + 1) * 1000,
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

  // Target-length: seed 60s so the ruler + template length default line up.
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

  // Mutable custom-templates collection: GET returns the list, POST appends.
  const templates: Record<string, unknown>[] = [];
  const createdTemplates: Record<string, unknown>[] = [];
  await page.route('**/api/v1/beat-templates', async (route) => {
    if (route.request().method() === 'POST') {
      const body = JSON.parse(route.request().postData() || '{}');
      createdTemplates.push(body);
      const row = {
        id: CREATED_ID_BASE + 900 + createdTemplates.length,
        name: body.name,
        anchors: body.anchors ?? [],
      };
      templates.push(row);
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
      body: JSON.stringify({ success: true, data: templates }),
    });
  });

  return { createdBeats, createdTemplates };
}

async function openArrangement(page: import('@playwright/test').Page) {
  await page.goto(SCRIPT_URL);
  await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
  await page.locator('.mh-rail-module', { hasText: 'Beats' }).click();
  await page.waitForSelector('[data-testid="beats-arrangement"]', { timeout: 10_000 });
}

test('save the arrangement as a template, then re-apply it from the wizard', async ({ page }) => {
  const { createdBeats, createdTemplates } = await setupCustom(page);
  await openArrangement(page);

  // Save the current arrangement (one beat 50%–100%) as a template.
  await page.locator('[data-testid="arr-save-template"]').click();
  const saveModal = page.locator('[data-testid="beats-save-template-modal"]');
  await expect(saveModal).toBeVisible();
  await saveModal.locator('[data-testid="beats-save-template-name"]').fill('My Two-Act');
  await saveModal.locator('[data-testid="beats-save-template-save"]').click();

  // The POST carried the reverse-computed anchor (30s/30s of a 60s total).
  await expect.poll(() => createdTemplates.length).toBe(1);
  expect(createdTemplates[0].name).toBe('My Two-Act');
  expect(createdTemplates[0].anchors).toEqual([
    { title: 'Open', summary: null, pctStart: 50, pctEnd: 100, color: null },
  ]);

  // Re-open the Apply wizard — the saved template now appears as a card.
  await page.locator('[data-testid="arr-templates"]').click();
  const wizard = page.locator('[data-testid="beats-template-wizard"]');
  await expect(wizard).toBeVisible();
  const customCard = wizard.locator('[data-testid="beats-template-card"][data-custom="true"]');
  await expect(customCard).toContainText('My Two-Act');

  // Apply it (append) at a 60s target → one beat replaying the stored anchor
  // (50%–100% of 60s = start 30s, duration 30s).
  await customCard.click();
  await wizard.locator('[data-testid="beats-template-next"]').click(); // → length
  await wizard.locator('[data-testid="beats-template-preset"][data-sec="60"]').click();
  await wizard.locator('[data-testid="beats-template-next"]').click(); // → mode (beats exist)
  await wizard.locator('[data-testid="beats-template-mode-append"]').click();
  await wizard.locator('[data-testid="beats-template-next"]').click(); // Generate

  await expect.poll(() => createdBeats.length).toBe(1);
  expect(createdBeats[0].title).toBe('Open');
  expect(createdBeats[0].beat_role).toBeNull();
  expect(createdBeats[0].start_sec).toBe(30);
  expect(createdBeats[0].duration_sec).toBe(30);
});
