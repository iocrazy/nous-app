/**
 * Which sentence a music failure turns into.
 *
 * `PUBLISH_NOTE_KEYS` is an ORDERED table and the component resolves with the
 * first match, so a specific reason placed after the `music_*` catch-all is
 * dead code that nobody notices — the user still gets *a* sentence, just the
 * wrong one. That is the same shape as the failures this repo keeps finding:
 * the system knows, the user is told something else.
 *
 * These tests resolve exactly the way the component does, so they go red both
 * when an entry is missing and when it sits below the catch-all.
 */
import { describe, it, expect } from 'vitest';
import { PUBLISH_NOTE_KEYS } from './RecordsPage';
import en from '../../public/locales/en.json';
import zh from '../../public/locales/zh.json';

/** Same resolution the component performs: first entry whose test matches. */
const resolve = (raw: string) => PUBLISH_NOTE_KEYS.find((row) => row.test.test(raw));

describe('publish note reasons', () => {
  it('tells an ambiguous pick apart from a picker that could not be driven', () => {
    // Real wire shape: `publish_distribution` writes `[<reason>] <message>`
    // into error_message, and the reason is the contract.
    const row = resolve(
      '[music_ambiguous] 5 results are indistinguishable from the track that was picked; '
      + 'refusing to guess which one to publish. Nothing was published',
    );
    expect(row?.key).toBe('distribution.records.noteMusicAmbiguous');
  });

  it('tells "we never saw the results" apart from "the platform has no such track"', () => {
    // Two different failures with two different user moves. `music_not_found`
    // means the search answered and this track was not in it — publishing the
    // same batch again repeats the identical search. `music_results_not_seen`
    // means the dialog never answered inside the wait, so publishing again can
    // genuinely land. Collapsing them is the mistake the browser side stopped
    // making on 2026-08-17; this keeps the page from re-making it.
    const notSeen = resolve(
      '[music_results_not_seen] the music dialog never showed results this search '
      + "produced, so whether the platform has '起风了' is unknown; nothing was published "
      + '[rows=0 (the dialog listed nothing) ready=timeout/12000ms anchors=0/0 fresh=0]',
    );
    expect(notSeen?.key).toBe('distribution.records.noteMusicResultsNotSeen');
    // ...and specifically NOT the generic "the control could not be driven"
    // sentence, which is what an entry placed below the catch-all would give.
    expect(notSeen?.key).not.toBe('distribution.records.noteMusicFailed');

    const notFound = resolve("[music_not_found] no music named '起风了' came back");
    expect(notFound?.key).toBe('distribution.records.noteMusicNotFound');
  });

  it('still routes the other music failures to the catch-all', () => {
    for (const reason of ['music_entry_missing', 'music_dialog_stuck', 'music_click_failed']) {
      expect(resolve(`[${reason}] whatever the browser said`)?.key)
        .toBe('distribution.records.noteMusicFailed');
    }
  });

  it('has copy in both languages for every reason it can name', () => {
    // A key with no translation renders the English fallback in a Chinese UI —
    // silently, because i18next never complains about a missing key.
    const lookup = (bundle: unknown, key: string): unknown =>
      key.split('.').reduce<unknown>(
        (node, part) => (node && typeof node === 'object'
          ? (node as Record<string, unknown>)[part]
          : undefined),
        bundle,
      );
    for (const row of PUBLISH_NOTE_KEYS) {
      expect(typeof lookup(en, row.key), `${row.key} missing in en.json`).toBe('string');
      expect(typeof lookup(zh, row.key), `${row.key} missing in zh.json`).toBe('string');
    }
  });
});
