/**
 * Centralized API base URL accessor.
 * All frontend files should import from here instead of defining their own.
 */
export const getApiUrl = (): string => {
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    return sanitizeApiUrl(import.meta.env.VITE_API_URL || '');
  }
  return 'http://localhost:8080';
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
