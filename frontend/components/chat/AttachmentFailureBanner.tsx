import React from 'react';
import { useTranslation } from 'react-i18next';

/** One entry of the backend's `attachment_failures`, as it arrives on the
 *  stream's `done` event and on the non-streaming `/chat` response.
 *
 *  `index` is a position in the FULL attachment list the client sent (the
 *  backend re-bases its binary indices for exactly that reason), and `reason`
 *  is a stable code — never a sentence. Anything the UI cannot name falls back
 *  to the generic line rather than printing the code at the user. */
export interface AttachmentFailure {
  index: number;
  kind: string;
  reason: string;
}

export interface AttachmentFailureBannerProps {
  /** Number of attachments the backend could not resolve for the last turn.
   *  0 or undefined → renders nothing. */
  count: number | undefined;
  /**
   * The failures themselves, when the caller kept them.
   *
   * OPTIONAL, and the count is not derived from it: a caller that has only a
   * count still gets the headline. When they are present each distinct reason
   * gets its own line, because "2 attachments could not be used" does not tell
   * a user whether to re-share an asset, generate its first image, or stop
   * waiting for a deleted one.
   */
  failures?: AttachmentFailure[];
}

/**
 * Warn-semantic notice bar surfacing `attachment_failures` from the chat
 * stream's `done` event (Task 6, needs-input first-class plan). Backend
 * has returned this field since G2, but nothing in the UI read it — a
 * silent no-op the "typed result" discipline in CLAUDE.md now forbids.
 *
 * P5 extends it from a bare count to the REASONS: `asset_ref` attachments
 * fail for four typed causes (ruling C) and each one has a different thing to
 * do about it. A code with no string renders the generic line, never the raw
 * code — an untranslated identifier in a user-facing banner is the failure
 * mode this component exists to prevent, not a lesser version of it.
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

  // Distinct reasons, in the order they first occur, so three assets that
  // failed the same way produce one line rather than three identical ones.
  const reasons: string[] = [];
  for (const f of failures ?? []) {
    if (f.reason && !reasons.includes(f.reason)) reasons.push(f.reason);
  }

  return (
    <div
      role="status"
      className="mx-3 mb-2 flex flex-col gap-0.5 rounded-md border border-warn-line bg-warn-soft px-3 py-1.5"
    >
      <span className="text-xs font-medium text-warn">
        {t('chat.attachmentFailures', { count })}
      </span>
      {reasons.map((reason) => (
        <span
          key={reason}
          data-testid="attachment-failure-reason"
          data-reason={reason}
          className="text-[11px] text-warn"
        >
          {t(
            `chat.attachmentFailureReason.${reason}`,
            t('chat.attachmentFailureReason.unknown', 'The Attachment Could Not Be Used'),
          )}
        </span>
      ))}
    </div>
  );
};
