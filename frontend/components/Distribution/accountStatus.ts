import { SocialAccount } from '../../types';

/**
 * Whether a bound account cannot publish right now and needs the user to
 * reconnect it.
 *
 * Two statuses mean that, and they are NOT interchangeable in the copy:
 * `expired` is an OAuth token that lapsed (recover by reauthorizing at the
 * platform), `needs_relogin` is a dead browser session (recover by scanning a
 * new QR code). Any count, filter or row state that means "this one is out of
 * action" must cover both — treating only `expired` as blocking makes a page
 * read "all good" while every session account is offline, which is exactly the
 * silent no-op CLAUDE.md forbids.
 *
 * This lives in its own module because it was already wrong once: AccountsPage
 * covered both statuses while PublishPage checked `status === 'expired'` alone,
 * so a user whose accounts are ALL session-bound (the common case — sessions
 * never reach `expired`) could tick a dead account, press Publish, and only
 * then find out. One predicate, imported by both, is what stops the two pages
 * drifting apart a second time.
 */
export const needsReconnect = (a: SocialAccount): boolean =>
  a.status === 'expired' || a.status === 'needs_relogin';

/**
 * How long ago we last *confirmed* a session account still works — or that we
 * never did.
 *
 * `session_checked_at` is written by the backend sweep
 * (`backend/app/workflows/session_health_check.py`) and only ever advances on a
 * verdict of `healthy`; an inconclusive tick (browser container down, proxy
 * dead) deliberately leaves it alone. So the column means exactly one thing:
 * "the last moment we saw this session alive". Null means we have never seen
 * that — a freshly bound account the sweep has not reached yet.
 *
 * Why this is a separate shape and not a boolean or a formatted string:
 *
 *  * `never` must not collapse into the healthy case. The card used to render
 *    an unchecked account and an account verified an hour ago identically
 *    ("Active"), which turns *absence of evidence* into a positive claim —
 *    the exact thing CLAUDE.md's probe discipline forbids. They are different
 *    facts and must read differently.
 *  * We report the elapsed time and stop there. Deciding that "6 hours" is
 *    stale would need `MIN_RECHECK_INTERVAL_S`, which lives in the backend and
 *    is env-overridable; copying a number the frontend cannot see would render
 *    a guess as a fact. No threshold, no staleness colour — just the age.
 *  * `notApplicable` covers OAuth accounts. The sweep only scans session rows,
 *    so their `session_checked_at` is structurally null forever; labelling
 *    them "never checked" would invent an alarm about a check that does not
 *    apply to them. Their freshness signal is `token_expires_at`, a different
 *    question with a different answer.
 *  * `unknown` covers a value that is present but unparseable. Folding it into
 *    `never` would state something we did not observe, and folding it into
 *    `checked` renders "NaN".
 *
 * A timestamp in the future (clock skew between the DB and the browser) is
 * reported as `now` rather than a negative age.
 */
export type SessionFreshness =
  | { kind: 'notApplicable' }
  | { kind: 'never' }
  | { kind: 'unknown' }
  | { kind: 'checked'; unit: 'now' | 'minutes' | 'hours' | 'days'; value: number };

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

export const describeSessionFreshness = (
  a: SocialAccount,
  now: number = Date.now(),
): SessionFreshness => {
  if (a.auth_type !== 'session') return { kind: 'notApplicable' };

  const raw = a.session_checked_at;
  if (raw === null || raw === undefined || raw === '') return { kind: 'never' };

  const at = new Date(raw).getTime();
  if (Number.isNaN(at)) return { kind: 'unknown' };

  const elapsed = now - at;
  if (elapsed < MINUTE_MS) return { kind: 'checked', unit: 'now', value: 0 };
  if (elapsed < HOUR_MS) {
    return { kind: 'checked', unit: 'minutes', value: Math.floor(elapsed / MINUTE_MS) };
  }
  if (elapsed < DAY_MS) {
    return { kind: 'checked', unit: 'hours', value: Math.floor(elapsed / HOUR_MS) };
  }
  return { kind: 'checked', unit: 'days', value: Math.floor(elapsed / DAY_MS) };
};
