import { describe, it, expect } from 'vitest';
import { classifyFailure, failureLabel } from './taskFailure';

describe('classifyFailure', () => {
  it('treats Soda "no url_player_info" as permanent (Track unavailable)', () => {
    const c = classifyFailure('SodaApiError: track 7407336751146469392 returned no url_player_info');
    expect(c.permanent).toBe(true);
    expect(c.friendly).toBe('Track unavailable');
  });

  it('treats 404 / not found as permanent (Source not found)', () => {
    expect(classifyFailure('HTTP 404 not found').permanent).toBe(true);
    expect(classifyFailure('HTTP 404 not found').friendly).toBe('Source not found');
  });

  it('treats TimeoutError (incl. decoded pickle hint) as transient', () => {
    const c = classifyFailure('TimeoutError (open detail for context)');
    expect(c.permanent).toBe(false);
    expect(c.friendly).toBe('Timed out');
  });

  it('treats dns / URLBlocked as transient network error', () => {
    const c = classifyFailure('URLBlockedError: dns resolution timed out for v26-luna.douyinvod.com');
    // "timed out" matches first → Timed out; still transient (retryable).
    expect(c.permanent).toBe(false);
    expect(c.friendly).not.toBeNull();
  });

  it('leaves unknown errors raw and retryable', () => {
    const c = classifyFailure('some bespoke failure xyz');
    expect(c.permanent).toBe(false);
    expect(c.friendly).toBeNull();
  });

  it('handles empty / missing error', () => {
    expect(classifyFailure(undefined)).toEqual({ permanent: false, friendly: null });
    expect(classifyFailure('')).toEqual({ permanent: false, friendly: null });
  });
});

describe('failureLabel', () => {
  it('returns friendly label when known', () => {
    expect(failureLabel('SodaApiError: track 1 returned no url_player_info')).toBe('Track unavailable');
  });
  it('falls back to raw text when unknown', () => {
    expect(failureLabel('weird raw error')).toBe('weird raw error');
  });
  it('returns null for no error', () => {
    expect(failureLabel(null)).toBeNull();
  });
});
