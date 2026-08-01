import React from 'react';
import { useTranslation } from 'react-i18next';

export interface AttachmentFailureBannerProps {
  /** Number of attachments the backend could not resolve for the last turn.
   *  0 or undefined → renders nothing. */
  count: number | undefined;
}

/**
 * Warn-semantic notice bar surfacing `attachment_failures` from the chat
 * stream's `done` event (Task 6, needs-input first-class plan). Backend
 * has returned this field since G2, but nothing in the UI read it — a
 * silent no-op the "typed result" discipline in CLAUDE.md now forbids.
 *
 * Kept as its own component (rather than inline in AIChatPanel) so the
 * failure-count → banner mapping is unit-testable without standing up
 * the full chat panel's session/streaming machinery.
 */
export const AttachmentFailureBanner: React.FC<AttachmentFailureBannerProps> = ({ count }) => {
  const { t } = useTranslation();
  if (!count || count <= 0) return null;

  return (
    <div
      role="status"
      className="mx-3 mb-2 flex items-center gap-2 rounded-md border border-warn-line bg-warn-soft px-3 py-1.5"
    >
      <span className="text-xs font-medium text-warn">
        {t('chat.attachmentFailures', { count })}
      </span>
    </div>
  );
};
