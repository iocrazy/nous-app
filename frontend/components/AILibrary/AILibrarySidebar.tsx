// frontend/components/AILibrary/AILibrarySidebar.tsx
// AI Library secondary sidebar — v2.1 IA (docs/superpowers design proposal):
// use-layer first (Dashboard / Runtime / Chat), manage-layer after
// (Agents with My-on-top + System collapsed, Skills), Insights last.
// Tasks deliberately have NO entry here — 待办事项 lives in the main nav.

import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangle,
  BarChart3,
  Brain,
  ChevronLeft,
  ChevronRight,
  Cpu,
  LayoutDashboard,
  Library,
  MessageSquare,
  MessagesSquare,
  Plus,
} from 'lucide-react';
import type { AILibraryAgent } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { useAuth } from '../../contexts/AuthContext';
import { useAgentRuns } from '../../hooks/useAgentRuns';
import { useGlobalChatStore } from '../../stores/globalChatStore';
import { NewAgentModal } from '../AILibrary/NewAgentModal';
import { getAgentIcon } from '../AILibrary/agentIcons';

interface AILibrarySidebarProps {
  /** URL prefix for the current team context, e.g. "/team/:teamId". */
  urlPrefix: string;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

const SYSTEM_COLLAPSED_KEY = 'aiLibrary.systemAgentsCollapsed';

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
    ? 'bg-indigo-500/8 text-indigo-300'
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
        className={`shrink-0 ${active ? 'text-indigo-300' : 'text-ink-500'}`}
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
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showNewAgentModal, setShowNewAgentModal] = useState(false);
  const [systemCollapsed, setSystemCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(SYSTEM_COLLAPSED_KEY) !== 'false';
    } catch {
      return true;
    }
  });

  const toggleSystemCollapsed = () => {
    setSystemCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(SYSTEM_COLLAPSED_KEY, String(next));
      } catch {
        /* ignore — collapse state is cosmetic */
      }
      return next;
    });
  };

  const activeAgentSlug = (() => {
    const match = location.pathname.match(/\/ai-library\/agents\/([^/]+)/);
    return match ? match[1] : null;
  })();
  const dashboardActive = /\/ai-library\/?$/.test(location.pathname);
  const skillsActive = /\/ai-library\/skills(\/|$)/.test(location.pathname);
  const sessionsActive = /\/ai-library\/sessions(\/|$)/.test(location.pathname);
  const usageActive = /\/ai-library\/usage(\/|$)/.test(location.pathname);
  const workforceActive = /\/ai-library\/workforce(\/|$)/.test(location.pathname);
  const memoryActive = /\/ai-library\/memory(\/|$)/.test(location.pathname);

  const loadAgents = useCallback(async () => {
    try {
      setLoadError(null);
      const list = await aiLibraryService.listAgents();
      const sorted = [...list].sort((a, b) => a.name.localeCompare(b.name));
      setAgents(sorted);
    } catch (err) {
      console.error('[AILibrarySidebar] listAgents failed:', err);
      setLoadError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoaded(true);
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

  const handleAgentCreated = async (newSlug: string) => {
    setShowNewAgentModal(false);
    await loadAgents();
    navigate(`${urlPrefix}/ai-library/agents/${newSlug}`);
  };

  const renderAgent = (agent: AILibraryAgent) => {
    const Icon = getAgentIcon(agent.icon);
    const active = activeAgentSlug === agent.slug;
    const isRunning = runningAgentIds.has(agent.id);
    const isPausedBudget = agent.paused_reason === 'budget';
    const isPausedManual = agent.paused_reason === 'manual';
    const pausedTitle = isPausedBudget
      ? t('sidebar.agentPausedBudget', 'Paused: monthly budget exceeded')
      : isPausedManual
      ? t('sidebar.agentPausedManual', 'Paused by admin')
      : null;

    const statusTrailing = isRunning ? (
      <span
        className="relative flex h-2 w-2 flex-shrink-0"
        title={t('sidebar.agentRunning', 'Running...')}
        aria-label={t('sidebar.agentRunning', 'Running...')}
      >
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
      </span>
    ) : isPausedBudget ? (
      <AlertTriangle
        size={12}
        className="flex-shrink-0 text-amber-500"
        aria-label={pausedTitle ?? undefined}
      />
    ) : isPausedManual ? (
      <AlertTriangle
        size={12}
        className="flex-shrink-0 text-ink-500"
        aria-label={pausedTitle ?? undefined}
      />
    ) : null;

    // Agent-overrides (mig 341): presets the caller (or their teams)
    // customized get a dot — reset lives in the editor.
    const customized = (agent.override_scopes?.length ?? 0) > 0;
    const customizedDot = customized ? (
      <span
        className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-indigo-400"
        title={t('aiLibrary.customized', 'Customized')}
        aria-label={t('aiLibrary.customized', 'Customized')}
      />
    ) : null;

    const trailing: React.ReactNode =
      customized || statusTrailing ? (
        <div className="flex items-center gap-1">
          {customizedDot}
          {statusTrailing}
        </div>
      ) : null;

    return (
      <NavItem
        key={agent.slug}
        icon={Icon}
        label={agent.name}
        active={active}
        onClick={() => navigate(`${urlPrefix}/ai-library/agents/${agent.slug}`)}
        trailing={trailing}
        title={
          pausedTitle
            ? `${agent.name} — ${pausedTitle}`
            : isRunning
            ? `${agent.name} (running)`
            : agent.name
        }
      />
    );
  };

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
  const myAgents = agents.filter((a) => !a.is_system_preset);
  const systemAgents = agents.filter((a) => a.is_system_preset);
  const systemHasAlert = systemAgents.some((a) => a.paused_reason);
  const liveCount = runningAgentIds.size;

  return (
    <>
      <div className={`group relative flex w-52 flex-col overflow-y-auto border-r border-ink-800/40`}>
        {/* Header */}
        <div className="px-4 pt-4 pb-3">
          <span className="text-sm font-semibold text-ink-200">
            {t('sidebar.aiLibrary', 'AI Library')}
          </span>
        </div>

        {/* Dashboard */}
        <div className="px-2">
          <NavItem
            icon={LayoutDashboard}
            label={t('sidebar.dashboard', 'Dashboard')}
            active={dashboardActive}
            onClick={() => navigate(`${urlPrefix}/ai-library`)}
            trailing={
              liveCount > 0 ? (
                <span
                  className="flex items-center gap-1 text-[10px] font-medium text-emerald-400"
                  title={t('sidebar.liveRuns', '{{count}} running', { count: liveCount })}
                >
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  {liveCount}
                </span>
              ) : undefined
            }
          />
        </div>

        {/* Divider */}
        <div className="mx-3 my-2 border-t border-ink-800/80" />

        {/* Runtime */}
        <div className="px-2">
          <SectionLabel>{t('aiLibrary.runtimeSection', 'Runtime')}</SectionLabel>
          <div className="mt-1 flex flex-col gap-0.5">
            <NavItem
              icon={Cpu}
              label={t('sidebar.workforce', 'Workforce')}
              active={workforceActive}
              onClick={() => navigate(`${urlPrefix}/ai-library/workforce`)}
            />
          </div>
        </div>

        {/* Divider */}
        <div className="mx-3 my-2 border-t border-ink-800/80" />

        {/* Chat — use-layer: sessions + chat-enabled agents (click = talk) */}
        <div className="px-2">
          <SectionLabel>{t('aiLibrary.chatSection', 'Chat')}</SectionLabel>
          <div className="mt-1 flex flex-col gap-0.5">
            <NavItem
              icon={MessageSquare}
              label={t('sidebar.sessions', 'Sessions')}
              active={sessionsActive}
              onClick={() => navigate(`${urlPrefix}/ai-library/sessions`)}
            />
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
                    <MessagesSquare size={12} className="flex-shrink-0 text-ink-600" />
                  }
                />
              );
            })}
          </div>
        </div>

        {/* Divider */}
        <div className="mx-3 my-2 border-t border-ink-800/80" />

        {/* Agents — manage-layer */}
        <div className="px-2">
          <div className="flex items-center justify-between px-2">
            <SectionLabel>{t('aiLibrary.agentsSection', 'Agents')}</SectionLabel>
            <button
              type="button"
              onClick={() => setShowNewAgentModal(true)}
              className="flex h-5 w-5 items-center justify-center rounded text-ink-500 hover:text-ink-200 hover:bg-ink-800/60 transition-colors"
              aria-label={t('sidebar.newAgent', 'New agent')}
              title={t('sidebar.newAgent', 'New agent')}
            >
              <Plus size={13} />
            </button>
          </div>
          <div className="mt-1 flex flex-col gap-0.5">
            {!loaded ? (
              <div className="px-3 py-1 text-[11px] text-ink-600">
                {t('aiLibrary.loadingAgents', 'Loading...')}
              </div>
            ) : loadError ? (
              <div className="px-2 py-1 space-y-1">
                <div className="text-[11px] text-red-400 truncate" title={loadError}>
                  {t('aiLibrary.loadAgentsError', 'Failed to load')}
                </div>
                <button
                  type="button"
                  onClick={() => void loadAgents()}
                  className="text-[11px] text-indigo-400 hover:text-indigo-300"
                >
                  {t('aiLibrary.retry', 'Retry')}
                </button>
              </div>
            ) : (
              <>
                {myAgents.length === 0 ? (
                  <div className="px-3 py-1 text-[11px] text-ink-600">
                    {t('aiLibrary.noMyAgents', 'No custom agents yet')}
                  </div>
                ) : (
                  myAgents.map(renderAgent)
                )}

                {systemAgents.length > 0 && (
                  <>
                    <button
                      type="button"
                      onClick={toggleSystemCollapsed}
                      className="flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-[12px] text-ink-500 hover:text-ink-300 hover:bg-ink-800/40 transition-colors"
                      aria-expanded={!systemCollapsed}
                    >
                      <span className="text-[9px]">{systemCollapsed ? '▸' : '▾'}</span>
                      <span className="flex-1 text-left">
                        {t('aiLibrary.systemAgents', 'System')}
                        <span className="ml-1 text-ink-600">({systemAgents.length})</span>
                      </span>
                      {systemCollapsed && systemHasAlert && (
                        <span
                          className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-amber-500"
                          title={t('aiLibrary.systemAgentsAlert', 'A system agent is paused')}
                        />
                      )}
                    </button>
                    {!systemCollapsed && systemAgents.map(renderAgent)}
                  </>
                )}
              </>
            )}
          </div>
        </div>

        {/* Divider */}
        <div className="mx-3 my-2 border-t border-ink-800/80" />

        {/* Skills */}
        <div className="px-2">
          <SectionLabel>{t('aiLibrary.skillsSection', 'Skills')}</SectionLabel>
          <div className="mt-1 flex flex-col gap-0.5">
            <NavItem
              icon={Library}
              label={t('sidebar.skills', 'Skills')}
              active={skillsActive}
              onClick={() => navigate(`${urlPrefix}/ai-library/skills`)}
            />
          </div>
        </div>

        {/* Divider */}
        <div className="mx-3 my-2 border-t border-ink-800/80" />

        {/* Insights */}
        <div className="px-2 pb-3">
          <SectionLabel>{t('aiLibrary.insightsSection', 'Insights')}</SectionLabel>
          <div className="mt-1 flex flex-col gap-0.5">
            <NavItem
              icon={BarChart3}
              label={t('sidebar.aiUsage', 'AI Usage')}
              active={usageActive}
              onClick={() => navigate(`${urlPrefix}/ai-library/usage`)}
            />
            <NavItem
              icon={Brain}
              label={t('sidebar.memory', 'My Memory')}
              active={memoryActive}
              onClick={() => navigate(`${urlPrefix}/ai-library/memory`)}
            />
          </div>
        </div>

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

      {showNewAgentModal && (
        <NewAgentModal
          existingAgents={agents}
          onClose={() => setShowNewAgentModal(false)}
          onCreated={handleAgentCreated}
        />
      )}
    </>
  );
};

export default AILibrarySidebar;
