/**
 * Why the canvas save failed, as something the badge (and a maintainer
 * reading a screenshot) can act on.
 *
 * Before this module the badge rendered a flat "Save failed" for every
 * non-conflict failure while `store.saveError` — which already held the real
 * message — went unread. A user reported a red "Save failed" badge and the
 * investigation had to go to the production logs to learn that the server had
 * seen no failing PUT at all in 48 hours: the request had never landed. The
 * badge could have said so. An untyped, one-size-fits-all failure message is
 * exactly what CLAUDE.md's 「触发路径必须类型化失败回显」 rules out.
 *
 * Split in two on purpose:
 *   - `readErrorStatus` runs at the THROW site (the store's `doSave` catch),
 *     where the thrown value still exists and its `status` can be read.
 *   - `classifySaveFailure` runs at RENDER time over what the store kept
 *     (a message string + that status), so the badge holds no logic and the
 *     classification is unit-testable without a store or a DOM.
 *
 * Both are pure. Neither knows about i18n — the caller maps `kind` to copy.
 */

/**
 * - `conflict`    — 409, someone else saved first. Its own resolution UI
 *                   (`CanvasConflictDialog`) already owns this case; kept in
 *                   the union so the badge has one exhaustive switch.
 * - `unreachable` — the request produced no HTTP response at all (offline,
 *                   DNS, TLS, timeout, a killed tab's in-flight fetch). The
 *                   server never saw it, so it is worth retrying.
 * - `server`      — the server answered and refused, with a status code.
 * - `unknown`     — errored with nothing to say. Should not happen; kept so
 *                   the badge degrades to the old flat message instead of
 *                   rendering an empty reason.
 */
export type SaveFailureKind = 'conflict' | 'unreachable' | 'server' | 'unknown';

export interface SaveFailureInfo {
  kind: SaveFailureKind;
  /** HTTP status when the server answered; `null` when it never did. */
  status: number | null;
  /** Raw underlying message, for the tooltip. `null` when there was none. */
  message: string | null;
}

/** The sentinel `doSave` stores in `saveError` for a 409. */
export const CONFLICT_SAVE_ERROR = 'conflict';

/**
 * HTTP status of a thrown value, or `null` if it carries none.
 *
 * Duck-typed on `status` rather than `instanceof ApiError` for the same
 * reason the store's `isForbidden` is: `saveImpl` is an injectable
 * dependency (tests, and any future transport), so recognising the wire
 * condition must not require one specific Error subclass. `ApiError`
 * (`services/apiClient.ts`) satisfies this, and so does anything else that
 * reports a numeric `status`.
 */
export function readErrorStatus(err: unknown): number | null {
  if (!err || typeof err !== 'object') return null;
  const status = (err as { status?: unknown }).status;
  return typeof status === 'number' && Number.isFinite(status) ? status : null;
}

/**
 * Classify from what the store persisted.
 *
 * `status === null` is read as "the request never reached the server"
 * because that is what a thrown `fetch` means: no response, no status. A
 * client-side bug thrown before the request would land here too — which is
 * why the badge always exposes the raw `message` in its tooltip rather than
 * letting the category be the whole story.
 */
export function classifySaveFailure(
  error: string | null,
  status: number | null,
): SaveFailureInfo {
  if (error === CONFLICT_SAVE_ERROR) {
    return { kind: 'conflict', status, message: null };
  }
  const message = error && error.trim() ? error : null;
  if (status !== null) return { kind: 'server', status, message };
  if (message) return { kind: 'unreachable', status: null, message };
  return { kind: 'unknown', status: null, message: null };
}
