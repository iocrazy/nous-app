/**
 * modelHealth — turning the platform-model list's health columns into
 * something a picker can render.
 *
 * The health signal existed in the DB for months but never reached a user:
 * the public endpoint didn't return it, so someone could pick a model whose
 * probe had been failing all day and only find out when the chat failed
 * silently. These helpers are the shared read of that signal — one place, so
 * the agent editor and AI Settings can't drift on what "unhealthy" means.
 */
import { describe, it, expect } from 'vitest';
import type { NousModelPublic } from '../types';
import { buildModelHealth } from './modelHealth';

const model = (over: Partial<NousModelPublic>): NousModelPublic =>
  ({
    name: 'mediahub-deepseek-v4-flash',
    display_name: 'DeepSeek V4 Flash',
    type: 'llm',
    pricing_type: 'per_token',
    pricing_value: 1,
    ...over,
  }) as NousModelPublic;

describe('buildModelHealth', () => {
  it('keys health by model name, carrying the check time', () => {
    const map = buildModelHealth([
      model({ name: 'a', last_test_status: 'fail', last_tested_at: '2026-08-14T00:00:32Z' }),
      model({ name: 'b', last_test_status: 'ok', last_tested_at: '2026-08-14T00:00:12Z' }),
    ]);
    expect(map.a).toEqual({ status: 'fail', testedAt: '2026-08-14T00:00:32Z' });
    expect(map.b.status).toBe('ok');
  });

  it('omits models that have never been probed', () => {
    // "Never checked" is not "healthy" and not "broken" — claiming either
    // would be inventing a signal. Absent means the UI says nothing.
    const map = buildModelHealth([
      model({ name: 'a' }),
      model({ name: 'b', last_test_status: null, last_tested_at: null }),
    ]);
    expect(map).toEqual({});
  });

  it('ignores a status the backend never promises', () => {
    const map = buildModelHealth([
      model({ name: 'a', last_test_status: 'degraded' as unknown as 'ok' }),
    ]);
    expect(map).toEqual({});
  });

  it.each(['image', 'video', 'tts'] as const)(
    'stays silent about a %s model the probe cannot judge',
    (type) => {
      // The backend probe POSTs everything but asr/embedding to
      // /chat/completions, so these types are recorded as failing no matter
      // what. Production 2026-08-14: 3 of 4 `fail` rows were exactly this, and
      // all three models worked. Surfacing them would teach users to ignore
      // the badge — the failure mode this whole feature is meant to prevent.
      const map = buildModelHealth([model({ name: 'a', type, last_test_status: 'fail' })]);
      expect(map).toEqual({});
    },
  );

  it('still reports the types the probe really speaks', () => {
    // Counterpart to the above: the type filter must not swallow the signal
    // it was built to protect.
    const map = buildModelHealth([
      model({ name: 'chat', type: 'llm', last_test_status: 'fail' }),
      model({ name: 'vec', type: 'embedding', last_test_status: 'fail' }),
      model({ name: 'speech', type: 'asr', last_test_status: 'fail' }),
    ]);
    expect(Object.keys(map).sort()).toEqual(['chat', 'speech', 'vec']);
  });
});

describe('module boundaries', () => {
  it('stays free of the i18n instance', async () => {
    // This module is imported by AI Settings, which is not translated and whose
    // test suite never loads i18n. Pulling utils/relativeTime in here (it
    // reaches i18n.ts at module-eval time) suspended the whole AISettings tree
    // and blanked the page in tests. The label wording lives at the call sites
    // for exactly this reason — keep this module's imports type-only.
    const source = await import('./modelHealth?raw');
    expect(source.default).not.toMatch(/from '\.\/relativeTime'/);
    expect(source.default).not.toMatch(/from '\.\.\/i18n'/);
  });
});
