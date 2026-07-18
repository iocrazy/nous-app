// A comment row reads as a QUOTE block (laper parity): a subtle left rule, not
// just italic faint text. This asserts the left border survives on the clean
// page in BOTH the Hollywood (.hw-comment) and Asian (.as-comment) engines — a
// clean-page override used to strip the Hollywood bar entirely.

import { expect, test } from '@playwright/test';
import {
  SCENE_ID_BASE,
  SCRIPT_ID,
  SCRIPT_URL,
  formatKey,
  setupScriptStubs,
  wireScene,
  type WireElement,
} from './helpers/script-stubs';

const cases = [
  { format: 'hollywood' as const, expectClass: 'hw-comment' },
  { format: 'asian' as const, expectClass: 'as-comment' },
];

for (const { format, expectClass } of cases) {
  test(`comment renders a left quote bar — ${format}`, async ({ page }) => {
    const elements: WireElement[] = [
      { id: 'el_c0000001', type: 'action', text: 'A beat.' },
      { id: 'el_c0000002', type: 'comment', text: 'Note to the director about tone here.' },
    ];
    const scenes = [wireScene({ id: SCENE_ID_BASE + 1, sortOrder: 1, location: 'STAGE', elements })];

    await setupScriptStubs(page, { scenes, format });
    // setupScriptStubs stores the format via JSON.stringify ('"asian"'), which
    // readStoredFormat() never matches — so its format:'asian' silently renders
    // hollywood. Write the RAW value (what persistFormat/readStoredFormat use)
    // AFTER, so this later addInitScript wins and the asian engine truly renders.
    // (Harness bug flagged separately; kept local here to avoid disturbing other
    // asian specs that lean on the current fallback behaviour.)
    await page.addInitScript(
      ([key, value]) => {
        try {
          localStorage.setItem(key as string, value as string);
        } catch {
          /* ignore */
        }
      },
      [formatKey(SCRIPT_ID), format] as const,
    );
    await page.goto(SCRIPT_URL);
    await page.waitForSelector('[data-testid="scene-block"]', { timeout: 15_000 });
    await page.waitForTimeout(500);

    const info = await page.evaluate(() => {
      // Select by data-el-type only, so the engine (hollywood/asian) that
      // actually rendered is reflected in the returned className.
      const el = document.querySelector<HTMLElement>('.mh-el-editable[data-el-type="comment"]');
      if (!el) return null;
      const cs = getComputedStyle(el);
      return {
        className: el.className,
        borderStyle: cs.borderLeftStyle,
        borderWidth: parseFloat(cs.borderLeftWidth) || 0,
      };
    });

    expect(info, 'comment element must render').not.toBeNull();
    // Confirm the engine under test actually rendered (format switch took hold).
    expect(info!.className, `comment rendered via the ${format} engine`).toContain(expectClass);
    // The quote bar: a real left rule, not the old stripped/borderless comment.
    expect(info!.borderStyle, 'comment has a left rule (not stripped)').not.toBe('none');
    expect(info!.borderWidth, 'comment left rule has real width').toBeGreaterThanOrEqual(2);
  });
}
