// frontend/components/resources/GenerationsGrid.tsx
// Resources → Project Assets → Generations: the AI-generation card grid.
//
// Extracted out of ResourcesViewInner when the view gained management actions.
// Before that it was a read-only wall of previews with a single `Keep` button —
// the user's report was literally "there is nowhere to manage these previews,
// and no way to delete them".
//
// Two design calls worth recording, because both were judgement rather than
// mechanics:
//
// 1) Confirmation is an INLINE two-step on the card, not a modal and not an
//    undo bar. Deleting a generation is irreversible — the row and (once no
//    sibling generation shares the content key) the object both go away, and
//    there is no recycle bin for Tier-1 media — so an "Undo" affordance would
//    be a lie. Pruning a grid is a repeated action, so a modal per image would
//    be in the way; an inline confirm keeps the image you are about to destroy
//    on screen while you decide.
//
// 2) The delete control is ALWAYS visible in the card footer rather than
//    revealed on hover. Hover-only controls do not exist on touch, are
//    invisible to keyboard users, and — for a complaint that is precisely
//    "I could not find anywhere to manage this" — hiding the answer under a
//    hover state is the wrong instinct twice over.

import { Check, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  deleteGeneration,
  generatedMediaCoverUrl,
  GeneratedMediaError,
  promoteGeneration,
  type GeneratedMediaFailure,
  type GenerationItem,
} from '../../services/generatedMediaService';

export interface GenerationsGridProps {
  items: GenerationItem[];
  /** Called with the next list whenever an item is deleted or promoted. */
  onItemsChange: (next: GenerationItem[]) => void;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

/**
 * Turn a typed failure into a sentence that says what actually went wrong and
 * what the user can do about it.
 *
 * This exists because the previous handler for `Keep` was
 * `addToast(t('common.error', 'Something went wrong'))` — a message that
 * carries no information at all. CLAUDE.md has a standing rule that every
 * user-triggered path must report a typed reason.
 */
function failureMessage(
  t: (key: string, fallback: string, opts?: Record<string, unknown>) => string,
  action: 'keep' | 'delete',
  reason: GeneratedMediaFailure,
  status?: number,
): string {
  switch (reason) {
    case 'network':
      return t(
        'projectAssets.errNetwork',
        'Could not reach the server. Check your connection and try again.',
      );
    case 'unauthenticated':
      return t(
        'projectAssets.errUnauthenticated',
        'Your session has expired. Sign in again to continue.',
      );
    case 'forbidden':
      return action === 'delete'
        ? t(
            'projectAssets.errDeleteForbidden',
            'You do not have permission to delete this generation.',
          )
        : t(
            'projectAssets.errKeepForbidden',
            'You do not have permission to save this generation to the library.',
          );
    case 'not-found':
      return action === 'delete'
        ? t(
            'projectAssets.errDeleteGone',
            'This generation was already deleted, or it is not in this workspace.',
          )
        : t(
            'projectAssets.errKeepGone',
            'This generation no longer exists, so it cannot be saved to the library.',
          );
    case 'server':
    default:
      return action === 'delete'
        ? t('projectAssets.errDeleteServer', 'The server could not delete this generation (HTTP {{status}}).', {
            status: status ?? 500,
          })
        : t('projectAssets.errKeepServer', 'The server could not save this generation (HTTP {{status}}).', {
            status: status ?? 500,
          });
  }
}

function reasonOf(err: unknown): { reason: GeneratedMediaFailure; status?: number } {
  if (err instanceof GeneratedMediaError) return { reason: err.reason, status: err.status };
  return { reason: 'server' };
}

export function GenerationsGrid({ items, onItemsChange, addToast }: GenerationsGridProps) {
  const { t } = useTranslation();
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const drop = (id: string) => onItemsChange(items.filter((i) => i.id !== id));

  async function handleKeep(item: GenerationItem) {
    setBusyId(item.id);
    try {
      const { promoted_resource_id } = await promoteGeneration(item.id);
      // The row STAYS in Generations after a promote — the backend only stamps
      // generated_media.promoted_resource_id. Reflect that here so the card
      // stops offering an action it has already performed.
      onItemsChange(
        items.map((i) =>
          i.id === item.id ? { ...i, promoted_resource_id: promoted_resource_id ?? 'kept' } : i,
        ),
      );
      addToast(t('projectAssets.kept', 'Saved to library'), 'success');
    } catch (err) {
      console.error('[Generations] promote failed:', err);
      const { reason, status } = reasonOf(err);
      if (reason === 'not-found') drop(item.id);
      addToast(failureMessage(t, 'keep', reason, status), reason === 'not-found' ? 'info' : 'error');
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(item: GenerationItem) {
    setBusyId(item.id);
    try {
      await deleteGeneration(item.id);
      drop(item.id);
      addToast(t('projectAssets.deleted', 'Generation deleted'), 'success');
    } catch (err) {
      console.error('[Generations] delete failed:', err);
      const { reason, status } = reasonOf(err);
      // "Already gone" is not a failure of intent — the item is off the server
      // either way, so take it off the grid rather than leaving a dead card.
      if (reason === 'not-found') drop(item.id);
      addToast(
        failureMessage(t, 'delete', reason, status),
        reason === 'not-found' ? 'info' : 'error',
      );
    } finally {
      setBusyId(null);
      setConfirmingId(null);
    }
  }

  return (
    <div className="p-4">
      <div className="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-3">
        {items.map((item) => {
          const kept = Boolean(item.promoted_resource_id);
          const confirming = confirmingId === item.id;
          const busy = busyId === item.id;
          return (
            <div
              key={item.id}
              data-testid={`generation-card-${item.id}`}
              className="rounded-lg overflow-hidden border border-ink-800/40 bg-ink-900 hover:border-ink-600 transition-colors"
            >
              <div className="relative aspect-square bg-ink-800 flex items-center justify-center overflow-hidden">
                <img
                  src={generatedMediaCoverUrl(item.id)}
                  alt={item.prompt ?? item.media_kind}
                  className="w-full h-full object-cover"
                  loading="lazy"
                />
                {kept && (
                  <span
                    data-testid={`generation-kept-${item.id}`}
                    className="absolute left-1.5 top-1.5 inline-flex items-center gap-1 rounded border border-ok-line bg-ok-soft px-1.5 py-0.5 text-[10px] font-medium text-ok"
                  >
                    <Check size={10} />
                    {t('projectAssets.inLibrary', 'In Library')}
                  </span>
                )}
              </div>
              <div className="px-2 py-1.5 space-y-0.5">
                {item.prompt && (
                  <p className="text-xs text-ink-300 truncate" title={item.prompt}>
                    {item.prompt}
                  </p>
                )}
                <p className="text-xs text-ink-500">
                  {[item.model, item.provider].filter(Boolean).join(' · ')}
                </p>
                <p className="text-xs text-ink-600">
                  {new Date(item.created_at).toLocaleDateString()}
                </p>

                {confirming ? (
                  <div className="mt-1 space-y-1">
                    <p className="text-[11px] leading-snug text-danger">
                      {kept
                        ? t(
                            'projectAssets.deleteConfirmKept',
                            'Delete this generation? The copy already saved in your library is kept.',
                          )
                        : t(
                            'projectAssets.deleteConfirm',
                            'Delete this generation? This cannot be undone.',
                          )}
                    </p>
                    <div className="flex gap-1">
                      <button
                        type="button"
                        className="flex-1 rounded px-2 py-0.5 text-xs font-medium bg-ink-700 hover:bg-ink-600 text-ink-200"
                        onClick={() => setConfirmingId(null)}
                      >
                        {t('common.cancel', 'Cancel')}
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        className="flex-1 rounded border border-danger-line bg-danger-soft px-2 py-0.5 text-xs font-medium text-danger disabled:opacity-50"
                        onClick={() => void handleDelete(item)}
                      >
                        {t('projectAssets.confirmDelete', 'Delete')}
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="mt-1 flex gap-1">
                    <button
                      type="button"
                      disabled={kept || busy}
                      className="flex-1 rounded px-2 py-0.5 text-xs font-medium bg-ink-700 hover:bg-ink-600 text-ink-200 hover:text-ink-100 transition-colors disabled:cursor-default disabled:opacity-60 disabled:hover:bg-ink-700"
                      onClick={() => void handleKeep(item)}
                    >
                      {kept
                        ? t('projectAssets.keptShort', 'Saved')
                        : t('projectAssets.keep', 'Keep')}
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      aria-label={t('projectAssets.deleteGeneration', 'Delete generation')}
                      title={t('projectAssets.deleteGeneration', 'Delete generation')}
                      className="rounded px-1.5 py-0.5 text-ink-400 hover:bg-danger-soft hover:text-danger transition-colors disabled:opacity-50"
                      onClick={() => setConfirmingId(item.id)}
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
