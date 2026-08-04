// frontend/components/AILibrary/AgentGalleryPage.tsx
// B1 — the AI Library landing page (spec 2026-08-02 §B1).
//
// Replaces the flat 19-row agent list with a roster: agents grouped by
// department, each card carrying its status, what it did this week, and the
// one button matching what it needs from you right now. The status decision
// itself lives in agentStatus.ts — this file only renders it.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Plus, Search } from 'lucide-react';
import type { AILibraryAgent } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import type { AgentStatsItem } from '../../services/aiLibraryService';
import { useAuth } from '../../contexts/AuthContext';
import { useAgentRuns } from '../../hooks/useAgentRuns';
import { useGlobalChatStore } from '../../stores/globalChatStore';
import { getAgentIcon } from './agentIcons';
import { NewAgentModal } from './NewAgentModal';
import { PageHeader } from '../layout/PageHeader';
import {
  AGENT_GROUP_ORDER,
  agentGroupOf,
  deriveAgentStatus,
  primaryAction,
  type AgentDerivedStatus,
  type AgentGroup,
} from './agentStatus';

const GROUP_META: Record<AgentGroup, { emoji: string; labelKey: string; label: string }> = {
  writing: { emoji: '✍️', labelKey: 'aiLibrary.group.writing', label: 'Writing' },
  art: { emoji: '🎨', labelKey: 'aiLibrary.group.art', label: 'Art' },
  tools: { emoji: '🔧', labelKey: 'aiLibrary.group.tools', label: 'Tools' },
};

/** Avatar tint per group — semantic tokens, never raw hues (K1 palette). */
const GROUP_AVATAR: Record<AgentGroup, string> = {
  writing: 'bg-agent-soft text-agent',
  art: 'bg-info-soft text-info',
  tools: 'bg-ok-soft text-ok',
};

const fmtTokens = (n: number): string =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1_000 ? `${Math.round(n / 1_000)}k` : String(n);

function StatusBadge({ status }: { status: AgentDerivedStatus }) {
  const { t } = useTranslation();
  if (status.kind === 'idle') {
    return (
      <span
        className="inline-flex items-center gap-1 text-[11px] text-ink-500"
        data-testid="status-badge"
        data-status="idle"
      >
        <span className="h-1.5 w-1.5 rounded-full bg-ink-600" />
        {t('aiLibrary.status.idle', 'Idle')}
      </span>
    );
  }
  const style =
    status.kind === 'running'
      ? 'border-agent-line bg-agent-soft text-agent'
      : status.kind === 'needs_reply'
        ? 'border-warn-line bg-warn-soft text-warn'
        : 'border-danger-line bg-danger-soft text-danger';
  const label =
    status.kind === 'running'
      ? t('aiLibrary.status.running', 'Running')
      : status.kind === 'needs_reply'
        ? t('aiLibrary.status.needsReply', 'Waiting for reply')
        : t('aiLibrary.status.fault', 'Fault');
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${style}`}
      data-testid="status-badge"
      data-status={status.kind}
    >
      {label}
      {status.kind === 'needs_reply' && ` · ${status.count}`}
    </span>
  );
}

function AgentCard({
  agent,
  stats,
  status,
  urlPrefix,
  onChat,
}: {
  agent: AILibraryAgent;
  stats: AgentStatsItem | undefined;
  status: AgentDerivedStatus;
  urlPrefix: string;
  onChat: (slug: string) => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const Icon = getAgentIcon(agent.icon);
  const group = agentGroupOf(agent);
  const action = primaryAction(status);

  const openEditor = (tab?: string) =>
    navigate(
      `${urlPrefix}/ai-library/agents/${agent.slug}${tab ? `?tab=${tab}` : ''}`,
    );

  const primary: { label: string; onClick: () => void } = {
    chat: {
      label: t('aiLibrary.action.chat', 'Chat'),
      onClick: () => onChat(agent.slug),
    },
    viewRuns: {
      label: t('aiLibrary.action.viewRuns', 'View run'),
      onClick: () => openEditor('workbench'),
    },
    goReply: {
      label: t('aiLibrary.action.goReply', 'Go reply'),
      onClick: () => openEditor('workbench'),
    },
    fixGuide: {
      label: t('aiLibrary.action.fixGuide', 'How to fix'),
      onClick: () => openEditor('profile'),
    },
  }[action.key];

  const scopeBadge = agent.team_name
    ? t('aiLibrary.scope.team', 'Team')
    : agent.project_name
      ? t('aiLibrary.scope.project', 'Project')
      : t('aiLibrary.scope.private', 'Private');

  return (
    <div
      className="group/card rounded-xl border border-ink-800/60 bg-ink-900/20 p-3 transition-colors hover:border-ink-700"
      data-testid="agent-card"
      data-slug={agent.slug}
    >
      <div className="flex items-start gap-3">
        <span
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${GROUP_AVATAR[group]}`}
        >
          <Icon size={17} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => openEditor()}
              className="min-w-0 truncate text-[13px] font-medium text-ink-200 hover:text-ink-100"
            >
              {agent.name}
            </button>
            <StatusBadge status={status} />
          </div>
          <div className="mt-0.5 truncate text-[11px] text-ink-600">
            {agent.model} · {fmtTokens(agent.max_tokens)}
          </div>
        </div>
      </div>

      {/* Provenance — "official template" replaces the old "system preset"
          wording: the override model already means template + customization. */}
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-ink-600">
        <span className="rounded border border-ink-800 px-1.5 py-0.5">
          {agent.is_system_preset
            ? t('aiLibrary.officialTemplate', 'Official template')
            : t('aiLibrary.customAgent', 'Custom')}
        </span>
        <span className="rounded border border-ink-800 px-1.5 py-0.5">
          {t('aiLibrary.skillCount', '{{count}} skills', {
            count: agent.skill_ids?.length ?? 0,
          })}
        </span>
        <span className="rounded border border-ink-800 px-1.5 py-0.5">{scopeBadge}</span>
      </div>

      {/* This week */}
      <div className="mt-2 text-[11px] text-ink-500 tabular-nums">
        {stats && (stats.runs_7d > 0 || stats.tokens_7d > 0)
          ? t('aiLibrary.weekMeta', '{{runs}} runs · {{tokens}} tok · 7d', {
              runs: stats.runs_7d,
              tokens: fmtTokens(stats.tokens_7d),
            })
          : t('aiLibrary.weekMetaEmpty', 'No runs · 7d')}
      </div>

      {/* A fault badge without a next step is what this redesign removed. */}
      {status.kind === 'fault' && (
        <div
          className="mt-2 flex items-start gap-1.5 rounded-md border border-danger-line bg-danger-soft px-2 py-1 text-[11px] text-danger"
          data-testid="fault-warnline"
        >
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          <span>{status.detail}</span>
        </div>
      )}

      <div className="mt-2.5 flex items-center gap-2">
        <button
          type="button"
          onClick={primary.onClick}
          data-testid="primary-action"
          data-action={action.key}
          className="rounded-md border border-ink-700 px-2 py-1 text-[11px] text-ink-200 hover:bg-ink-800/60"
        >
          {primary.label}
        </button>
        <button
          type="button"
          onClick={() => openEditor('persona')}
          className="rounded-md px-2 py-1 text-[11px] text-ink-500 hover:text-ink-300"
        >
          {agent.is_system_preset
            ? t('aiLibrary.action.fork', 'Fork & customize')
            : t('aiLibrary.action.configure', 'Configure')}
        </button>
        <button
          type="button"
          onClick={() => openEditor('workbench')}
          className="rounded-md px-2 py-1 text-[11px] text-ink-500 hover:text-ink-300"
        >
          {t('aiLibrary.action.runs', 'Runs')}
        </button>
      </div>
    </div>
  );
}

export const AgentGalleryPage: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  // Same derivation as AILibraryLayout — the router renders this as a bare
  // index element, so it reads the team context itself.
  const { teamId } = useParams();
  const urlPrefix = teamId ? `/team/${teamId}` : '';
  const { currentUserId } = useAuth();
  const { runningAgentIds } = useAgentRuns(currentUserId);
  const requestChat = useGlobalChatStore((s) => s.requestChat);

  const [agents, setAgents] = useState<AILibraryAgent[]>([]);
  const [stats, setStats] = useState<Record<string, AgentStatsItem>>({});
  const [skillCount, setSkillCount] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [showNewAgent, setShowNewAgent] = useState(false);

  const [query, setQuery] = useState('');
  const [groupFilter, setGroupFilter] = useState<AgentGroup | 'all'>('all');
  const [faultsOnly, setFaultsOnly] = useState(false);

  const loadAgents = useCallback(async () => {
    try {
      setLoadError(null);
      const list = await aiLibraryService.listAgents();
      setAgents([...list].sort((a, b) => a.name.localeCompare(b.name)));
    } catch (err) {
      console.error('[AgentGalleryPage] listAgents failed:', err);
      setLoadError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const loadStats = useCallback(async () => {
    try {
      setStatsError(null);
      setStats(await aiLibraryService.getAgentStats(7));
    } catch (err) {
      // Non-fatal: cards still render from the agent rows themselves, so we
      // degrade to "no weekly numbers" rather than blanking the roster —
      // but we say so, instead of showing zeros that look like real data.
      console.error('[AgentGalleryPage] getAgentStats failed:', err);
      setStatsError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void loadAgents();
    void loadStats();
    void (async () => {
      try {
        setSkillCount((await aiLibraryService.listSkills()).length);
      } catch (err) {
        console.error('[AgentGalleryPage] listSkills failed:', err);
      }
    })();
  }, [loadAgents, loadStats]);

  // The sidebar used to own the agents list and this event; the gallery
  // inherits it now that agents live here (B0).
  useEffect(() => {
    const onChanged = () => {
      void loadAgents();
      void loadStats();
    };
    window.addEventListener('ai-library:agents-changed', onChanged);
    return () => window.removeEventListener('ai-library:agents-changed', onChanged);
  }, [loadAgents, loadStats]);

  const statusById = useMemo(() => {
    const map = new Map<string, AgentDerivedStatus>();
    for (const a of agents) {
      map.set(a.id, deriveAgentStatus(a, stats[a.id], runningAgentIds));
    }
    return map;
  }, [agents, stats, runningAgentIds]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return agents.filter((a) => {
      if (q && !`${a.name} ${a.slug}`.toLowerCase().includes(q)) return false;
      if (groupFilter !== 'all' && agentGroupOf(a) !== groupFilter) return false;
      if (faultsOnly && statusById.get(a.id)?.kind !== 'fault') return false;
      return true;
    });
  }, [agents, query, groupFilter, faultsOnly, statusById]);

  const grouped = useMemo(() => {
    const out = new Map<AgentGroup, AILibraryAgent[]>();
    for (const g of AGENT_GROUP_ORDER) out.set(g, []);
    for (const a of visible) out.get(agentGroupOf(a))!.push(a);
    return out;
  }, [visible]);

  const faultCount = agents.filter(
    (a) => statusById.get(a.id)?.kind === 'fault',
  ).length;
  const liveAgent = agents.find((a) => runningAgentIds.has(a.id));

  const handleCreated = async (slug: string) => {
    setShowNewAgent(false);
    await loadAgents();
    navigate(`${urlPrefix}/ai-library/agents/${slug}`);
  };

  return (
    <div className="max-w-5xl pt-6 pb-12">
      <PageHeader
        title={t('sidebar.aiLibrary', 'AI Library')}
        actions={
          <button
            type="button"
            onClick={() => setShowNewAgent(true)}
            className="flex items-center gap-1.5 rounded-md border border-ink-700 px-2.5 py-1.5 text-[12px] text-ink-200 hover:bg-ink-800/60"
          >
            <Plus size={13} />
            {t('aiLibrary.newAgent', 'New Agent')}
          </button>
        }
        /* Marketplace is a placeholder slot, deliberately inert. */
        tabs={
          <>
            <span className="border-b-2 border-[var(--accent-text)] px-3 pb-2 font-medium text-ink-100">
              {t('aiLibrary.tab.agents', 'Agents')}{' '}
              <span className="text-ink-600">{agents.length}</span>
            </span>
            <button
              type="button"
              onClick={() => navigate(`${urlPrefix}/ai-library/skills`)}
              className="px-3 pb-2 text-ink-500 hover:text-ink-300"
            >
              {t('aiLibrary.tab.skills', 'Skills')}{' '}
              {skillCount != null && <span className="text-ink-600">{skillCount}</span>}
            </button>
            <span
              className="cursor-not-allowed px-3 pb-2 text-ink-700"
              title={t('aiLibrary.tab.marketPlanned', 'Planned')}
            >
              {t('aiLibrary.tab.market', 'Marketplace')}{' '}
              <span className="text-[10px]">
                ({t('aiLibrary.tab.marketPlanned', 'Planned')})
              </span>
            </span>
          </>
        }
      />

      {/* Filters */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search
            size={13}
            className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-ink-600"
          />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('aiLibrary.searchAgents', 'Search agents...')}
            aria-label={t('aiLibrary.searchAgents', 'Search agents...')}
            className="w-56 rounded-md border border-ink-800 bg-transparent py-1 pl-7 pr-2 text-[12px] text-ink-200 placeholder:text-ink-600"
          />
        </div>
        {(['all', ...AGENT_GROUP_ORDER] as const).map((g) => (
          <button
            key={g}
            type="button"
            onClick={() => setGroupFilter(g)}
            data-testid={`group-chip-${g}`}
            aria-pressed={groupFilter === g}
            className={`rounded-full border px-2.5 py-1 text-[11px] ${
              groupFilter === g
                ? 'border-ink-600 bg-ink-800/60 text-ink-200'
                : 'border-ink-800 text-ink-500 hover:text-ink-300'
            }`}
          >
            {g === 'all'
              ? t('aiLibrary.filterAll', 'All')
              : `${GROUP_META[g].emoji} ${t(GROUP_META[g].labelKey, GROUP_META[g].label)}`}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setFaultsOnly((v) => !v)}
          aria-pressed={faultsOnly}
          className={`rounded-full border px-2.5 py-1 text-[11px] ${
            faultsOnly
              ? 'border-danger-line bg-danger-soft text-danger'
              : 'border-ink-800 text-ink-500 hover:text-ink-300'
          }`}
        >
          {t('aiLibrary.filterFaultsOnly', 'Faults only')}
          {faultCount > 0 && ` · ${faultCount}`}
        </button>
      </div>

      {loadError && (
        <div className="mt-3 rounded-lg border border-danger-line bg-danger-soft px-3 py-2 text-[12px] text-danger">
          {t('aiLibrary.loadAgentsError', 'Failed to load')}: {loadError}{' '}
          <button
            type="button"
            onClick={() => void loadAgents()}
            className="underline"
          >
            {t('aiLibrary.retry', 'Retry')}
          </button>
        </div>
      )}
      {statsError && (
        <div className="mt-3 rounded-lg border border-warn-line bg-warn-soft px-3 py-2 text-[12px] text-warn">
          {t(
            'aiLibrary.statsUnavailable',
            'Weekly stats unavailable — cards show agent details only',
          )}
        </div>
      )}

      {/* Live banner — one line naming what is running right now. */}
      {liveAgent && (
        <div
          className="mt-3 flex items-center gap-2 rounded-lg border border-agent-line bg-agent-soft px-3 py-2 text-[12px] text-agent"
          data-testid="live-banner"
        >
          <span className="h-1.5 w-1.5 rounded-full bg-agent" />
          <span className="min-w-0 flex-1 truncate">
            {t('aiLibrary.liveBanner', '{{name}} is running now', {
              name: liveAgent.name,
            })}
          </span>
          <button
            type="button"
            onClick={() =>
              navigate(`${urlPrefix}/ai-library/agents/${liveAgent.slug}?tab=workbench`)
            }
            className="shrink-0 underline"
          >
            {t('aiLibrary.action.viewRuns', 'View run')}
          </button>
        </div>
      )}

      {/* Roster */}
      <div className="mt-4 space-y-6">
        {AGENT_GROUP_ORDER.map((g) => {
          const rows = grouped.get(g) ?? [];
          if (rows.length === 0) return null;
          return (
            <section key={g} data-testid={`group-section-${g}`}>
              <h2 className="flex items-baseline gap-2 text-[12px] font-medium text-ink-400">
                <span>
                  {GROUP_META[g].emoji} {t(GROUP_META[g].labelKey, GROUP_META[g].label)}
                </span>
                <span className="text-[11px] text-ink-600">{rows.length}</span>
              </h2>
              <div className="mt-2 grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
                {rows.map((a) => (
                  <AgentCard
                    key={a.id}
                    agent={a}
                    stats={stats[a.id]}
                    status={statusById.get(a.id) ?? { kind: 'idle' }}
                    urlPrefix={urlPrefix}
                    onChat={requestChat}
                  />
                ))}
              </div>
            </section>
          );
        })}

        {visible.length === 0 && !loadError && (
          <div className="rounded-lg border border-ink-800/60 px-4 py-8 text-center text-[12px] text-ink-600">
            {t('aiLibrary.noAgentsMatch', 'No agents match these filters')}
          </div>
        )}
      </div>

      {showNewAgent && (
        <NewAgentModal
          existingAgents={agents}
          onClose={() => setShowNewAgent(false)}
          onCreated={handleCreated}
        />
      )}
    </div>
  );
};

export default AgentGalleryPage;
