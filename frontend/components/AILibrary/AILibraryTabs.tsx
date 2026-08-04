// frontend/components/AILibrary/AILibraryTabs.tsx
// The Agents | Skills | Marketplace strip that sits at the top of both
// gallery pages.
//
// It used to live inline in AgentGalleryPage only, so the skills gallery —
// which you reach by clicking the Skills tab — had no strip at all and no way
// back except the browser button. Sharing it makes the two pages one surface
// with two states.
//
// There is deliberately no page title above this strip: the secondary sidebar
// already names the module, and repeating "AI Library" in the content area
// says nothing the user cannot already see.

import React from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

export type AILibraryTabKey = 'agents' | 'skills';

export interface AILibraryTabsProps {
  active: AILibraryTabKey;
  agentCount?: number | null;
  skillCount?: number | null;
  /** Right-aligned slot — each page's primary "New …" button. */
  actions?: React.ReactNode;
  /**
   * Where this strip is mounted, which decides whether the ACTIVE tab is
   * clickable:
   *
   *   'gallery' (default) — you are already on that gallery, so the active
   *      tab is inert; clicking it would be a no-op reload.
   *   'detail' — you are inside an agent/skill editor. The active tab is the
   *      way back OUT to its gallery, so it stays a live link and `active`
   *      only means "highlight this one". Making it inert here stranded
   *      users in the editor with no route back.
   */
  placement?: 'gallery' | 'detail';
}

const ACTIVE_CLS = 'border-b-2 border-[var(--accent-text)] px-3 pb-2 font-medium text-ink-100';
const IDLE_CLS = 'px-3 pb-2 text-ink-500 hover:text-ink-300';

export const AILibraryTabs: React.FC<AILibraryTabsProps> = ({
  active,
  agentCount,
  skillCount,
  actions,
  placement = 'gallery',
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { teamId } = useParams();
  const urlPrefix = teamId ? `/team/${teamId}` : '';

  const count = (n: number | null | undefined) =>
    n != null ? <span className="text-ink-600">{n}</span> : null;

  /** A tab is inert only when it is active AND we are on its own gallery. */
  const tab = (key: AILibraryTabKey, to: string, label: string, n?: number | null) => {
    const isActive = active === key;
    const body = (
      <>
        {label} {count(n)}
      </>
    );
    if (isActive && placement === 'gallery') {
      return <span className={ACTIVE_CLS}>{body}</span>;
    }
    return (
      <button
        type="button"
        onClick={() => navigate(to)}
        className={isActive ? ACTIVE_CLS : IDLE_CLS}
      >
        {body}
      </button>
    );
  };

  return (
    <div className="flex items-center gap-1 border-b border-ink-800/60 text-[13px]">
      {tab(
        'agents',
        `${urlPrefix}/ai-library`,
        t('aiLibrary.tab.agents', 'Agents'),
        agentCount,
      )}
      {tab(
        'skills',
        `${urlPrefix}/ai-library/skills`,
        t('aiLibrary.tab.skills', 'Skills'),
        skillCount,
      )}

      {/* Marketplace is a placeholder slot, deliberately inert. */}
      <span
        className="cursor-not-allowed px-3 pb-2 text-ink-700"
        title={t('aiLibrary.tab.marketPlanned', 'Planned')}
      >
        {t('aiLibrary.tab.market', 'Marketplace')}{' '}
        <span className="text-[10px]">({t('aiLibrary.tab.marketPlanned', 'Planned')})</span>
      </span>

      {actions && <div className="ml-auto pb-2">{actions}</div>}
    </div>
  );
};

export default AILibraryTabs;
