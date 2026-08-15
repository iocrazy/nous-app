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
