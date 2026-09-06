import React from 'react';
import { useTranslation } from 'react-i18next';

import { MAX_ASSET_REF_ATTACHMENTS } from './attachmentLimits';

/** One entry of the backend's `attachment_failures`, as it arrives on the
 *  stream's `done` event and on the non-streaming `/chat` response.
 *
 *  `index` is a position in the FULL attachment list the client sent (the
 *  backend re-bases its binary indices for exactly that reason).
 *
 *  `reason` is a CLOSED VOCABULARY on the reference path (ruling C's four
 *  asset codes, v2's `loadout_not_owned`, plus `attachment_limit_exceeded`,
 *  which the chat service raises
 *  for `asset_ref` entries only — resource refs are uncapped) and FREE FORM on the binary one —
 *  `chat_attachment_resolver` builds it as
 *  `f"{type(exc).__name__}: {exc}"` for anything it caught, so two binary
 *  failures rarely share a string and none of them is translatable. That split
 *  is why this component groups rather than merely de-duplicating. */
export interface AttachmentFailure {
  index: number;
  kind: string;
  reason: string;
}

/**
 * The reasons this build has copy for — ruling C's four, `loadout_not_owned`
 * (v2: the staged chip's loadout picker made a foreign loadout id reachable, so
 * the resolver refuses instead of quietly re-dressing the character), and
 * `attachment_limit_exceeded`, which the chat service raises when a turn
 * carries more `asset_ref` attachments than `MAX_ASSET_REF_ATTACHMENTS`. All
 * six arrive with `kind: 'asset_ref'`; `resource_ref` has no cap (any number
 * of them is one batched query) and no typed failure of its own.
 *
 * Keyed on REASON, never on kind: the kind says which bucket the attachment
 * went to, and grouping by it would split one user-visible problem across two
 * lines the day a second bucket learns to report the same cause.
 *
 * An explicit list rather than an `i18n.exists` probe: the check has to work
 * under the `t`-only react-i18next mocks every consumer test uses, and being
 * explicit makes the typed-vs-free-form split reviewable instead of implicit.
 * A new backend reason needs a line here AND a string in both locales; missing
 * either lands it in the counted bucket, which is degraded but never wrong.
 */
const NAMED_REASONS = new Set([
  'asset_not_accessible',
  'asset_deleted',
  'asset_no_primary_image',
  'asset_type_unknown',
  'loadout_not_owned',
  'attachment_limit_exceeded',
]);

export interface AttachmentFailureBannerProps {
  /** Number of attachments the backend could not resolve for the last turn.
   *  0 or undefined → renders nothing. */
  count: number | undefined;
  /**
   * The failures themselves, when the caller kept them.
   *
   * OPTIONAL, and the count is not derived from it: a caller that has only a
   * count still gets the headline. When they are present each distinct NAMED
   * reason gets its own line, because "2 attachments could not be used" does
   * not tell a user whether to re-share an asset, generate its first image, or
   * stop waiting for a deleted one. Unnamed ones are counted into a single
   * line instead — see NAMED_REASONS.
   */
  failures?: AttachmentFailure[];
}

/**
 * Warn-semantic notice bar surfacing `attachment_failures` from the chat
 * stream's `done` event (Task 6, needs-input first-class plan). Backend
 * has returned this field since G2, but nothing in the UI read it — a
 * silent no-op the "typed result" discipline in CLAUDE.md now forbids.
 *
 * P5 extends it from a bare count to the REASONS: reference attachments fail
 * for six typed causes (ruling C's four, v2's `loadout_not_owned`, plus the
 * reference-count cap) and
 * each one has a different thing to do about it. A code with no string is COUNTED into one generic line, never
 * printed — an untranslated identifier in a user-facing banner is the failure
 * mode this component exists to prevent, not a lesser version of it, and one
 * generic sentence repeated per failure is that same noise in other clothes.
 *
 * Kept as its own component (rather than inline in AIChatPanel) so the
 * failure-count → banner mapping is unit-testable without standing up
 * the full chat panel's session/streaming machinery.
 */
export const AttachmentFailureBanner: React.FC<AttachmentFailureBannerProps> = ({
  count,
  failures,
}) => {
  const { t } = useTranslation();
  if (!count || count <= 0) return null;

  // Two buckets. NAMED reasons get a line each, in first-occurrence order and
  // de-duplicated, so three assets that failed the same way say it once.
  // Everything else is COUNTED, not listed: binary reasons are free-form
  // exception strings, so listing one line per failure printed the same
  // untranslated sentence N times over — a line that said nothing, repeatedly.
  const named: string[] = [];
  let unnamed = 0;
  for (const f of failures ?? []) {
    if (!f.reason) continue;
    if (NAMED_REASONS.has(f.reason)) {
      if (!named.includes(f.reason)) named.push(f.reason);
    } else {
      unnamed += 1;
    }
  }

  return (
    <div
      role="status"
      className="mx-3 mb-2 flex flex-col gap-0.5 rounded-md border border-warn-line bg-warn-soft px-3 py-1.5"
    >
      <span className="text-xs font-medium text-warn">
        {t('chat.attachmentFailures', { count })}
      </span>
      {named.map((reason) => (
        <span
          key={reason}
          data-testid="attachment-failure-reason"
          data-reason={reason}
          className="text-[11px] text-warn"
        >
          {/* `n` is passed for every reason, not just the one that reads it:
              i18next ignores an unused option, and branching per reason here
              is how the interpolation silently stops being supplied the day a
              second code needs it. `{{n}}`, never `{{count}}` — see the note
              on the unnamed line below for why that matters. */}
          {t(`chat.attachmentFailureReason.${reason}`, {
            n: MAX_ASSET_REF_ATTACHMENTS,
          })}
        </span>
      ))}
      {unnamed > 0 && (
        <span
          data-testid="attachment-failure-reason"
          data-reason="unknown"
          data-unnamed-count={unnamed}
          className="text-[11px] text-warn"
        >
          {/* `{{n}}`, never `{{count}}` — i18next reads a `count` option as a
              plural selector and would go looking for `_one` / `_other`
              siblings that do not exist, falling through to the raw key. */}
          {unnamed === 1
            ? t('chat.attachmentFailureReason.unknown', 'The Attachment Could Not Be Used')
            : t(
              'chat.attachmentFailureReason.unknownCount',
              '{{n}} Attachments Could Not Be Used',
              { n: unnamed },
            )}
        </span>
      )}
    </div>
  );
};
