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

// ── codex-local 码表同步(终审 D-2) ────────────────────────────────────────
//
// 后端 error_catalog.py 的 ALL_ERROR_CODES 里有 7 个 `local_*` 码,而这边的
// AI_ERROR_CODES 曾经一个都没有——注释白纸黑字写着 "Keep in sync",实际没同步。
// 后果不是崩溃(该模块对未知码优雅降级),是 issue 执行链上跑本机模型的 agent 失败后,
// 任务中心退回原始英文报错,把"去你自己电脑上做什么"这条唯一有用的信息丢掉。
//
// ⚠️ 这条测试的作用范围要说清楚:它只能让**删除**变响。跨语言(Python ↔ TS)的
// 真契约测试是账本里 deferred 的 I2,本波没做——所以后端单方面新增一个码,这里
// 仍然不会有任何信号。
describe('codex-local error codes stay in the table', () => {
  const LOCAL_CODES = [
    'local_daemon_offline',
    'local_daemon_outdated',
    'local_cli_missing',
    'local_tools_unsupported',
    'local_codex_not_logged_in',
    'local_ref_rejected',
    'local_codex_failed',
  ];

  it.each(LOCAL_CODES)('%s resolves to actionable copy, not the raw fallback', (code) => {
    const r = resolveTaskError({ error_code: code }, 'DBOSMaxStepRetriesExceeded: …');
    expect(r.code).toBe(code);
    expect(r.title).not.toMatch(/DBOS/);
    // 修复动作在用户自己的机器上,所以 hint 必须指向那里——不是 "Settings → AI
    // 换个模型",对这条链路来说换模型是放弃而不是修复。
    expect(r.hint ?? '').toMatch(/machine|Settings → AI|nous library/);
  });

  it('keys i18n off the lowercase code, matching the backend string exactly', () => {
    const t = vi.fn((_k: string, d: string) => d);
    resolveTaskError({ error_code: 'local_daemon_outdated' }, null, t);
    expect(t).toHaveBeenCalledWith('errors.local_daemon_outdated.title', expect.any(String));
    expect(t).toHaveBeenCalledWith('errors.local_daemon_outdated.hint', expect.any(String));
  });
});
