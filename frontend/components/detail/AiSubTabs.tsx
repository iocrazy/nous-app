// frontend/components/detail/AiSubTabs.tsx
//
// The small tab row inside the detail inspector's AI tab:
//   Transcript | Summary | Visual
// Shared by VideoDetailPanel and ResourceDetailPage so both inspectors look
// and behave the same.

import React from 'react';
import { useTranslation } from 'react-i18next';

export type AiSubTab = 'transcript' | 'summary' | 'visual';

export const AI_SUB_TABS: readonly AiSubTab[] = ['transcript', 'summary', 'visual'];

type Translate = (key: string, def: string) => string;

/** Busiest of several AI statuses — processing > failed > completed > none.
 *  Drives the single status dot on the AI big tab. */
const STATUS_RANK: Record<string, number> = { processing: 3, failed: 2, completed: 1 };
export function busiestStatus(...statuses: (string | null | undefined)[]): string | undefined {
  return statuses.reduce<string | undefined>(
    (best, cur) =>
      (STATUS_RANK[cur ?? ''] ?? 0) > (STATUS_RANK[best ?? ''] ?? 0) ? (cur ?? undefined) : best,
    undefined,
  );
}

export function aiSubTabLabel(tab: AiSubTab, t: Translate): string {
  switch (tab) {
    case 'transcript':
      return t('detail.aiTabs.transcript', 'Transcript');
    case 'summary':
      return t('detail.aiTabs.summary', 'Summary');
    case 'visual':
      return t('detail.aiTabs.visual', 'Visual');
  }
}

export interface AiSubTabsProps {
  active: AiSubTab;
  onChange: (tab: AiSubTab) => void;
  /** Sub tabs to show, in display order. Defaults to all three. */
  tabs?: readonly AiSubTab[];
  /** Optional status dot per sub tab. */
  indicators?: Partial<Record<AiSubTab, React.ReactNode>>;
  className?: string;
}

export const AiSubTabs: React.FC<AiSubTabsProps> = ({
  active,
  onChange,
  tabs = AI_SUB_TABS,
  indicators,
  className = '',
}) => {
  const { t } = useTranslation();
  return (
    <div
      role="tablist"
      data-testid="ai-subtabs"
      className={`flex items-center gap-1 ${className}`}
    >
      {tabs.map((tab) => {
        const selected = tab === active;
        return (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(tab)}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
              selected
                ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                : 'text-content-2 hover:text-content'
            }`}
          >
            {aiSubTabLabel(tab, t)}
            {indicators?.[tab]}
          </button>
        );
      })}
    </div>
  );
};

export default AiSubTabs;
