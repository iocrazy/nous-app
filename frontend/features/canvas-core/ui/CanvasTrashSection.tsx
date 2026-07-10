// frontend/features/canvas-core/ui/CanvasTrashSection.tsx
//
// Collapsible trash on the canvas landing page (Infinite parity G9).
// Lazy: the trash list is only fetched when the section is first expanded.
// Restore refetches the live tree through onRestored; Delete Forever is a
// two-click arm/confirm — a destructive action never fires on one click.

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight, Loader2, RotateCcw, Trash2 } from 'lucide-react';
import { useToast } from '../../../components/Toast';
import {
  listTeamCanvasTrash,
  purgeCanvas,
  restoreCanvas,
  type TrashedCanvas,
} from '../services/canvasService';

const FOCUS_RING =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40';

export function CanvasTrashSection({
  teamId,
  onRestored,
}: {
  teamId: string;
  onRestored: () => void;
}) {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<TrashedCanvas[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [armedId, setArmedId] = useState<string | null>(null);

  const loadTrash = async () => {
    setLoading(true);
    try {
      setRows(await listTeamCanvasTrash(teamId));
    } catch (err) {
      console.error('[CanvasTrashSection] load failed:', err);
      addToast(t('canvasList.trashLoadFailed', 'Failed to load trash'), 'error');
    } finally {
      setLoading(false);
    }
  };

  const toggle = () => {
    const next = !open;
    setOpen(next);
    setArmedId(null);
    // Refetch on EVERY expand — cards deleted while collapsed must show up
    // (review F4: a once-loaded cache read as "Trash is empty" forever).
    if (next) void loadTrash();
  };

  const handleRestore = async (canvasId: string) => {
    try {
      await restoreCanvas(canvasId);
      setRows((prev) => prev?.filter((r) => r.id !== canvasId) ?? prev);
      onRestored();
      addToast(t('canvasList.restored', 'Canvas restored'), 'success');
    } catch (err) {
      console.error('[CanvasTrashSection] restore failed:', err);
      addToast(t('canvasList.restoreFailed', 'Failed to restore canvas'), 'error');
    }
  };

  const handlePurge = async (canvasId: string) => {
    if (armedId !== canvasId) {
      setArmedId(canvasId);
      return;
    }
    setArmedId(null);
    try {
      await purgeCanvas(canvasId);
      setRows((prev) => prev?.filter((r) => r.id !== canvasId) ?? prev);
      addToast(t('canvasList.purged', 'Canvas permanently deleted'), 'info');
    } catch (err) {
      console.error('[CanvasTrashSection] purge failed:', err);
      addToast(t('canvasList.purgeFailed', 'Failed to delete canvas'), 'error');
    }
  };

  return (
    <section className="mt-10 border-t border-line pt-4">
      <button
        onClick={toggle}
        className={`flex items-center gap-2 text-sm font-medium text-content-3 hover:text-content ${FOCUS_RING}`}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Trash2 size={14} />
        <span>
          {t('canvasList.trash', 'Trash')}
          {rows !== null ? ` (${rows.length})` : ''}
        </span>
      </button>

      {open && (
        <div className="mt-3">
          {loading && (
            <div className="flex h-16 items-center justify-center">
              <Loader2 size={16} className="animate-spin text-content-3" />
            </div>
          )}
          {!loading && rows !== null && rows.length === 0 && (
            <div className="rounded-xl border border-dashed border-line p-4 text-sm text-content-4">
              {t('canvasList.trashEmpty', 'Trash is empty')}
            </div>
          )}
          {!loading &&
            rows?.map((row) => (
              <div
                key={row.id}
                className="mb-2 flex items-center justify-between gap-3 rounded-xl border border-line bg-island px-4 py-2.5"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm text-content">
                    {row.name || t('canvasList.untitled', 'Untitled Canvas')}
                  </div>
                  <div className="truncate text-xs text-content-4">
                    {row.project_name}
                    {row.deleted_at
                      ? ` · ${new Date(row.deleted_at).toLocaleDateString()}`
                      : ''}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  <button
                    aria-label={t('canvasList.restore', 'Restore')}
                    title={t('canvasList.restore', 'Restore')}
                    onClick={() => void handleRestore(row.id)}
                    className={`flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-xs text-content-2 hover:bg-island-2 ${FOCUS_RING}`}
                  >
                    <RotateCcw size={12} />
                    {t('canvasList.restore', 'Restore')}
                  </button>
                  <button
                    aria-label={
                      armedId === row.id
                        ? t('canvasList.confirmDelete', 'Confirm delete')
                        : t('canvasList.deleteForever', 'Delete Forever')
                    }
                    title={
                      armedId === row.id
                        ? t('canvasList.confirmDelete', 'Confirm delete')
                        : t('canvasList.deleteForever', 'Delete Forever')
                    }
                    onClick={() => void handlePurge(row.id)}
                    onBlur={() => setArmedId((v) => (v === row.id ? null : v))}
                    className={`flex items-center gap-1 rounded-lg px-2 py-1 text-xs ${FOCUS_RING} ${
                      armedId === row.id
                        ? 'bg-rose-600 text-white hover:bg-rose-700'
                        : 'border border-rose-500/40 text-rose-400 hover:bg-rose-500/10'
                    }`}
                  >
                    <Trash2 size={12} />
                    {armedId === row.id
                      ? t('canvasList.confirmDelete', 'Confirm delete')
                      : t('canvasList.deleteForever', 'Delete Forever')}
                  </button>
                </div>
              </div>
            ))}
        </div>
      )}
    </section>
  );
}
