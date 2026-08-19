import React from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';
import { useOptionalTaskManager } from '../../hooks/useOptionalTaskManager';
import { resolveChipProcessingState, resourceProcessingState } from './resourceStatus';
import { ResourceThumb } from './ResourceThumb';

/**
 * The resource chip's visual body, shared by the two places one can appear:
 * `inline` — a tiptap atom inside a sentence, which is how chips were
 * inserted before the attachment row existed and how saved drafts still
 * carry them; `staged` — the attachment row above the composer, where every
 * newly picked asset now lands.
 *
 * One component on purpose. The status dot is not decoration — it is a live
 * Task Center reading refining an insert-time snapshot — and two copies of
 * that would drift into two different answers to "is this asset readable
 * yet?". Sizing is the only thing the variants disagree on: the staged chip
 * matches the uploaded-file chips it shares a row with.
 */

export type ResourceChipVariant = 'inline' | 'staged';

export interface ResourceChipBodyProps {
  resourceId: string;
  name: string;
  kind: string;
  mime?: string;
  thumbnailUrl?: string;
  transcriptStatus?: string;
  summaryStatus?: string;
  onRemove: () => void;
  variant?: ResourceChipVariant;
}

const CHIP_CLASS: Record<ResourceChipVariant, string> = {
  inline:
    'inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-agent-line '
    + 'bg-agent-soft text-agent text-[12px] mx-0.5 select-none',
  // Padding and type size follow the file-attachment chip next to it; the
  // agent tokens stay so a library asset still reads as a library asset.
  staged:
    'inline-flex items-center gap-1.5 px-2 py-0.5 rounded border border-agent-line '
    + 'bg-agent-soft text-agent text-[11px] select-none',
};

/** Distinct ids per variant so a test can tell "chip moved to the row" from
 *  "chip is still in the sentence" — the whole point of this change. */
const TESTID: Record<ResourceChipVariant, string> = {
  inline: 'resource-chip',
  staged: 'staged-resource-chip',
};

export function ResourceChipBody({
  resourceId,
  name,
  kind,
  mime,
  thumbnailUrl,
  transcriptStatus,
  summaryStatus,
  onRemove,
  variant = 'inline',
}: ResourceChipBodyProps): React.ReactElement {
  const { t } = useTranslation();
  // Optional on purpose: the floating chat also mounts on the fullscreen
  // Script / Storyboard routes, which have no TaskManagerProvider. There the
  // chip falls back to the snapshot taken when it was staged instead of
  // crashing the editor (RECON#18).
  const taskManager = useOptionalTaskManager();
  const snapshot = resourceProcessingState({ kind, mime, transcriptStatus, summaryStatus });
  const status = resolveChipProcessingState(snapshot, taskManager?.tasks, String(resourceId ?? ''));
  const testId = TESTID[variant];

  return (
    <span data-testid={testId} className={CHIP_CLASS[variant]}>
      <ResourceThumb
        thumbnailUrl={thumbnailUrl}
        kind={kind}
        iconSize={11}
        imgClassName="w-4 h-4 rounded object-cover"
        iconClassName="inline-flex"
        imgTestId={`${testId}-thumb`}
        iconTestId={`${testId}-icon`}
      />
      <span className={variant === 'staged' ? 'font-medium truncate max-w-[120px]' : 'font-medium'}>
        {name}
      </span>
      {status && (
        <span
          data-testid={`${testId}-status`}
          data-status={status}
          title={t(
            status === 'processing'
              ? 'chat.mentionPicker.statusProcessing'
              : 'chat.mentionPicker.statusUnprocessed',
          )}
          className={`w-1.5 h-1.5 rounded-full bg-warn ${
            status === 'processing' ? 'animate-pulse' : ''
          }`}
        />
      )}
      <button
        type="button"
        onClick={onRemove}
        className="opacity-50 hover:opacity-100 ml-0.5 inline-flex"
        aria-label="remove"
      >
        <X size={10} />
      </button>
    </span>
  );
}
