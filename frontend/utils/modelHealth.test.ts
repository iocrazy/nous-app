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
import { buildModelHealth, healthReasonKey } from './modelHealth';

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
    expect(map.a).toEqual({
      status: 'fail',
      testedAt: '2026-08-14T00:00:32Z',
      code: null,
    });
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

  it('treats not_probed as no claim at all, not as a failure', () => {
    // Backend migration 428: the probe now records "I have no protocol for this
    // type" instead of a guaranteed-red `fail`. The value reaches this public
    // payload, so it needs a rule here too — and the rule is the same one that
    // covers never-probed rows: no check happened, so there is nothing to say.
    // The `type` below is deliberately a probeable one: this must hold on the
    // status alone, without leaning on the PROBED_TYPES filter above it.
    const map = buildModelHealth([
      model({ name: 'a', type: 'llm', last_test_status: 'not_probed' }),
    ]);
    expect(map).toEqual({});
  });

  it('carries the failure reason code so a picker can say WHY', () => {
    // #1838 could only say "health check failed". The two rows below are the
    // real 2026-08-14 pair, and they ask the user for opposite things: wait out
    // a local engine that is still loading, vs go deal with a quota.
    const map = buildModelHealth([
      model({ name: 'qwen', last_test_status: 'fail', last_test_code: 'timeout' }),
      model({ name: 'doubao', last_test_status: 'fail', last_test_code: 'rate_limit' }),
    ]);
    expect(map.qwen.code).toBe('timeout');
    expect(map.doubao.code).toBe('rate_limit');
  });

  it('reports a missing code as null rather than guessing one', () => {
    // Rows probed before migration 427 have no code at all. "Unknown" is a
    // fact about our data, not a diagnosis of the model.
    const map = buildModelHealth([
      model({ name: 'a', last_test_status: 'fail' }),
      model({ name: 'b', last_test_status: 'fail', last_test_code: null }),
    ]);
    expect(map.a.code).toBeNull();
    expect(map.b.code).toBeNull();
  });

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

describe('healthReasonKey', () => {
  it.each([
    ['timeout', 'aiSettings.healthReason.timeout'],
    ['unreachable', 'aiSettings.healthReason.unreachable'],
    ['auth', 'aiSettings.healthReason.auth'],
    ['rate_limit', 'aiSettings.healthReason.rate_limit'],
    ['model_not_found', 'aiSettings.healthReason.model_not_found'],
    ['upstream_error', 'aiSettings.healthReason.upstream_error'],
    ['bad_response', 'aiSettings.healthReason.bad_response'],
    ['other', 'aiSettings.healthReason.other'],
  ])('maps %s to its i18n key', (code, key) => {
    expect(healthReasonKey(code)).toBe(key);
  });

  it('falls back to no key when the backend sent nothing', () => {
    expect(healthReasonKey(null)).toBeNull();
    expect(healthReasonKey(undefined)).toBeNull();
  });

  it('falls back to no key for a code this build does not know', () => {
    // The backend enum can grow before a frontend deploy catches up. Returning
    // a key we have no translation for would render the raw key string to the
    // user; returning null degrades to the plain "health check failed" wording.
    expect(healthReasonKey('quota_exhausted')).toBeNull();
  });

  it('never builds a key out of arbitrary text', () => {
    // The guarantee that makes this safe: the key comes from a fixed table, so
    // even if a raw probe message reached here it could not be rendered.
    expect(healthReasonKey("ConnectError: http://10.0.0.10:9997/v1")).toBeNull();
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
