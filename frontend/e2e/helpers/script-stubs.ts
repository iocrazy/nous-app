import { type Locator, type Page, type Route } from '@playwright/test';
import { PARENT_PROJECT_ID, TEAM_ID, setupStubbedSession } from './stubs';

/**
 * Script-editor stub layer, built on top of {@link setupStubbedSession}.
 *
 * Fixtures deliberately mirror the WIRE shape, not the client `SceneDoc` shape:
 * scene rows carry elements under `content_json` and ids arrive as JSON numbers
 * (Snowflake BIGINTs). `toSceneDoc` is what normalizes both — fixtures typed as
 * `SceneDoc` would bypass the exact coercion that caused the moveScene 422.
 * See editor/sceneService.ts:41-70.
 */

/**
 * Ids must look like REAL snowflakes, and real snowflakes here are ~15 digits.
 *
 * `generate_snowflake_id()` is deliberately 53-bit — timestamp(41) | sequence(12),
 * epoch 2024-01-01 — so ids stay under Number.MAX_SAFE_INTEGER and survive
 * `res.json()` intact (migrations/050). A fixture with an invented 19-digit id
 * silently rounds to a shared value the moment it is parsed, so every scene ends
 * up with the SAME id and any id-keyed assertion tests a fiction.
 */
export const SCRIPT_ID = '323456789000001';
export const SCRIPT_URL = `/team/${TEAM_ID}/projects/${PARENT_PROJECT_ID}/scripts/${SCRIPT_ID}`;
/** Base for scene ids — same 53-bit-safe magnitude as production. */
export const SCENE_ID_BASE = 323456789100000;

/** localStorage key the shell reads synchronously at mount (editor/formatStorage.ts:14). */
export const formatKey = (scriptId: string) => `editor.format.${scriptId}`;

/** localStorage key for display zoom (bare number string, editor/zoomStorage.ts). */
export const zoomKey = (scriptId: string) => `editor.zoom.${scriptId}`;

function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

/**
 * Drag `grip` onto `target` the way a hand does: press, edge away, cross in
 * steps, settle, release.
 *
 * `locator.dragTo()` is NOT a substitute here. The drop target is bookkept by
 * dragover handlers, and dragTo jumps straight to the destination — too few
 * dragovers land for the row under the pointer to ever be recorded, so the drop
 * fires with no target and the reorder silently no-ops. The test then reports a
 * broken feature that works fine by hand. Element reorder is a timing-sensitive
 * path (it was already broken once by a re-render mid-drag), so the drag that
 * tests it has to have real timing.
 */
export async function dragGrip(page: Page, grip: Locator, target: Locator): Promise<void> {
  const g = await grip.boundingBox();
  const t = await target.boundingBox();
  if (!g || !t) throw new Error('dragGrip: grip or target has no box (not rendered/visible?)');

  await page.mouse.move(g.x + g.width / 2, g.y + g.height / 2);
  await page.mouse.down();
  // Break away first — a drag that starts on top of its own row can settle on
  // itself as the target.
  await page.mouse.move(g.x + g.width / 2, g.y - 10, { steps: 5 });
  await page.mouse.move(t.x + t.width / 2, t.y + t.height / 2, { steps: 12 });
  // Settle into the target's top edge so the drop resolves a stable edge.
  await page.mouse.move(t.x + t.width / 2, t.y + 3, { steps: 6 });
  await page.mouse.up();
}

export type WireElement = {
  id: string;
  type: 'action' | 'dialogue' | 'character' | 'paren' | 'transition' | 'comment' | 'subtitle';
  text: string;
  character_id?: string | null;
};

/** A scene row exactly as the backend serializes it (numeric id, content_json). */
export function wireScene(opts: {
  id: number;
  sortOrder: number;
  location: string;
  elements: WireElement[];
}) {
  return {
    id: opts.id,
    script_id: Number(SCRIPT_ID),
    chapter_id: null,
    heading_int_ext: 'INT',
    location_text: opts.location,
    time_of_day: 'DAY',
    content_version: 1,
    content_json: opts.elements,
    sort_order: opts.sortOrder,
  };
}

export interface ScriptStubOptions {
  scenes: unknown[];
  /** Seed the persisted layout variant so runs are deterministic. */
  format?: 'hollywood' | 'asian';
  /**
   * Seed the persisted display zoom (percent). Defaults to 100 so the many
   * geometry-sensitive specs (seam alignment, narrow-width floor, sheet width)
   * keep the print-baseline size regardless of the product's 115% default.
   * Pass `null` to seed nothing and let the app default (115%) apply.
   */
  zoom?: number | null;
}

/**
 * Seed session + intercept the script-editor backend.
 * Routes register AFTER setupStubbedSession's `**\/api/v1/**` catch-all so they win.
 */
export async function setupScriptStubs(page: Page, opts: ScriptStubOptions): Promise<void> {
  await setupStubbedSession(page);

  const format = opts.format ?? 'hollywood';
  // Seed the BARE string — readStoredFormat (editor/formatStorage.ts) compares the
  // raw localStorage value against 'asian' / 'hollywood', so a JSON.stringify'd
  // '"asian"' would never match and the asian engine would silently never render.
  await page.addInitScript(
    ([key, value]) => {
      try {
        localStorage.setItem(key as string, value as string);
      } catch {
        /* localStorage unavailable — nothing we can do */
      }
    },
    [formatKey(SCRIPT_ID), format] as const,
  );

  // Zoom seeds a BARE number string (not JSON) — matches zoomStorage's writer.
  // Default 100 keeps geometry specs on the print baseline; `null` opts out so
  // the app's 115% default applies (used by the default-zoom assertion).
  const zoom = opts.zoom === undefined ? 100 : opts.zoom;
  if (zoom !== null) {
    await page.addInitScript(
      ([key, value]) => {
        try {
          localStorage.setItem(key as string, value as string);
        } catch {
          /* localStorage unavailable — nothing we can do */
        }
      },
      [zoomKey(SCRIPT_ID), String(zoom)] as const,
    );
  }

  await page.route(`**/api/v1/scripts/${SCRIPT_ID}/scenes`, (route) =>
    fulfillJson(route, { success: true, data: opts.scenes }),
  );

  await page.route(`**/api/v1/scripts/projects/${SCRIPT_ID}`, (route) =>
    fulfillJson(route, {
      success: true,
      data: {
        project: {
          id: SCRIPT_ID,
          name: 'E2E Script',
          team_id: TEAM_ID,
          project_id: PARENT_PROJECT_ID,
          episode_id: null,
          created_at: '2020-01-01T00:00:00Z',
          updated_at: '2020-01-01T00:00:00Z',
        },
        chapters: [],
      },
    }),
  );

  await page.route(`**/api/v1/projects/${PARENT_PROJECT_ID}/episodes`, (route) =>
    fulfillJson(route, { success: true, data: [] }),
  );
}
