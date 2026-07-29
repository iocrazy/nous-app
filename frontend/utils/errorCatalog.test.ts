import { describe, expect, it, vi } from 'vitest';
import { AI_ERROR_CODES, formatTaskError, resolveTaskError } from './errorCatalog';

describe('resolveTaskError', () => {
  it('maps a known error_code to i18n keys and actionable copy', () => {
    const t = vi.fn((_k: string, d: string) => d);
    const r = resolveTaskError({ error_code: 'PROVIDER_AUTH' }, 'DBOSMaxStepRetriesExceeded: …', t);

    expect(r.code).toBe('PROVIDER_AUTH');
    expect(t).toHaveBeenCalledWith('errors.PROVIDER_AUTH.title', expect.any(String));
    expect(t).toHaveBeenCalledWith('errors.PROVIDER_AUTH.hint', expect.any(String));
    // The engine's retry-count string never reaches the user once a code exists.
    expect(r.title).not.toMatch(/DBOS/);
    // The hint must say where to go, not just what broke.
    expect(r.hint).toMatch(/Settings → AI/);
  });

  it('prefers the translated string over the English default', () => {
    const t = (key: string) => (key === 'errors.TASK_TIMEOUT.title' ? '任务超时了' : '重试通常就能成功。');
    const r = resolveTaskError({ error_code: 'TASK_TIMEOUT' }, 'timed out', t);
    expect(r.title).toBe('任务超时了');
  });

  it('every declared code has a title and a hint', () => {
    for (const code of AI_ERROR_CODES) {
      const r = resolveTaskError({ error_code: code }, null);
      expect(r.title.length).toBeGreaterThan(0);
      expect(r.hint?.length ?? 0).toBeGreaterThan(0);
    }
  });

  it('falls back to the raw error for a code this build does not know', () => {
    // Forward compatibility: a newer backend emitting a code an older bundle
    // has never heard of must not blank the message out.
    const r = resolveTaskError({ error_code: 'SOME_FUTURE_CODE' }, 'raw engine text');
    expect(r.code).toBeUndefined();
    expect(r.title).toBe('raw engine text');
    expect(r.hint).toBeUndefined();
  });

  it.each([
    ['no metadata at all', undefined],
    ['metadata without a code', {}],
    ['a non-string code', { error_code: 42 }],
  ])('falls back to the raw error when there is %s', (_label, metadata) => {
    const r = resolveTaskError(metadata as Record<string, unknown> | undefined, 'boom: it broke');
    expect(r.title).toBe('boom: it broke');
    expect(r.code).toBeUndefined();
  });

  it('truncates a long raw error to 160 characters', () => {
    const r = resolveTaskError(null, 'x'.repeat(500));
    expect(r.title).toHaveLength(160);
    expect(r.title.endsWith('…')).toBe(true);
  });

  it('leaves a raw error at the limit untouched', () => {
    const raw = 'y'.repeat(160);
    expect(resolveTaskError(null, raw).title).toBe(raw);
  });

  it('falls back to a generic title when there is neither a code nor raw text', () => {
    const r = resolveTaskError(null, '   ');
    expect(r.title).toBe('The task failed');
    expect(r.hint).toBeUndefined();
  });
});

describe('formatTaskError', () => {
  it('joins title and hint for single-string surfaces', () => {
    const s = formatTaskError({ error_code: 'PROVIDER_RATE_LIMIT' }, null);
    expect(s).toMatch(/rate-limiting — /);
  });

  it('is just the raw text when there is no code', () => {
    expect(formatTaskError(null, 'provider unreachable')).toBe('provider unreachable');
  });
});
