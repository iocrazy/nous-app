/**
 * Centralized API base URL accessor.
 * All frontend files should import from here instead of defining their own.
 */
export const getApiUrl = (): string => {
  if (_useFallback) {
    const fb = getFallbackApiUrl();
    if (fb) return fb;
  }
  return getPrimaryApiUrl();
};

const getPrimaryApiUrl = (): string => {
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    return sanitizeApiUrl(import.meta.env.VITE_API_URL || '');
  }
  return 'http://localhost:8080';
};

/** Secondary API base (the Cloudflare tunnel). Null when not configured —
 *  dev / self-hosted setups have one origin and no failover. */
export const getFallbackApiUrl = (): string | null => {
  if (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_FALLBACK_URL) {
    const fb = sanitizeApiUrl(import.meta.env.VITE_API_FALLBACK_URL);
    // A fallback identical to the primary is no fallback at all.
    if (fb && fb !== getPrimaryApiUrl()) return fb;
  }
  return null;
};

// ── failover state ─────────────────────────────────────────────────────────
// The cn.nous.ink:88 primary is a residential direct link: fast from inside
// China but it flaps (carrier resets, NAT expiry, IP churn). Two consecutive
// network-level failures flip every subsequent request to the Cloudflare
// fallback; a background probe flips back the moment the primary answers.
// HTTP errors (4xx/5xx) are NOT failures here — the origin answered.

const FAILOVER_THRESHOLD = 2;
export const PRIMARY_PROBE_INTERVAL_MS = 60_000;

let _failureCount = 0;
let _useFallback = false;
let _probeTimer: ReturnType<typeof setInterval> | null = null;

export const isUsingFallbackApi = (): boolean => _useFallback;

/** Call on fetch-level failures only (TypeError / abort on timeout). */
export const reportApiNetworkFailure = (): void => {
  if (_useFallback || !getFallbackApiUrl()) return;
  _failureCount += 1;
  if (_failureCount >= FAILOVER_THRESHOLD) {
    _useFallback = true;
    console.warn(
      `[apiConfig] primary API unreachable ×${_failureCount} — switching to ${getFallbackApiUrl()}`,
    );
    if (!_probeTimer && typeof setInterval !== 'undefined') {
      _probeTimer = setInterval(() => {
        void probePrimaryOnce();
      }, PRIMARY_PROBE_INTERVAL_MS);
    }
  }
};

/** One primary-health probe; flips back and stops probing on success. */
export const probePrimaryOnce = async (): Promise<void> => {
  if (!_useFallback) return;
  try {
    const res = await fetch(`${getPrimaryApiUrl()}/api/v1/healthz`, {
      signal: typeof AbortSignal !== 'undefined' && AbortSignal.timeout
        ? AbortSignal.timeout(5000)
        : undefined,
    });
    if (res.ok) {
      _useFallback = false;
      _failureCount = 0;
      if (_probeTimer) {
        clearInterval(_probeTimer);
        _probeTimer = null;
      }
      console.info('[apiConfig] primary API recovered — switching back');
    }
  } catch {
    // still down — keep the fallback
  }
};

export const _resetFailoverForTests = (): void => {
  _failureCount = 0;
  _useFallback = false;
  if (_probeTimer) {
    clearInterval(_probeTimer);
    _probeTimer = null;
  }
};

/**
 * Defends against a poisoned env value. A Vercel env var once carried a
 * LITERAL backslash-n suffix ("…:88\n" as two characters — an escape typed
 * into the value, which .trim() cannot remove because backslash is not
 * whitespace). The WHATWG URL parser treats backslashes in special-scheme
 * URLs as forward slashes, so every request silently became
 * "…:88/n/api/v1/…" and 404'd — a full production outage for fresh bundles
 * (2026-07-07). Strip whitespace, trailing literal escape sequences, and
 * trailing slashes so a dirty value can never poison request URLs again.
 */
export const sanitizeApiUrl = (raw: string): string => {
  let url = raw.trim();
  // Trailing literal escape sequences typed into the env value: \n \r \t
  // (backslash + letter as two real characters), possibly repeated.
  url = url.replace(/(?:\\[nrt])+$/, '');
  // Interior control characters never belong in a base URL.
  url = url.replace(/[\n\r\t]/g, '');
  return url.replace(/\/+$/, '');
};
