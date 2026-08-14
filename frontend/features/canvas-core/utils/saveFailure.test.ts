/**
 * Save-failure classification (2026-08-13).
 *
 * The distinction that matters and that the old flat "Save failed" badge
 * erased: a save the SERVER refused (it answered, with a status) versus one
 * that never reached it (nothing answered). The reported red badge was the
 * second kind — 48 hours of production logs held no failing canvas PUT — and
 * neither the user nor the person debugging it could tell from the screen.
 */

import { describe, expect, it } from 'vitest';

import { CONFLICT_SAVE_ERROR, classifySaveFailure, readErrorStatus } from './saveFailure';

describe('readErrorStatus', () => {
  it('reads the status off an ApiError-shaped throw', () => {
    // The real shape (services/apiClient.ts): `new ApiError(message, 500)`.
    const err = Object.assign(new Error('boom'), { status: 500 });
    expect(readErrorStatus(err)).toBe(500);
  });

  it('is duck-typed — any thrown object reporting a numeric status counts', () => {
    expect(readErrorStatus({ status: 503 })).toBe(503);
  });

  it('returns null for a throw with no status at all (a TypeError from fetch)', () => {
    expect(readErrorStatus(new TypeError('Failed to fetch'))).toBeNull();
  });

  it('ignores a non-numeric or non-finite status instead of trusting it', () => {
    expect(readErrorStatus({ status: '500' })).toBeNull();
    expect(readErrorStatus({ status: Number.NaN })).toBeNull();
  });

  it('survives primitives and null', () => {
    expect(readErrorStatus(null)).toBeNull();
    expect(readErrorStatus(undefined)).toBeNull();
    expect(readErrorStatus('nope')).toBeNull();
  });
});

describe('classifySaveFailure', () => {
  it('classifies the 409 sentinel as a conflict, whatever the status says', () => {
    expect(classifySaveFailure(CONFLICT_SAVE_ERROR, 409)).toMatchObject({ kind: 'conflict' });
    // The sentinel is what `doSave` writes; it wins even without a status.
    expect(classifySaveFailure('conflict', null)).toMatchObject({ kind: 'conflict' });
  });

  it('classifies "the server answered and refused" as server, keeping the code', () => {
    expect(classifySaveFailure('Internal Server Error', 500)).toEqual({
      kind: 'server',
      status: 500,
      message: 'Internal Server Error',
    });
  });

  it('classifies "nothing answered" as unreachable — the request never landed', () => {
    expect(classifySaveFailure('Failed to fetch', null)).toEqual({
      kind: 'unreachable',
      status: null,
      message: 'Failed to fetch',
    });
  });

  it('falls back to unknown rather than inventing a reason for an empty error', () => {
    expect(classifySaveFailure(null, null)).toMatchObject({ kind: 'unknown', message: null });
    expect(classifySaveFailure('   ', null)).toMatchObject({ kind: 'unknown', message: null });
  });

  it('keeps the raw message on every non-conflict kind — the tooltip needs it verbatim', () => {
    expect(classifySaveFailure('canvas is locked', 423).message).toBe('canvas is locked');
    expect(classifySaveFailure('NetworkError when attempting to fetch', null).message).toBe(
      'NetworkError when attempting to fetch',
    );
  });
});
