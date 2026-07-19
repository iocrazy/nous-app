/**
 * TodolistPage — paperclip-style task tracking + chat (A8.3 wired to backend).
 *
 * Lifecycle:
 *   - on mount, fetch issues + agents in parallel; build UiIssue list
 *   - URL :identifier present → fetch the single issue by identifier;
 *     IssueDetailView fetches its messages and subscribes Realtime
 *   - + New Issue → modal; on submit createIssue + navigate to detail
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ListTodo, Users, User, Bot, GitBranch } from 'lucide-react';
import { IssueListView, type IssueViewMode } from '../components/Todolist/IssueListView';
import { PipelinesManagerModal } from '../components/Todolist/PipelinesManagerModal';
import { PageHeader } from '../components/AILibrary/PageHeader';
import { fetchMyTeams, fetchPersonalTeam } from '../services/teamService';
import { IssueDetailView } from '../components/Todolist/IssueDetailView';
import { NewIssueDialog } from '../components/Todolist/NewIssueDialog';
import type { AgentRef, UiIssue } from '../components/Todolist/types';
import type { IssueScope } from '../components/Todolist/issueScope';
import {
  listIssues, getIssueByIdentifier, createIssue, type Issue, type IssueCreatePayload,
} from '../services/issuesService';
import { aiLibraryService } from '../services/aiLibraryService';
import { toAgentRef, toUiIssue, type ProjectNameMap } from '../components/Todolist/uiIssue';
import { fetchProjects, createProject } from '../services/projectsService';
import { useToast } from '../components/Toast';
import { useAuth } from '../contexts/AuthContext';

/** Which slice of the team's issues the list is showing (client-side toggle). */
type ScopeMode = 'team' | 'my' | 'agent';

const ScopePill: React.FC<{
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}> = ({ active, onClick, icon, label }) => (
  <button
    type="button"
    onClick={onClick}
    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full ring-1 transition ${
      active
        ? 'bg-[var(--accent-soft)] text-[var(--accent-text)] ring-[var(--accent-border)]'
        : 'text-ink-400 hover:text-ink-200 ring-ink-800 hover:bg-ink-800'
    }`}
  >
    {icon} {label}
  </button>
);
import { getSupabaseClient } from '../supabaseClient';
// useAuth gives currentUserId via context; UserProfile shape doesn't carry id.

export function TodolistPage() {
  const { identifier, teamId } = useParams<{ identifier?: string; teamId: string }>();
  const navigate = useNavigate();
  const { addToast } = useToast();
  const { currentUserId } = useAuth();

  const [issues, setIssues] = useState<UiIssue[]>([]);
  const [issuesLoading, setIssuesLoading] = useState(true);
  const [issuesError, setIssuesError] = useState<string | null>(null);

  const [agents, setAgents] = useState<AgentRef[]>([]);
  const [agentsById, setAgentsById] = useState<Record<string, AgentRef>>({});
  const [pipelinesOpen, setPipelinesOpen] = useState(false);
  const [projectsById, setProjectsById] = useState<ProjectNameMap>({});
  // Team name for the page header (mockup: "Issues  Team 8"). Best-effort —
  // a fetch failure just renders the title without the team suffix.
  const [teamName, setTeamName] = useState<string | null>(null);

  useEffect(() => {
    if (!teamId) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const [mine, personal] = await Promise.all([
          fetchMyTeams().catch(() => []),
          fetchPersonalTeam().catch(() => null),
        ]);
        if (cancelled) return;
        const all = personal ? [personal, ...mine] : mine;
        const hit = all.find((t) => String(t.id) === String(teamId));
        if (hit) setTeamName(hit.name);
      } catch (err) {
        console.error('[TodolistPage] team name resolve failed:', err);
      }
    })();
    return () => { cancelled = true; };
  }, [teamId]);

  const [selectedIssue, setSelectedIssue] = useState<UiIssue | null>(null);
  const [selectedLoading, setSelectedLoading] = useState(false);
  const [selectedError, setSelectedError] = useState<string | null>(null);

  const [newIssueOpen, setNewIssueOpen] = useState(false);
  const [newIssueParentId, setNewIssueParentId] = useState<number | null>(null);
  const [viewMode, setViewMode] = useState<IssueViewMode>('list');
  const [scopeMode, setScopeMode] = useState<ScopeMode>('team');
  const [scopeAgentId, setScopeAgentId] = useState<string | null>(null);

  // Kept as a STRING: team ids are Snowflake BIGINTs and Number() rounds them
  // past 2^53 — a rounded filter can never match rows created with the exact
  // id (scene/canvas-spawned issues), and a rounded create writes a corrupt
  // team_id. Validated digits-only; the backend parses it as an exact int64.
  const teamIdNum = useMemo(() => {
    if (!teamId) return null;
    return /^\d+$/.test(teamId) ? teamId : null;
  }, [teamId]);

  const projectOptions = useMemo(
    () => Object.entries(projectsById).map(([id, v]) => ({ id, name: v.name })),
    [projectsById],
  );

  // The scope handed to IssueListView. team is the default and hard boundary;
  // `my`/`agent` are client-side projections (no refetch). Falls back to team
  // when the mode's required id (userId / agentId) isn't available yet.
  const scopeTeamId = teamIdNum ?? teamId ?? '';
  const scope: IssueScope = useMemo(() => {
    if (scopeMode === 'my' && currentUserId) {
      return { type: 'my', teamId: scopeTeamId, userId: currentUserId };
    }
    if (scopeMode === 'agent' && scopeAgentId) {
      return { type: 'agent', teamId: scopeTeamId, agentId: scopeAgentId };
    }
    return { type: 'team', teamId: scopeTeamId };
  }, [scopeMode, scopeAgentId, currentUserId, scopeTeamId]);

  // Create a project inline (from the New Issue picker or the Group-by-Project
  // header). Stamps the current team; ids stay strings (Snowflake-safe).
  const handleCreateProject = useCallback(async (name: string) => {
    try {
      const created = await createProject({ name, team_id: teamIdNum ?? undefined });
      const id = String(created.id);
      setProjectsById((prev) => ({ ...prev, [id]: { name: created.name } }));
      addToast(`Created project ${created.name}`, 'success');
      return { id, name: created.name };
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Failed to create project', 'error');
      throw err;
    }
  }, [teamIdNum, addToast]);

  const refreshIssues = useCallback(async (
    agentMap: Record<string, AgentRef>,
    projectMap: ProjectNameMap,
  ) => {
    setIssuesLoading(true);
    setIssuesError(null);
    try {
      const filters = teamIdNum ? { team_id: teamIdNum, limit: 200 } : { limit: 200 };
      const resp = await listIssues(filters);
      setIssues(resp.items.map((r) => toUiIssue(r, agentMap, projectMap)));
    } catch (err) {
      setIssuesError(err instanceof Error ? err.message : 'Failed to load issues');
    } finally {
      setIssuesLoading(false);
    }
  }, [teamIdNum]);

  // Fetch agents + issues on mount
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [list, projects] = await Promise.all([
          aiLibraryService.listAgents(),
          fetchProjects(teamId ? { teamId } : undefined).catch(() => []),
        ]);
        if (cancelled) return;
        const refs = list.map(toAgentRef);
        const map: Record<string, AgentRef> = {};
        for (const r of refs) map[r.id] = r;
        const projMap: ProjectNameMap = {};
        for (const p of projects) projMap[String(p.id)] = { name: p.name };
        setAgents(refs);
        setAgentsById(map);
        setProjectsById(projMap);
        await refreshIssues(map, projMap);
      } catch (err) {
        if (cancelled) return;
        addToast(err instanceof Error ? err.message : 'Failed to load agents', 'error');
        await refreshIssues({}, {});
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [refreshIssues, addToast, teamId]);

  // Fetch single issue when :identifier set
  useEffect(() => {
    if (!identifier) {
      setSelectedIssue(null);
      return;
    }
    let cancelled = false;
    setSelectedLoading(true);
    setSelectedError(null);
    (async () => {
      try {
        const raw = await getIssueByIdentifier(identifier);
        if (cancelled) return;
        setSelectedIssue(toUiIssue(raw, agentsById, projectsById));
      } catch (err) {
        if (cancelled) return;
        setSelectedError(err instanceof Error ? err.message : 'Failed to load issue');
      } finally {
        if (!cancelled) setSelectedLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [identifier, agentsById, projectsById]);

  // Realtime: keep the issues list in sync without a manual refresh.
  // `issues` is in the supabase_realtime publication — subscribe so status
  // changes (agent updates, other users) reflect live in the list instead
  // of going stale until onRefresh / re-navigation.
  useEffect(() => {
    const supabase = getSupabaseClient();
    if (!supabase) return;

    const filter = teamIdNum != null ? `team_id=eq.${teamIdNum}` : undefined;
    const channel = supabase
      .channel('issues_list_realtime')
      .on(
        'postgres_changes',
        {
          event: '*',
          schema: 'public',
          table: 'issues',
          ...(filter ? { filter } : {}),
        },
        (payload) => {
          if (payload.eventType === 'DELETE') {
            const oldId = (payload.old as { id?: number })?.id;
            if (oldId != null) {
              setIssues((prev) => prev.filter((i) => i.id !== oldId));
            }
            return;
          }
          const raw = payload.new as Issue;
          if (!raw?.id) return;
          const ui = toUiIssue(raw, agentsById, projectsById);
          setIssues((prev) => {
            const idx = prev.findIndex((i) => i.id === ui.id);
            if (idx < 0) return [ui, ...prev]; // INSERT
            const next = [...prev]; // UPDATE
            next[idx] = ui;
            return next;
          });
        },
      )
      .subscribe();
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [teamIdNum, agentsById, projectsById]);

  const handleCreate = async (payload: IssueCreatePayload) => {
    try {
      const created = await createIssue(payload);
      const ui = toUiIssue(created, agentsById, projectsById);
      setIssues((prev) => [ui, ...prev]);
      setNewIssueOpen(false);
      navigate(`/team/${teamId}/todolist/${created.identifier}`);
      addToast(`Created ${created.identifier}`, 'success');
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Create failed', 'error');
      throw err;
    }
  };

  if (identifier) {
    if (selectedLoading || (!selectedIssue && !selectedError)) {
      return (
        <div className="flex flex-col items-center justify-center h-64 text-ink-500">
          <ListTodo size={32} className="mb-3 text-ink-700 animate-pulse" />
          <p className="text-sm">Loading {identifier}…</p>
        </div>
      );
    }
    if (selectedError || !selectedIssue) {
      return (
        <div className="flex flex-col items-center justify-center h-64 text-ink-500">
          <ListTodo size={32} className="mb-3 text-ink-700" />
          <p className="text-sm">{selectedError ?? `Issue ${identifier} not found.`}</p>
          <button
            onClick={() => navigate(`/team/${teamId}/todolist`)}
            className="mt-3 text-xs text-[var(--accent-text)] hover:text-[var(--accent-text)]"
          >
            ← Back to all issues
          </button>
        </div>
      );
    }
    return (
      <>
        <IssueDetailView
          issue={selectedIssue}
          agents={agents}
          agentsById={agentsById}
          selfUserId={currentUserId ?? undefined}
          onCreateSubIssue={(parentId) => {
            setNewIssueParentId(parentId);
            setNewIssueOpen(true);
          }}
          onIssueDispatched={() => {
            if (identifier) {
              getIssueByIdentifier(identifier).then((raw) => {
                setSelectedIssue(toUiIssue(raw, agentsById, projectsById));
              }).catch(() => { /* ignore — Realtime will sync eventually */ });
            }
          }}
        />
        {newIssueOpen && (
          <NewIssueDialog
            agents={agents}
            teamId={teamIdNum}
            parentId={newIssueParentId}
            projects={projectOptions}
            onCreateProject={handleCreateProject}
            onClose={() => { setNewIssueOpen(false); setNewIssueParentId(null); }}
            onSubmit={handleCreate}
          />
        )}
      </>
    );
  }

  return (
    <>
      <div className="px-4 pt-4">
        <PageHeader
          title={
            <span className="flex items-baseline gap-2">
              Issues
              {teamName && (
                <span className="text-sm font-normal text-ink-500">{teamName}</span>
              )}
            </span>
          }
          actions={
            <div className="flex items-center gap-3">
              {teamIdNum && (
                <button
                  type="button"
                  onClick={() => setPipelinesOpen(true)}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 text-[12px] rounded border border-ink-800 text-ink-300 hover:bg-ink-800/60"
                  title="Manage content relay pipelines"
                >
                  <GitBranch size={13} /> Pipelines
                </button>
              )}
              <span data-testid="issues-kbd-hints" className="hidden md:flex items-center gap-1.5 text-[12px] text-ink-500">
              {scopeMode !== 'agent' && (
                <>
                  <kbd className="font-mono text-[10px] leading-none px-1 py-0.5 rounded border border-ink-700 bg-ink-800/50 text-ink-400">C</kbd>
                  new issue
                  <span className="text-ink-700">·</span>
                </>
              )}
              <kbd className="font-mono text-[10px] leading-none px-1 py-0.5 rounded border border-ink-700 bg-ink-800/50 text-ink-400">/</kbd>
              search
            </span>
            </div>
          }
          className="pb-2"
        />
        <div className="flex items-center gap-1.5 pb-2 text-[12px]" data-testid="scope-pills">
          <ScopePill
            active={scopeMode === 'team'}
            onClick={() => setScopeMode('team')}
            icon={<Users size={12} />}
            label="Team"
          />
          <ScopePill
            active={scopeMode === 'my'}
            onClick={() => setScopeMode('my')}
            icon={<User size={12} />}
            label="My Issues"
          />
          <div
            className={`inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded-full ring-1 transition ${
              scopeMode === 'agent'
                ? 'bg-[var(--accent-soft)] text-[var(--accent-text)] ring-[var(--accent-border)]'
                : 'text-ink-400 ring-ink-800'
            }`}
          >
            <button
              type="button"
              onClick={() => setScopeMode('agent')}
              className="inline-flex items-center gap-1"
            >
              <Bot size={12} /> Agent
            </button>
            <select
              value={scopeAgentId ?? ''}
              onChange={(e) => {
                setScopeAgentId(e.target.value || null);
                setScopeMode('agent');
              }}
              className="bg-transparent text-[11px] focus:outline-none cursor-pointer max-w-[8rem]"
              title="Filter to an agent's issues"
            >
              <option value="">Select…</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
          </div>
        </div>
      </div>
      <IssueListView
        issues={issues}
        loading={issuesLoading}
        error={issuesError}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        onNewIssue={() => setNewIssueOpen(true)}
        onRefresh={() => { void refreshIssues(agentsById, projectsById); }}
        agents={agents}
        currentUserId={currentUserId ?? undefined}
        scope={scope}
        teamName={teamName ?? undefined}
        onCreateProject={handleCreateProject}
      />
      {newIssueOpen && (
        <NewIssueDialog
          agents={agents}
          teamId={teamIdNum}
          parentId={newIssueParentId}
          projects={projectOptions}
          onCreateProject={handleCreateProject}
          onClose={() => { setNewIssueOpen(false); setNewIssueParentId(null); }}
          onSubmit={handleCreate}
        />
      )}
      {pipelinesOpen && teamIdNum && (
        <PipelinesManagerModal
          teamId={teamIdNum}
          agents={agents}
          onClose={() => setPipelinesOpen(false)}
        />
      )}
    </>
  );
}

export default TodolistPage;
