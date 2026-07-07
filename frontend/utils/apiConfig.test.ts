import { describe, expect, it } from 'vitest';
import { sanitizeApiUrl } from './apiConfig';

// Regression net for the 2026-07-07 outage: a Vercel env value ending in a
// LITERAL backslash-n turned every request into "…:88/n/api/v1/…" (WHATWG
// URL treats backslashes in special-scheme URLs as slashes).
describe('sanitizeApiUrl', () => {
  it('strips a trailing literal \\n escape sequence (the outage shape)', () => {
    expect(sanitizeApiUrl('https://api.example.com:88\\n')).toBe('https://api.example.com:88');
  });

  it('strips repeated/mixed trailing literal escapes', () => {
    expect(sanitizeApiUrl('https://api.example.com\\n\\r\\t')).toBe('https://api.example.com');
  });

  it('strips real whitespace and control characters', () => {
    expect(sanitizeApiUrl('  https://api.example.com\n\t ')).toBe('https://api.example.com');
  });

  it('strips trailing slashes', () => {
    expect(sanitizeApiUrl('https://api.example.com//')).toBe('https://api.example.com');
  });

  it('leaves a clean value untouched', () => {
    expect(sanitizeApiUrl('https://api.example.com:88')).toBe('https://api.example.com:88');
  });

  it('keeps an empty value empty (relative-path mode)', () => {
    expect(sanitizeApiUrl('')).toBe('');
  });
});
