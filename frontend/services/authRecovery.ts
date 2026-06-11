/**
 * Global 401 recovery — closes the "JWT expired but the app stays
 * navigable" gap (2026-06-11).
 *
 * supabase-js's getSession() returns the CACHED session even when its
 * background refresh fails (slept tab, rotated/expired refresh token),
 * and it doesn't reliably emit SIGNED_OUT — so every service kept
 * sending a dead Bearer token and the backend answered with a 401 storm
 * while the UI still looked logged-in.
 *
 * Fix: intercept fetch responses to OUR backend API. On a 401, try
 * `supabase.auth.refreshSession()` once (single-flight + cooldown);
 * success → subsequent requests pick up the fresh token; failure →
 * `signOut()`, which fires SIGNED_OUT → AuthContext flips
 * isAuthenticated → AuthGuard redirects to /login.
 */

import { getSupabaseClient } from '../supabaseClient';
import { getApiUrl } from '../utils/apiConfig';

/** Pure decision: does this response warrant a recovery attempt? */
export function shouldIntercept(url: string, status: number, apiBase: string): boolean {
  if (status !== 401) return false;
  if (!apiBase || !url.startsWith(apiBase)) return false;
  // Our own auth endpoints legitimately 401 on bad credentials /
  // expired refresh — re-entering recovery from them would loop.
  if (url.includes('/api/v1/auth/')) return false;
  return true;
}

interface AuthRecoveryDeps {
  /** Attempt a session refresh; resolve true when a live session exists. */
  refresh: () => Promise<boolean>;
  /** Hard logout — must end with SIGNED_OUT so AuthGuard redirects. */
  signOut: () => Promise<void>;
  cooldownMs: number;
}

/** Single-flight + cooldown wrapper around refresh-or-logout. */
export function createAuthRecovery({ refresh, signOut, cooldownMs }: AuthRecoveryDeps) {
  let inflight: Promise<void> | null = null;
  let lastSuccessAt = 0;

  return async function recover(): Promise<void> {
    if (inflight) return inflight;
    if (Date.now() - lastSuccessAt < cooldownMs) return;

    inflight = (async () => {
      let ok = false;
      try {
        ok = await refresh();
      } catch (err) {
        console.error('[authRecovery] refresh threw:', err);
        ok = false;
      }
      if (ok) {
        lastSuccessAt = Date.now();
        return;
      }
      console.warn('[authRecovery] session refresh failed — signing out');
      try {
        await signOut();
      } catch (err) {
        console.error('[authRecovery] signOut failed:', err);
      }
    })().finally(() => {
      inflight = null;
    });
    return inflight;
  };
}

let installed = false;

/**
 * Install the global fetch interceptor (idempotent). Called once from
 * AuthContext after the supabase client exists.
 */
export function installAuthRecovery(): void {
  if (installed || typeof window === 'undefined') return;
  installed = true;

  const recover = createAuthRecovery({
    refresh: async () => {
      const supabase = getSupabaseClient();
      if (!supabase) return false;
      const { data, error } = await supabase.auth.refreshSession();
      return !error && !!data.session;
    },
    signOut: async () => {
      const supabase = getSupabaseClient();
      await supabase?.auth.signOut();
    },
    cooldownMs: 30_000,
  });

  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args: Parameters<typeof fetch>) => {
    const response = await originalFetch(...args);
    try {
      const url =
        typeof args[0] === 'string'
          ? args[0]
          : args[0] instanceof Request
            ? args[0].url
            : String(args[0]);
      if (shouldIntercept(url, response.status, getApiUrl())) {
        // Fire-and-forget: the failed request itself is not retried —
        // callers keep their own error handling; recovery fixes the
        // NEXT request or kicks the user to /login.
        void recover();
      }
    } catch (err) {
      // The interceptor must never break a fetch that already succeeded.
      console.error('[authRecovery] interceptor error:', err);
    }
    return response;
  };
}
