// frontend/components/resources/generated/CleanupDialog.tsx
//
// The ONLY bulk-delete entry point for the Generated inbox, and deliberately a
// two-step one: preview, then confirm. The design note behind it
// ("不自动、不静默——这是当年退役 TTL 的原因") is the reason there is no
// scheduled purge; the price of that is that the manual path must never be
// able to fire a delete the user has not seen the size of.
//
// The gate is enforced by state, not by discipline: `preview` holds the
// dry-run result together with THE DAY COUNT IT WAS RUN FOR, and editing the
// input clears it. A confirm button therefore cannot outlive the number it
// was quoting.

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { UiModal } from '../../ui/primitives';
import { useToast } from '../../Toast';
import { cleanupGenerated, GeneratedApiError } from '../../../services/generatedService';
import type { CleanupResult, GeneratedItem } from '../../../services/generatedService';
import { generatedMediaCoverUrl } from '../../../services/generatedMediaService';
import { placeholderFor } from '../mediaKindPlaceholder';

/** How many of `sample` we show. The backend may send more; a preview is a
 *  reassurance, not a gallery. */
const SAMPLE_LIMIT = 12;
const DEFAULT_DAYS = 30;

export interface CleanupDialogProps {
  scopeId: string;
  onClose: () => void;
  /** Fired after a real (non-dry-run) delete, so the list can reload. */
  onDone: () => void;
}

interface Preview {
  /** The day count this preview describes — compared against the live input
   *  so a stale count can never authorise a delete. */
  days: number;
  result: CleanupResult;
}

export const CleanupDialog: React.FC<CleanupDialogProps> = ({ scopeId, onClose, onDone }) => {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const [days, setDays] = useState(DEFAULT_DAYS);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);

  const reportFailure = (err: unknown) => {
    if (err instanceof GeneratedApiError) {
      addToast(t(`generated.err.${err.code}`, t('generated.err.generic')), 'error');
      return;
    }
    console.error('[CleanupDialog] cleanup failed:', err);
    addToast(t('generated.err.generic'), 'error');
  };

  const handleDaysChange = (raw: string) => {
    const parsed = Number.parseInt(raw, 10);
    setDays(Number.isNaN(parsed) ? 0 : parsed);
    // Any edit invalidates the preview — see the header comment.
    setPreview(null);
  };

  const runPreview = async () => {
    if (busy || days < 1) return;
    setBusy(true);
    try {
      const result = await cleanupGenerated(scopeId, { older_than_days: days, dry_run: true });
      setPreview({ days, result });
    } catch (err) {
      reportFailure(err);
    } finally {
      setBusy(false);
    }
  };

  const runDelete = async () => {
    // Belt and braces: the button only renders behind a matching preview, but
    // this is the call that actually destroys rows, so it re-checks rather
    // than trusting the render path.
    if (busy || preview === null || preview.days !== days) return;
    setBusy(true);
    try {
      const result = await cleanupGenerated(scopeId, { older_than_days: days, dry_run: false });
      addToast(
        t('generated.cleanup.deleted', {
          n: result.deleted,
          defaultValue: '{{n}} deleted',
        }),
        'success',
      );
      setPreview(null);
      onDone();
    } catch (err) {
      reportFailure(err);
    } finally {
      setBusy(false);
    }
  };

  const fresh = preview !== null && preview.days === days;
  const count = fresh ? preview.result.count : 0;
  const sample: GeneratedItem[] = fresh ? preview.result.sample.slice(0, SAMPLE_LIMIT) : [];

  return (
    <UiModal
      isOpen
      title={t('generated.cleanup.title', 'Clean Up Generated')}
      onClose={onClose}
      widthClassName="w-[520px]"
      footer={
        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-line-strong px-3 py-1.5 text-xs font-medium text-content-2 hover:bg-island-2"
          >
            {t('common.cancel', 'Cancel')}
          </button>
          {fresh && count > 0 ? (
            <button
              type="button"
              disabled={busy}
              onClick={runDelete}
              className="rounded-lg border border-danger-line bg-danger-soft px-3 py-1.5 text-xs font-semibold text-danger hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {t('generated.cleanup.confirm', { n: count, defaultValue: 'Delete {{n}}' })}
            </button>
          ) : (
            <button
              type="button"
              disabled={busy || days < 1}
              onClick={runPreview}
              className="rounded-lg border border-accent bg-accent px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {t('generated.cleanup.preview', 'Preview')}
            </button>
          )}
        </div>
      }
    >
      <div className="space-y-3 text-sm">
        <p className="text-xs text-content-3">
          {t(
            'generated.cleanup.hint',
            'Deletes unreviewed generations older than the given number of days. Saved items and items in assets are never touched.',
          )}
        </p>

        <label className="block text-xs font-medium text-content-2">
          {t('generated.cleanup.olderThan', 'Older Than (Days)')}
          <input
            type="number"
            min={1}
            value={days === 0 ? '' : String(days)}
            onChange={(e) => handleDaysChange(e.target.value)}
            className="mt-1 block w-28 rounded-lg border border-line-strong bg-card px-2 py-1 text-sm text-content"
          />
        </label>

        {fresh && count === 0 && (
          <p className="text-xs text-content-3">
            {t('generated.cleanup.empty', 'Nothing To Clean Up')}
          </p>
        )}

        {fresh && count > 0 && (
          <div className="space-y-2">
            <p className="text-xs text-content-2">
              {t('generated.cleanup.matched', {
                n: count,
                defaultValue: '{{n}} Unreviewed Generations Match',
              })}
            </p>
            <div className="grid grid-cols-6 gap-1.5">
              {sample.map((entry) => {
                // Same rule as the grid card: an `audio` or `file` row has no
                // frame, and a `/cover` request for one draws a broken-image
                // icon. A preview whose job is "look at what is about to be
                // deleted" must not show damage that is not there.
                const placeholder = placeholderFor(entry.media_kind);
                return placeholder ? (
                  <span
                    key={entry.id}
                    data-testid="cleanup-sample"
                    data-media-kind={entry.media_kind}
                    title={entry.title}
                    className="flex aspect-square w-full items-center justify-center rounded-md border border-line text-content-4"
                  >
                    <placeholder.Icon size={18} aria-hidden="true" />
                  </span>
                ) : (
                  <img
                    key={entry.id}
                    data-testid="cleanup-sample"
                    src={generatedMediaCoverUrl(entry.id)}
                    alt={entry.title}
                    className="aspect-square w-full rounded-md border border-line object-cover"
                  />
                );
              })}
            </div>
            {/* `truncated` means one pass scanned a bounded window: `count` is
                "matched in this pass", not "matched ever". Staying silent here
                would report the cleanup as finished while rows remain. */}
            {preview.result.truncated && (
              <p className="text-xs text-warn">
                {t(
                  'generated.cleanup.truncated',
                  'Showing the first 2000 — run clean up again for the rest.',
                )}
              </p>
            )}
          </div>
        )}
      </div>
    </UiModal>
  );
};
