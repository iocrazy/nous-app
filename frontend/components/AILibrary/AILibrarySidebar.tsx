// frontend/components/AILibrary/AILibrarySidebar.tsx
// AI Library secondary sidebar — B0 (spec 2026-08-02 §B0).
//
// Was ~30 rows: every agent and the skills tree flattened one-per-line. Those
// carry their own navigation now (the gallery's cards), so the sidebar holds
// only the six high-frequency entries declared in sidebarNav.ts, plus the
// chat-enabled agent shortcuts.
// Tasks deliberately have NO entry here — 待办事项 lives in the main nav.

import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  BarChart3,
  Brain,
  ChevronLeft,
  ChevronRight,
  Cpu,
  Library,
  MessageSquare,
  MessagesSquare,
  Wallet,
} from 'lucide-react';
import type { AILibraryAgent } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { useAuth } from '../../contexts/AuthContext';
import { useAgentRuns } from '../../hooks/useAgentRuns';
import { useGlobalChatStore } from '../../stores/globalChatStore';
import { getAgentIcon } from '../AILibrary/agentIcons';
import { AI_LIBRARY_NAV, activeNavKey } from './sidebarNav';
import { SecondarySidebarHeader } from '../layout/SecondarySidebarHeader';

interface AILibrarySidebarProps {
  /** URL prefix for the current team context, e.g. "/team/:teamId". */
  urlPrefix: string;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

/** Icon per nav key — kept here so sidebarNav.ts stays free of React imports. */
const NAV_ICONS: Record<string, React.ElementType> = {
  library: Library,
  workforce: Cpu,
  sessions: MessageSquare,
  usage: BarChart3,
  aiCost: Wallet,
  memory: Brain,
};

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="px-2 text-[11px] font-medium uppercase tracking-wider text-ink-600">
      {children}
    </span>
  );
}

interface NavItemProps {
  icon: React.ElementType;
  label: string;
  active: boolean;
  onClick: () => void;
  trailing?: React.ReactNode;
  title?: string;
}

function NavItem({ icon: Icon, label, active, onClick, trailing, title }: NavItemProps) {
  const style = active
    ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
    : 'text-ink-400 hover:text-ink-200 hover:bg-ink-800/40';
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={`flex w-full items-center gap-3 rounded-md px-3 py-2 text-[13px] transition-colors ${style}`}
    >
      <Icon
        size={16}
        className={`shrink-0 ${active ? 'text-[var(--accent-text)]' : 'text-ink-500'}`}
      />
      <span className="flex-1 truncate text-left">{label}</span>
      {trailing}
    </button>
  );
}

export const AILibrarySidebar: React.FC<AILibrarySidebarProps> = ({
  urlPrefix,
  collapsed,
  onToggleCollapse,
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const { currentUserId } = useAuth();
  const { runningAgentIds } = useAgentRuns(currentUserId);
  const requestChat = useGlobalChatStore((s) => s.requestChat);

  const [agents, setAgents] = useState<AILibraryAgent[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const activeKey = activeNavKey(location.pathname);

  const loadAgents = useCallback(async () => {
    try {
      setLoadError(null);
      const list = await aiLibraryService.listAgents();
      const sorted = [...list].sort((a, b) => a.name.localeCompare(b.name));
      setAgents(sorted);
    } catch (err) {
      console.error('[AILibrarySidebar] listAgents failed:', err);
      setLoadError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void loadAgents();
  }, [loadAgents]);

  // Editor-side mutations (delete today; anything tomorrow) announce
  // themselves via this event — the sidebar owns its agents list and
  // there is no shared store to invalidate.
  useEffect(() => {
    const onChanged = () => void loadAgents();
    window.addEventListener('ai-library:agents-changed', onChanged);
    return () => window.removeEventListener('ai-library:agents-changed', onChanged);
  }, [loadAgents]);

  if (collapsed) {
    return (
      <div className="relative w-4 flex-shrink-0">
        <button
          type="button"
          onClick={onToggleCollapse}
          className="absolute top-1/2 -translate-y-1/2 left-0 z-10 w-4 h-10 flex items-center justify-center rounded-r-md bg-ink-800/80 text-ink-500 hover:text-ink-200 hover:bg-ink-700 transition-colors"
          title={t('aiLibrary.expandSidebar', 'Expand sidebar')}
        >
          <ChevronRight size={12} />
        </button>
      </div>
    );
  }

  const chatAgents = agents.filter((a) => a.chat_permissions?.enabled);
  const liveCount = runningAgentIds.size;

  return (
    <>
      <div className="group relative flex w-52 flex-col overflow-y-auto border-r border-ink-800/40">
        <SecondarySidebarHeader title={t('sidebar.aiLibrary', 'AI Library')} />
        {AI_LIBRARY_NAV.map((section) => (
          <React.Fragment key={section.key}>
            {section.key !== 'library' && (
              <div className="mx-3 my-2 border-t border-ink-800/80" />
            )}
            <div className={`px-2 ${section.key === 'insights' ? 'pb-3' : ''}`}>
              {section.labelDefault && (
                <SectionLabel>{t(section.labelKey!, section.labelDefault)}</SectionLabel>
              )}
              <div className={`flex flex-col gap-0.5 ${section.labelDefault ? 'mt-1' : ''}`}>
                {section.items.map((item) => (
                  <NavItem
                    key={item.key}
                    icon={NAV_ICONS[item.key]}
                    label={t(item.labelKey, item.labelDefault)}
                    active={activeKey === item.key}
                    onClick={() => navigate(`${urlPrefix}${item.path}`)}
                    trailing={
                      item.key === 'library' && liveCount > 0 ? (
                        <span
                          className="flex items-center gap-1 text-[10px] font-medium text-agent"
                          title={t('sidebar.liveRuns', '{{count}} running', {
                            count: liveCount,
                          })}
                        >
                          <span className="h-1.5 w-1.5 rounded-full bg-agent" />
                          {liveCount}
                        </span>
                      ) : undefined
                    }
                  />
                ))}

                {/* Chat shortcuts — click talks to the agent instead of
                    navigating. Only chat-enabled agents appear, which is what
                    keeps this from regrowing into the old 19-row flatten. */}
                {section.key === 'chat' && (
                  <>
                    {chatAgents.map((agent) => {
                      const Icon = getAgentIcon(agent.icon);
                      return (
                        <NavItem
                          key={`chat-${agent.slug}`}
                          icon={Icon}
                          label={agent.name}
                          active={false}
                          onClick={() => requestChat(agent.slug)}
                          title={t('aiLibrary.openChatWith', 'Chat with {{name}}', {
                            name: agent.name,
                          })}
                          trailing={
                            <MessagesSquare
                              size={12}
                              className="flex-shrink-0 text-ink-600"
                            />
                          }
                        />
                      );
                    })}
                    {loadError && (
                      <div className="px-2 py-1 space-y-1">
                        <div
                          className="truncate text-[11px] text-danger"
                          title={loadError}
                        >
                          {t('aiLibrary.loadAgentsError', 'Failed to load')}
                        </div>
                        <button
                          type="button"
                          onClick={() => void loadAgents()}
                          className="text-[11px] text-[var(--accent-text)]"
                        >
                          {t('aiLibrary.retry', 'Retry')}
                        </button>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          </React.Fragment>
        ))}

        {/* Collapse toggle — right edge, mid-height */}
        <button
          type="button"
          onClick={onToggleCollapse}
          className="absolute top-1/2 -translate-y-1/2 right-0 z-10 w-4 h-10 flex items-center justify-center rounded-l-md bg-ink-800/80 text-ink-500 hover:text-ink-200 hover:bg-ink-700 transition-colors opacity-0 group-hover:opacity-100"
          title={t('aiLibrary.collapseSidebar', 'Collapse sidebar')}
        >
          <ChevronLeft size={12} />
        </button>
      </div>
    </>
  );
};

export default AILibrarySidebar;
