/**
 * The one envelope decoder (C13).
 *
 * Every body below is a shape the real stack sends — `ErrorResponse`
 * (`backend/app/core/exceptions.py`) with `details` as a dict and with
 * `details: null`, plus the bare FastAPI `{detail}` that the direct-ASGI
 * paths still produce. CLAUDE.md「边界 mock 必须用真实 JSON 形状」: a decoder
 * tested only against hand-tidied bodies is exactly how reading `detail`
 * instead of `details` stayed green for months while every production
 * refusal came out as "409 Conflict" (2026-09-09).
 */
import { describe, it, expect } from 'vitest';
import { decodeErrorEnvelope, decodeApiError } from './errorEnvelope';
import { ApiError } from './apiClient';

describe('decodeErrorEnvelope — production ErrorResponse', () => {
  it('reads the typed code and message out of a dict `details`', () => {
    // cn.nous.ink, POST /api/v1/issues/{id}/pause on an already-paused issue.
    const decoded = decodeErrorEnvelope({
      success: false,
      error: 'issue is already paused',
      code: 'http_409',
      request_id: '01J9W2K7Q3',
      details: { code: 'already_paused', message: 'issue is already paused' },
    });

    expect(decoded.code).toBe('already_paused');
    expect(decoded.message).toBe('issue is already paused');
    expect(decoded.details).toEqual({ code: 'already_paused', message: 'issue is already paused' });
    // The envelope's own code is kept apart: it is `http_409` on every 409,
    // so a caller that treated it as typed would name nothing.
    expect(decoded.envelopeCode).toBe('http_409');
  });

  it('keeps the WHOLE typed payload, not just code/message', () => {
    // A stale-write refusal: the copy has to name the version that appeared,
    // and the decoder does not get to decide which extra facts matter.
    const decoded = decodeErrorEnvelope({
      success: false,
      error: 'a newer version exists',
      code: 'http_409',
      request_id: 'r1',
      details: { code: 'stale_version', message: 'a newer version exists', latest_version: 7 },
    });

    expect(decoded.details?.latest_version).toBe(7);
  });

  it('`details: null` leaves the code untyped and the copy on `error`', () => {
    // The other real envelope: a string `exc.detail` never reaches `details`.
    const decoded = decodeErrorEnvelope({
      success: false,
      error: 'Issue not found',
      code: 'http_404',
      request_id: 'r2',
      details: null,
    });

    expect(decoded.code).toBeNull();
    expect(decoded.details).toBeNull();
    expect(decoded.message).toBe('Issue not found');
    expect(decoded.error).toBe('Issue not found');
    expect(decoded.envelopeCode).toBe('http_404');
  });

  it('accepts the bare FastAPI shapes the unwrapped paths still send', () => {
    expect(decodeErrorEnvelope({ detail: { code: 'run_live', message: 'pause it first' } })).toMatchObject({
      code: 'run_live',
      message: 'pause it first',
    });

    const text = decodeErrorEnvelope({ detail: 'Issue not found' });
    expect(text.code).toBeNull();
    expect(text.detailText).toBe('Issue not found');
    expect(text.message).toBe('Issue not found');
    expect(text.details).toBeNull();
  });

  it('a typed refusal that named no message answers null, not the envelope sentence', () => {
    // Four services then fall back to their own status line; profileService
    // deliberately prefers `error` and composes the pieces itself.
    const decoded = decodeErrorEnvelope({
      success: false,
      error: 'Conflict',
      code: 'http_409',
      details: { code: 'budget_exhausted' },
    });

    expect(decoded.code).toBe('budget_exhausted');
    expect(decoded.message).toBeNull();
    expect(decoded.error).toBe('Conflict');
  });

  it('says nothing about a body that is not an object', () => {
    // A gateway's HTML, an empty body, a JSON string: the caller keeps its
    // own status line rather than painting "undefined" at the user.
    for (const body of [null, undefined, '<html>502</html>', 42]) {
      const decoded = decodeErrorEnvelope(body);
      expect(decoded.code).toBeNull();
      expect(decoded.message).toBeNull();
      expect(decoded.details).toBeNull();
      expect(decoded.envelopeCode).toBeNull();
    }
  });

  it('ignores empty-string code/message the way a missing key is ignored', () => {
    const decoded = decodeErrorEnvelope({ error: '', code: '', details: { code: '', message: '' } });
    expect(decoded.code).toBeNull();
    expect(decoded.message).toBeNull();
    expect(decoded.envelopeCode).toBeNull();
  });
});

describe('decodeApiError — the same envelope, already unwrapped by apiClient', () => {
  it('reads the typed code from `details`, never from the envelope code', () => {
    // GET /api/v1/resources/{id}/provenance on a human upload: `err.code` is
    // `http_404` for BOTH "no chain here" and "this route is not deployed",
    // and only the typed code can tell them apart.
    const err = new ApiError('no generation behind this resource', 404, {
      code: 'http_404',
      requestId: 'r3',
      details: { code: 'no_provenance', message: 'no generation behind this resource' },
    });

    const decoded = decodeApiError(err);
    expect(decoded.code).toBe('no_provenance');
    expect(decoded.envelopeCode).toBe('http_404');
    expect(decoded.message).toBe('no generation behind this resource');
  });

  it('an untyped 404 (route not deployed) types nothing', () => {
    // FastAPI's own `{"detail": "Not Found"}` — apiClient leaves `details`
    // undefined for a string detail, so nothing here is branchable.
    const err = new ApiError('Not Found', 404, { code: undefined, details: undefined });

    const decoded = decodeApiError(err);
    expect(decoded.code).toBeNull();
    expect(decoded.details).toBeNull();
    // The sentence still survives: it is the one apiClient already composed.
    expect(decoded.message).toBe('Not Found');
  });
});
