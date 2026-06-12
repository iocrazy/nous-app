import React from 'react';
import { useTranslation } from 'react-i18next';
import type { TaskCounts } from './taskCenterSummary';

export type TaskTab = 'active' | 'history';

interface TaskCenterStatusBarProps {
  counts: TaskCounts;
  tab: TaskTab;
  onTab: (tab: TaskTab) => void;
  /** Active = running + queued; History = completed + failed. */
  activeTotal: number;
  historyTotal: number;
}

// One colored dot + count + label, e.g. "● 2 Running".
const StatChip: React.FC<{ color: string; count: number; label: string }> = ({
  color,
  count,
  label,
}) => (
  <span className="flex items-center gap-1 text-[11px] text-ink-400">
    <span className={`w-1.5 h-1.5 rounded-full ${color}`} />
    <span className="font-medium text-ink-300">{count}</span>
    <span>{label}</span>
  </span>
);

const TabButton: React.FC<{
  active: boolean;
  onClick: () => void;
  label: string;
  count: number;
}> = ({ active, onClick, label, count }) => (
  <button
    onClick={onClick}
    className={`relative px-1 pb-2 text-xs font-medium transition-colors ${
      active ? 'text-ink-100' : 'text-ink-500 hover:text-ink-300'
    }`}
  >
    {label}
    {count > 0 && (
      <span className="ml-1.5 text-[10px] text-ink-500">{count}</span>
    )}
    {active && (
      <span className="absolute -bottom-px left-0 right-0 h-0.5 bg-indigo-400 rounded-full" />
    )}
  </button>
);

/**
 * Task Center sub-header: Active / History tabs + a running / queued / completed
 * summary row, so the whole task pipeline's execution state (downloads, parses,
 * AI/agent runs) is visible at a glance — matching the global task panel design.
 */
export const TaskCenterStatusBar: React.FC<TaskCenterStatusBarProps> = ({
  counts,
  tab,
  onTab,
  activeTotal,
  historyTotal,
}) => {
  const { t } = useTranslation();
  return (
    <div className="px-4 pt-2 border-b border-ink-800">
      {/* Tabs */}
      <div className="flex items-center gap-4">
        <TabButton
          active={tab === 'active'}
          onClick={() => onTab('active')}
          label={t('topbar.tabActive')}
          count={activeTotal}
        />
        <TabButton
          active={tab === 'history'}
          onClick={() => onTab('history')}
          label={t('topbar.tabHistory')}
          count={historyTotal}
        />
      </div>
      {/* Status summary */}
      <div className="flex items-center gap-3 py-2">
        <StatChip color="bg-emerald-500" count={counts.running} label={t('topbar.running')} />
        <StatChip color="bg-amber-500" count={counts.queued} label={t('topbar.queued')} />
        <StatChip color="bg-ink-500" count={counts.completed} label={t('topbar.completed')} />
        {counts.failed > 0 && (
          <StatChip color="bg-red-500" count={counts.failed} label={t('topbar.failed')} />
        )}
      </div>
    </div>
  );
};
