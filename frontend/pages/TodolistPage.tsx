/**
 * TodolistPage — paperclip-style task tracking + chat (A8.3 wired to backend).
 *
 * Lifecycle:
 *   - on mount, fetch issues + agents in parallel; build UiIssue list
 *   - URL :identifier present → fetch the single issue by identifier;
 *     IssueDetailView fetches its messages and subscribes Realtime
 *   - + New Issue → modal; on submit createIssue + navigate to detail
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ListTodo, Users, User, Bot, GitBranch } from 'lucide-react';
import { IssueListView, type IssueViewMode } from '../components/Todolist/IssueListView';
import { PipelinesManagerModal } from '../components/Todolist/PipelinesManagerModal';
import { PageHeader } from '../components/layout/PageHeader';
import { fetchMyTeams, fetchPersonalTeam } from '../services/teamService';
import { IssueDetailView } from '../components/Todolist/IssueDetailView';
import { NewIssueDialog } from '../components/Todolist/NewIssueDialog';
import type { AgentRef, UiIssue } from '../components/Todolist/types';
import type { IssueScope } from '../components/Todolist/issueScope';
import {
  listIssues, getIssue, getIssueByIdentifier, createIssue, type Issue, type IssueCreatePayload,
} from '../services/issuesService';
import { aiLibraryService } from '../services/aiLibraryService';
import { toAgentRef, toUiIssue, type ProjectNameMap } from '../components/Todolist/uiIssue';
import { mergeRealtimeIssue, shouldRefetchOnRealtime } from '../components/Todolist/mergeRealtimeIssue';
import { computeSubtaskCounts } from '../components/Todolist/issueFlow';
import { fetchProjects, createProject } from '../services/projectsService';
import { useToast } from '../components/Toast';
import { useAuth } from '../contexts/AuthContext';
import { useWorkspaceScope } from '../hooks/useWorkspaceScope';

/** Which slice of the team's issues the list is showing (client-side toggle). */
type ScopeMode = 'team' | 'my' | 'agent';

/**
 * How long to sit on a live row's Realtime events before refetching it.
 *
 * A running agent emits a burst of UPDATEs per turn (status flip, progress
 * write, updated_at touch). Firing a request per event would hammer the API
 * for a counter that only needs to look current to a human, so the window
 * collapses each burst into one fetch — per issue, so a busy row can never
 * starve a quiet one.
 */
const LIVE_REFETCH_DEBOUNCE_MS = 800;

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
  const { t } = useTranslation();
  const { isPersonal: isPersonalWorkspace, effectiveTeamId } = useWorkspaceScope();
  const navigate = useNavigate();
  const { addToast } = useToast();
  const { currentUserId } = useAuth();

  const [issues, setIssues] = useState<UiIssue[]>([]);
  const [issuesLoading, setIssuesLoading] = useState(true);
  const [issuesError, setIssuesError] = useState<string | null>(null);
  // Sub-issue done/total per parent, from the full loaded list — shared by the
  // list rows (recomputed inside IssueListView) and the open detail header.
  const subtaskCounts = useMemo(() => computeSubtaskCounts(issues), [issues]);

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
  // header). Follows the projects convention: a personal workspace creates a
  // team-less project (team_id=NULL — how the Projects page identifies personal
  // projects), a real collaborative workspace stamps that team's snowflake. Ids
  // stay strings (Snowflake-safe). The mirrored stage issue still resolves the
  // owner's personal team on the backend so it stays visible in this Todolist.
  const handleCreateProject = useCallback(async (name: string) => {
    try {
      const created = await createProject({
        name,
        team_id: isPersonalWorkspace ? undefined : (effectiveTeamId || undefined),
      });
      const id = String(created.id);
      setProjectsById((prev) => ({ ...prev, [id]: { name: created.name } }));
      addToast(`Created project ${created.name}`, 'success');
      return { id, name: created.name };
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Failed to create project', 'error');
      throw err;
    }
  }, [isPersonalWorkspace, effectiveTeamId, addToast]);

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

  // Every fetch that maps a row reads the maps from here rather than closing
  // over them. The agent / project maps land a beat AFTER the single-issue
  // fetch (two endpoints racing one), so listing them as effect deps would
  // re-run the fetch, flip `selectedLoading` back to true and unmount the
  // whole detail subtree — taking the user's half-typed comment and any open
  // dialog with it. A late map is new display text, never a reason to reload.
  const mapsRef = useRef({ agentsById, projectsById });
  useEffect(() => {
    mapsRef.current = { agentsById, projectsById };
  }, [agentsById, projectsById]);

  // Bumped by every request and by the effect's teardown, so a response only
  // lands if nothing newer has been asked for since — the single fetch path is
  // shared by the URL effect and by the post-dispatch re-read.
  const selectedReqRef = useRef(0);
  /**
   * Which issue's detail subtree is mounted RIGHT NOW, or null while the page
   * is showing the placeholder / the error page / no issue at all.
   *
   * Deliberately not "the identifier `selectedIssue` happens to hold":
   * `selectedIssue` keeps the issue the page has LEFT until the next one
   * lands, so on an A→B→A flip it would claim A is on screen while B's
   * placeholder is what the user is actually looking at — and a load that
   * skips the placeholder from there never settles it again. Written
   * synchronously at every transition below, so it is always the frame the
   * user has, never one render behind.
   */
  const shownIdentifierRef = useRef<string | null>(null);

  /**
   * The one place the open issue is fetched. Every trigger goes through here.
   *
   * `selectedLoading` and `selectedError` gate the ENTIRE detail subtree (see
   * the two early returns below), so flipping either one for a refetch of the
   * issue already on screen unmounts `IssueDetailView` — taking a half-typed
   * comment and any open dialog with it. Only a request for a DIFFERENT issue
   * (or the first one) may show the placeholder; a same-issue refetch updates
   * state in place and, if it fails, leaves the slightly stale row standing
   * rather than replacing it with an error page. This is structural: it holds
   * for triggers that don't exist yet (Realtime re-read, post-dispatch, …),
   * not just for the ones wired today.
   */
  const loadSelectedIssue = useCallback(async (wanted: string) => {
    const seq = ++selectedReqRef.current;
    const inPlace = shownIdentifierRef.current === wanted;
    if (!inPlace) {
      // Nothing of `wanted` is on screen, so this is a cold load: placeholder
      // now, settled in `finally` — including when an earlier request for a
      // different issue is still in flight and will be discarded below.
      shownIdentifierRef.current = null;
      setSelectedLoading(true);
      setSelectedError(null);
    }
    try {
      const raw = await getIssueByIdentifier(wanted);
      if (selectedReqRef.current !== seq) return;
      const { agentsById: agentMap, projectsById: projectMap } = mapsRef.current;
      setSelectedIssue(toUiIssue(raw, agentMap, projectMap));
      shownIdentifierRef.current = wanted;
    } catch (err) {
      if (selectedReqRef.current !== seq) return;
      if (inPlace) {
        // Non-fatal on purpose: the row is already on screen, just a beat
        // stale. Same posture as the live-row refetch below.
        console.error('[TodolistPage] selected issue refetch failed', err);
        return;
      }
      setSelectedError(err instanceof Error ? err.message : 'Failed to load issue');
    } finally {
      // Settling is unconditional for the newest request: an in-place refetch
      // never turned it on, and React bails out of a no-op setState, so this
      // cannot cost the mounted subtree a render.
      if (selectedReqRef.current === seq) setSelectedLoading(false);
    }
  }, []);

  // Fetch single issue when :identifier set
  useEffect(() => {
    if (!identifier) {
      // Back to the list: reset every gate, so the next detail open starts
      // from a clean frame instead of inheriting a stale error or placeholder.
      shownIdentifierRef.current = null;
      setSelectedIssue(null);
      setSelectedError(null);
      setSelectedLoading(false);
      return undefined;
    }
    void loadSelectedIssue(identifier);
    return () => { selectedReqRef.current += 1; };
  }, [identifier, loadSelectedIssue]);

  // ── Live-row single-issue refetch ────────────────────────────────────
  // mergeRealtimeIssue stops the running chip flickering by carrying the
  // publication-excluded columns over, but carrying them over also freezes
  // them: `execution_state` (where the turn counter lives) is outside the mig
  // 172 whitelist, so a running row's "turn 3 · 4m" would read whatever the
  // last REST fetch saw until the user navigated away. For live rows the event
  // is a refetch signal, not data — same posture as TaskManagerContext.
  const refetchTimers = useRef(new Map<number, ReturnType<typeof setTimeout>>());
  // Lets the Realtime handler consult the current row without reading it
  // inside a setState updater (which React may invoke more than once).
  const issuesRef = useRef<UiIssue[]>(issues);
  useEffect(() => {
    issuesRef.current = issues;
  }, [issues]);

  useEffect(() => {
    const timers = refetchTimers.current;
    return () => {
      for (const timer of timers.values()) clearTimeout(timer);
      timers.clear();
    };
  }, []);

  const scheduleLiveRefetch = useCallback((issueId: number) => {
    const timers = refetchTimers.current;
    const pending = timers.get(issueId);
    if (pending) clearTimeout(pending);
    timers.set(issueId, setTimeout(() => {
      timers.delete(issueId);
      getIssue(issueId)
        .then((fresh) => {
          const { agentsById: agentMap, projectsById: projectMap } = mapsRef.current;
          setIssues((prev) => {
            const idx = prev.findIndex((i) => i.id === fresh.id);
            // Dropped from the list (filtered / deleted) while in flight.
            if (idx < 0) return prev;
            const next = [...prev];
            // Same merge as the Realtime path — `fresh` is a full REST row, so
            // this time the excluded columns arrive with real values.
            next[idx] = mergeRealtimeIssue(prev[idx], fresh, agentMap, projectMap);
            return next;
          });
        })
        .catch((err) => {
          // Non-fatal on purpose: the merged row is already on screen, just
          // with a stale turn count. Failing loudly here would be worse than
          // the staleness this whole path exists to reduce.
          console.error('[TodolistPage] live-row refetch failed', err);
        });
    }, LIVE_REFETCH_DEBOUNCE_MS));
  }, []);

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
          if (shouldRefetchOnRealtime(issuesRef.current.find((i) => i.id === raw.id), raw)) {
            scheduleLiveRefetch(raw.id);
          }
          setIssues((prev) => {
            const idx = prev.findIndex((i) => i.id === raw.id);
            // INSERT: nothing to merge onto.
            if (idx < 0) return [toUiIssue(raw, agentsById, projectsById), ...prev];
            // UPDATE: merge, don't replace — the publication (mig 172) omits
            // dbos_workflow_id / execution_state / execution_locked_at, so a
            // wholesale swap blanks them and the running chip flickers off.
            const next = [...prev];
            next[idx] = mergeRealtimeIssue(prev[idx], raw, agentsById, projectsById);
            return next;
          });
        },
      )
      .subscribe();
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [teamIdNum, agentsById, projectsById, scheduleLiveRefetch]);

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
    // Split view (harness P4 T9): on a wide screen the list stays visible and
    // clickable beside the detail, with the open row highlighted; narrower
    // screens keep the full-page detail. Same IssueListView instance, same
    // props — only `selectedIssueId` differs.
    return (
      <div className="flex h-full min-h-0">
        <div className="hidden xl:flex xl:flex-col w-[420px] shrink-0 border-r border-ink-800/80 min-h-0" data-testid="issues-split-list">
          <IssueListView
            issues={issues}
            loading={issuesLoading}
            error={issuesError}
            viewMode="list"
            onViewModeChange={setViewMode}
            onNewIssue={() => setNewIssueOpen(true)}
            onRefresh={() => { void refreshIssues(agentsById, projectsById); }}
            agents={agents}
            currentUserId={currentUserId ?? undefined}
            scope={scope}
            teamName={teamName ?? undefined}
            onCreateProject={handleCreateProject}
            selectedIssueId={selectedIssue.id}
          />
        </div>
        <div className="flex-1 min-w-0 min-h-0 flex flex-col">
        <IssueDetailView
          issue={selectedIssue}
          agents={agents}
          agentsById={agentsById}
          selfUserId={currentUserId ?? undefined}
          subtaskCount={subtaskCounts.get(selectedIssue.id)}
          onCreateSubIssue={(parentId) => {
            setNewIssueParentId(parentId);
            setNewIssueOpen(true);
          }}
          onIssueDispatched={() => {
            if (identifier) void loadSelectedIssue(identifier);
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
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="px-4 pt-4">
        <PageHeader
          // No secondary rail in this module, so this IS the module name.
          level="module"
          title={t('issues.pageTitle', 'Issues')}
          subtitle={teamName || undefined}
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
