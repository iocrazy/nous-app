/**
 * RecentItemsList — the Projects "Recent" view body: recently-edited scripts
 * and canvases across the caller's projects (newest first), driven by
 * `GET /api/v1/projects/recent-items`. Each row shows a kind icon
 * (FileText = script / Frame = canvas), the item name, its owning project,
 * and a relative timestamp. Clicking a row calls `onSelect(item)` — the page
 * owns navigation (script → project workspace ?module=script, canvas → the
 * standalone canvas editor).
 */
import { useTranslation } from 'react-i18next';
import { FileText, Frame, Clock } from 'lucide-react';
import type { RecentItem } from '../../types';
import { formatRelativeTime } from '../../utils/relativeTime';

interface RecentItemsListProps {
  items: RecentItem[];
  onSelect: (item: RecentItem) => void;
}

export function RecentItemsList({ items, onSelect }: RecentItemsListProps) {
  const { t } = useTranslation();

  if (items.length === 0) {
    return (
      <div
        className="flex flex-col items-center justify-center py-20 text-center"
        data-testid="recent-empty"
      >
        <div className="p-4 bg-ink-800 rounded-2xl mb-4">
          <Clock size={40} className="text-ink-500" />
        </div>
        <h3 className="text-lg font-medium text-ink-300">
          {t('projects.recentEmpty', 'No recent items')}
        </h3>
      </div>
    );
  }

  return (
    <div
      className="border border-ink-700/50 rounded-xl overflow-hidden"
      data-testid="recent-list"
    >
      {items.map((item) => {
        const Icon = item.kind === 'canvas' ? Frame : FileText;
        return (
          <button
            key={`${item.kind}-${item.id}`}
            data-testid="recent-row"
            onClick={() => onSelect(item)}
            className="flex w-full items-center gap-3.5 px-4 py-3 border-t border-ink-700/50 first:border-t-0
                       bg-ink-800/40 hover:bg-ink-800/70 transition-colors text-left"
          >
            <Icon size={18} className="shrink-0 text-indigo-300" data-testid="recent-icon" />
            <span className="text-sm font-medium text-ink-50 truncate min-w-0 flex-1">
              {item.name}
            </span>
            <span className="text-xs text-ink-400 truncate max-w-[40%]" data-testid="recent-project">
              {item.project_name}
            </span>
            {item.updated_at && (
              <span className="text-[11px] text-ink-500 shrink-0">
                {formatRelativeTime(item.updated_at, t)}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export default RecentItemsList;
