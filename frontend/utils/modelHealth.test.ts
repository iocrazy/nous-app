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
import { buildModelHealth, unhealthyModelNames } from './modelHealth';

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
});

describe('unhealthyModelNames', () => {
  it('collects only the failing models', () => {
    const names = unhealthyModelNames([
      model({ name: 'bad', last_test_status: 'fail' }),
      model({ name: 'good', last_test_status: 'ok' }),
      model({ name: 'unknown' }),
    ]);
    expect(names.has('bad')).toBe(true);
    expect(names.has('good')).toBe(false);
    expect(names.has('unknown')).toBe(false);
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
