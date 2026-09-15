/**
 * The one reader of the backend's error envelope (C13).
 *
 * Production wraps **every** `HTTPException` in `ErrorResponse`
 * (`backend/app/core/exceptions.py`):
 *
 * ```json
 * {"success": false, "error": "That name is taken", "code": "http_409",
 *  "request_id": "…", "details": {"code": "username_taken", "message": "…"}}
 * ```
 *
 * — a dict `exc.detail` is moved verbatim to `details` and the original
 * `detail` key does not survive the wrap; a string `exc.detail` leaves
 * `details` null and rides on `error`. The envelope's own `code` is only ever
 * `http_<status>`, so the code a caller can BRANCH on lives under `details`
 * (CLAUDE.md 2026-09-09: reading only `detail` made every refusal read as
 * `http_409` / "409 Conflict" on the real stack while every FastAPI-shaped
 * unit fixture stayed green).
 *
 * Seven services each grew their own copy of these precedence rules. They
 * agreed today; a rule learned on the real stack has to be learned seven
 * times, and nothing fails when one copy misses it. This module is that rule,
 * once.
 *
 * ## What it deliberately does NOT decide
 *
 * It returns `null` where the body said nothing, and never invents a
 * fallback: `http_<status>` vs a route's own default copy is the caller's
 * sentence to write, and the callers genuinely differ ("404 Not Found" vs
 * "Failed to load profile (HTTP 404)"). It also does not read the envelope's
 * `code` into `code` — that would make every failure look classified while
 * naming nothing. It is exposed separately as `envelopeCode` for the one
 * caller that wants it as a second choice.
 *
 * `apiClient.toApiError` is NOT a consumer: it builds the generic `ApiError`
 * from the envelope's own level (`error` before `details.message`, `code`
 * before `details.code`), which is the opposite precedence on purpose — it
 * has no typed contract with any route. Its `details` is already the carrier
 * this module resolves, so an `ApiError` is decoded with `decodeApiError`.
 */

import type { ApiError } from './apiClient';

export interface DecodedErrorEnvelope {
  /** The typed code the route chose (`details.code`), or null when the body
   *  typed nothing. Never `http_<status>` — see `envelopeCode`. */
  code: string | null;
  /** Human copy in the order the body offers it: the typed message, else a
   *  plain-string `detail`, else the envelope's `error` — and, when the body
   *  typed a `details` object that named no message, null rather than the
   *  envelope's generic sentence. That is the chain four services share;
   *  the two that order it differently compose the raw pieces below, in the
   *  open, instead of each keeping a whole second decoder. Null when the
   *  body carried no sentence at all — the caller supplies its own. */
  message: string | null;
  /** The WHOLE typed payload, not just the two keys read above: a refusal's
   *  extra facts (`latest_version`, `waiting_on`, `reason`) are what the
   *  caller's copy names, and this parser must not decide which matter.
   *  Null when the body carried a string reason or nothing. */
  details: Record<string, unknown> | null;
  /** `details.message` on its own. */
  typedMessage: string | null;
  /** A plain-string `detail`/`details` (the unwrapped FastAPI shape). */
  detailText: string | null;
  /** The envelope's own `error` — one sentence for the whole refusal, which
   *  is why it sits behind anything the route typed. */
  error: string | null;
  /** The envelope's own `code` (`http_409`). Present on every wrapped
   *  refusal and worth exactly that much. */
  envelopeCode: string | null;
}

const EMPTY: DecodedErrorEnvelope = {
  code: null,
  message: null,
  details: null,
  typedMessage: null,
  detailText: null,
  error: null,
  envelopeCode: null,
};

function asNonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null;
}

/**
 * Decode a parsed error body (the envelope, or a bare FastAPI `{detail: …}`
 * from a direct-ASGI path that never passes the handler).
 *
 * Non-object bodies (a gateway's HTML already parsed as a string, `null`)
 * decode to all-null: the caller keeps its own status line.
 */
export function decodeErrorEnvelope(body: unknown): DecodedErrorEnvelope {
  if (!body || typeof body !== 'object') return EMPTY;
  const b = body as { detail?: unknown; details?: unknown; error?: unknown; code?: unknown };
  const envelopeCode = asNonEmptyString(b.code);
  const error = asNonEmptyString(b.error);
  // `details` FIRST: it is the one production sends, and `detail` is what the
  // unwrapped paths send. A body never carries both meaningfully.
  const carrier = b.details ?? b.detail;

  if (carrier && typeof carrier === 'object') {
    const typed = carrier as { code?: unknown; message?: unknown };
    const typedMessage = asNonEmptyString(typed.message);
    return {
      code: asNonEmptyString(typed.code),
      // Falls to null, NOT to `error`: a typed refusal that named no message
      // gets the caller's own copy, not the envelope's generic sentence.
      message: typedMessage,
      details: carrier as Record<string, unknown>,
      typedMessage,
      detailText: null,
      error,
      envelopeCode,
    };
  }
  const detailText = asNonEmptyString(carrier);
  return {
    code: null,
    message: detailText ?? error,
    details: null,
    typedMessage: null,
    detailText,
    error,
    envelopeCode,
  };
}

/**
 * Decode an `ApiError` thrown by `apiClient`. Its `details` is already the
 * carrier (`body.details ?? a dict body.detail`), so the typed code is read
 * from there — `err.code` is the envelope's `http_<status>` and cannot tell
 * two refusals of the same status apart.
 */
export function decodeApiError(err: ApiError): DecodedErrorEnvelope {
  const decoded = decodeErrorEnvelope({ details: err.details, code: err.code });
  // `ApiError.message` already went through `toApiError`'s own chain, so it
  // is the sentence this error is carrying — not a second `error` field.
  return { ...decoded, message: decoded.message ?? asNonEmptyString(err.message) };
}
