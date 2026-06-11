import { describe, it, expect, vi } from 'vitest';
import { createAuthRecovery, shouldIntercept } from './authRecovery';

describe('shouldIntercept', () => {
  const api = 'https://mediahubserver.heygo.cn:88';

  it('matches 401 responses on our backend API only', () => {
    expect(shouldIntercept(`${api}/api/v1/cw/tickets`, 401, api)).toBe(true);
    expect(shouldIntercept(`${api}/api/v1/task-manager/active-counts`, 401, api)).toBe(true);
  });

  it('ignores non-401 and foreign hosts', () => {
    expect(shouldIntercept(`${api}/api/v1/resources`, 200, api)).toBe(false);
    expect(shouldIntercept('https://sb-mediahub.heygo.cn:88/auth/v1/token', 401, api)).toBe(false);
    expect(shouldIntercept('https://example.com/x', 401, api)).toBe(false);
  });

  it('ignores our own auth endpoints to avoid loops', () => {
    expect(shouldIntercept(`${api}/api/v1/auth/signin`, 401, api)).toBe(false);
    expect(shouldIntercept(`${api}/api/v1/auth/refresh`, 401, api)).toBe(false);
  });
});

describe('createAuthRecovery', () => {
  it('refresh success: no signOut, and is single-flight', async () => {
    let resolveRefresh: (v: boolean) => void;
    const refresh = vi.fn(
      () => new Promise<boolean>((resolve) => { resolveRefresh = resolve; }),
    );
    const signOut = vi.fn(async () => {});
    const recover = createAuthRecovery({ refresh, signOut, cooldownMs: 1000 });

    const p1 = recover();
    const p2 = recover(); // concurrent → coalesced
    resolveRefresh!(true);
    await Promise.all([p1, p2]);

    expect(refresh).toHaveBeenCalledTimes(1);
    expect(signOut).not.toHaveBeenCalled();
  });

  it('refresh failure: signs out (AuthGuard then redirects to /login)', async () => {
    const refresh = vi.fn(async () => false);
    const signOut = vi.fn(async () => {});
    const recover = createAuthRecovery({ refresh, signOut, cooldownMs: 1000 });

    await recover();

    expect(signOut).toHaveBeenCalledTimes(1);
  });

  it('cooldown: a successful recovery suppresses re-attempts within the window', async () => {
    const refresh = vi.fn(async () => true);
    const signOut = vi.fn(async () => {});
    const recover = createAuthRecovery({ refresh, signOut, cooldownMs: 60_000 });

    await recover();
    await recover(); // within cooldown → no second refresh

    expect(refresh).toHaveBeenCalledTimes(1);
    expect(signOut).not.toHaveBeenCalled();
  });

  it('refresh throwing is treated as failure → signOut', async () => {
    const refresh = vi.fn(async () => { throw new Error('network'); });
    const signOut = vi.fn(async () => {});
    const recover = createAuthRecovery({ refresh, signOut, cooldownMs: 1000 });

    await recover();

    expect(signOut).toHaveBeenCalledTimes(1);
  });
});
