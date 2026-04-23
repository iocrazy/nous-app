import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Library, Plus } from 'lucide-react';
import type { AILibraryAgent } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { NewAgentModal } from '../AILibrary/NewAgentModal';
import { getAgentIcon } from '../AILibrary/agentIcons';
import { SidebarSection } from './SidebarSection';

interface SidebarAgentsSectionProps {
  /** Team URL prefix ("/team/:teamId") or empty string for personal. */
  urlPrefix: string;
  /** Sidebar collapsed state — only icons render. */
  collapsed?: boolean;
}

/**
 * AI LIBRARY section for the main Sidebar.
 *
 * Self-contained: owns the agent list fetch, the "+" new-agent button, the
 * NewAgentModal, and navigation. Renders:
 *   1. Per-agent nav items (Bot icon + name) under the AI LIBRARY label
 *   2. A divider
 *   3. A Skills sub-link that lands on /skills
 *
 * Active highlight is URL-driven via `/agents/:slug` path match.
 */
export const SidebarAgentsSection: React.FC<SidebarAgentsSectionProps> = ({
  urlPrefix,
  collapsed = false,
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();

  const [agents, setAgents] = useState<AILibraryAgent[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [showNewAgentModal, setShowNewAgentModal] = useState(false);

  const activeAgentSlug = (() => {
    const match = location.pathname.match(/\/agents\/([^/]+)/);
    return match ? match[1] : null;
  })();
  const skillsActive = location.pathname.match(/\/skills(\/|$)/) !== null;

  const loadAgents = useCallback(async () => {
    try {
      const list = await aiLibraryService.listAgents();
      const sorted = [...list].sort((a, b) => a.name.localeCompare(b.name));
      setAgents(sorted);
    } catch (err) {
      console.error('[SidebarAgentsSection] listAgents failed:', err);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (cancelled) return;
      await loadAgents();
    })();
    return () => {
      cancelled = true;
    };
  }, [loadAgents]);

  const handleAgentCreated = async (newSlug: string) => {
    setShowNewAgentModal(false);
    await loadAgents();
    navigate(`${urlPrefix}/agents/${newSlug}`);
  };

  const newAgentButton = (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        setShowNewAgentModal(true);
      }}
      className="flex items-center justify-center h-5 w-5 rounded text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800/60 transition-colors"
      aria-label={t('sidebar.newAgent', 'New agent')}
      title={t('sidebar.newAgent', 'New agent')}
    >
      <Plus size={14} />
    </button>
  );

  return (
    <>
      <SidebarSection
        label={t('sidebar.aiLibrary')}
        hideLabel={collapsed}
        action={!collapsed ? newAgentButton : undefined}
      >
        {/* Agents list */}
        {!loaded ? (
          !collapsed && (
            <div className="px-3 py-1 text-[11px] text-zinc-600">Loading...</div>
          )
        ) : agents.length === 0 ? (
          !collapsed && (
            <div className="px-3 py-1 text-[11px] text-zinc-600">No agents yet</div>
          )
        ) : (
          agents.map((agent) => {
            const active = activeAgentSlug === agent.slug;
            const Icon = getAgentIcon(agent.icon);
            return (
              <button
                key={agent.slug}
                onClick={() => navigate(`${urlPrefix}/agents/${agent.slug}`)}
                title={collapsed ? agent.name : undefined}
                className={`w-full flex items-center ${collapsed ? 'justify-center px-0 py-2' : 'gap-3 px-3 py-2'} rounded-xl text-sm font-medium transition-colors group ${
                  active
                    ? 'bg-indigo-500/10 text-indigo-400'
                    : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50'
                }`}
              >
                <Icon
                  size={collapsed ? 20 : 18}
                  className={`flex-shrink-0 ${active ? 'text-indigo-400' : 'text-zinc-500 group-hover:text-zinc-300'}`}
                />
                {!collapsed && <span className="truncate">{agent.name}</span>}
              </button>
            );
          })
        )}

        {/* Skills sub-link */}
        {!collapsed && <div className="mx-3 my-1 border-t border-zinc-800/60" />}
        <button
          onClick={() => navigate(`${urlPrefix}/skills`)}
          title={collapsed ? t('sidebar.skills', 'Skills') : undefined}
          className={`w-full flex items-center ${collapsed ? 'justify-center px-0 py-2' : 'gap-3 px-3 py-2'} rounded-xl text-sm font-medium transition-colors group ${
            skillsActive
              ? 'bg-indigo-500/10 text-indigo-400'
              : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50'
          }`}
        >
          <Library
            size={collapsed ? 20 : 18}
            className={`flex-shrink-0 ${skillsActive ? 'text-indigo-400' : 'text-zinc-500 group-hover:text-zinc-300'}`}
          />
          {!collapsed && <span>{t('sidebar.skills')}</span>}
        </button>
      </SidebarSection>

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

export default SidebarAgentsSection;
