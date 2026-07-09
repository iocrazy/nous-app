/**
 * StageHistoryDrawer — right-side drawer listing a project's stage
 * transitions (Phase B B2). Fetches on open, newest first (server-ordered
 * DESC); the most recent entry with no ``exited_at`` is the current stage.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';
import { fetchStageHistory } from '../../services/projectsService';
import { formatRelativeTime } from '../../utils/relativeTime';
import type { StageHistoryEntry } from '../../types';

interface StageHistoryDrawerProps {
  projectId: string;
  open: boolean;
  onClose: () => void;
}

export function StageHistoryDrawer({ projectId, open, onClose }: StageHistoryDrawerProps) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<StageHistoryEntry[] | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetchStageHistory(projectId)
      .then((fetched) => {
        if (!cancelled) setEntries(fetched);
      })
      .catch((err) => {
        console.error('[StageHistoryDrawer] failed to load stage history:', err);
        if (!cancelled) setEntries([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open, projectId]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" data-testid="stage-history-drawer">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative w-80 max-w-full h-full bg-ink-900 border-l border-ink-800 p-5 overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-ink-100 font-semibold">{t('projects.workbench.stageHistory')}</h3>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-ink-800 text-ink-400"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>
        {entries && entries.length === 0 && (
          <p className="text-sm text-ink-500">{t('projects.workbench.historyEmpty')}</p>
        )}
        <ol className="flex flex-col gap-3">
          {(entries ?? []).map((entry) => (
            <li key={entry.id} className="flex gap-3">
              <span
                className={`mt-1 w-2 h-2 rounded-full flex-shrink-0 ${
                  entry.exited_at ? 'bg-indigo-500' : 'bg-emerald-400'
                }`}
              />
              <div className="min-w-0">
                <div className="text-sm text-ink-200 font-medium">{entry.stage_name}</div>
                <div className="text-xs text-ink-500">
                  {formatRelativeTime(entry.entered_at, t)}
                  {!entry.exited_at && ` · ${t('projects.workbench.historyCurrent')}`}
                </div>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

export default StageHistoryDrawer;
