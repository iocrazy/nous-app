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
  Library,
  Plus,
} from 'lucide-react';
import type { AILibraryAgent } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { useAuth } from '../../contexts/AuthContext';
import { useAgentRuns } from '../../hooks/useAgentRuns';
import { NewAgentModal } from '../AILibrary/NewAgentModal';
import { getAgentIcon } from '../AILibrary/agentIcons';

interface AILibrarySidebarProps {
  /** URL prefix for the current team context, e.g. "/team/:teamId". */
  urlPrefix: string;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

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

  const [agents, setAgents] = useState<AILibraryAgent[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showNewAgentModal, setShowNewAgentModal] = useState(false);

  const activeAgentSlug = (() => {
    const match = location.pathname.match(/\/ai-library\/agents\/([^/]+)/);
    return match ? match[1] : null;
  })();
  const skillsActive = /\/ai-library\/skills(\/|$)/.test(location.pathname);
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

  const handleAgentCreated = async (newSlug: string) => {
    setShowNewAgentModal(false);
    await loadAgents();
    navigate(`${urlPrefix}/ai-library/agents/${newSlug}`);
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

  return (
    <>
      <div className="group relative flex w-52 flex-col border-r border-ink-800/40 pt-16">
        {/* Header */}
        <div className="px-4 pt-4 pb-3">
          <span className="text-sm font-semibold text-ink-200">
            {t('sidebar.aiLibrary', 'AI Library')}
          </span>
        </div>

        {/* Agents */}
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
            ) : agents.length === 0 ? (
              <div className="px-3 py-1 text-[11px] text-ink-600">
                {t('aiLibrary.noAgents', 'No agents yet')}
              </div>
            ) : (
              agents.map((agent) => {
                const Icon = getAgentIcon(agent.icon);
                const active = activeAgentSlug === agent.slug;
                const isRunning = runningAgentIds.has(agent.id);
                const isPausedBudget = agent.paused_reason === 'budget';
                const isPausedManual = agent.paused_reason === 'manual';
                const pausedTitle = isPausedBudget
                  ? t(
                      'sidebar.agentPausedBudget',
                      'Paused: monthly budget exceeded',
                    )
                  : isPausedManual
                  ? t('sidebar.agentPausedManual', 'Paused by admin')
                  : null;

                const trailing = isRunning ? (
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

                return (
                  <NavItem
                    key={agent.slug}
                    icon={Icon}
                    label={agent.name}
                    active={active}
                    onClick={() =>
                      navigate(`${urlPrefix}/ai-library/agents/${agent.slug}`)
                    }
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
              })
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

        {/* Usage + Memory */}
        <div className="px-2 pb-3">
          <SectionLabel>{t('aiLibrary.usageSection', 'Usage')}</SectionLabel>
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
