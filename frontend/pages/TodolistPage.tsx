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
import { ListTodo } from 'lucide-react';
import { IssueListView, type IssueViewMode } from '../components/Todolist/IssueListView';
import { IssueDetailView } from '../components/Todolist/IssueDetailView';
import { NewIssueDialog } from '../components/Todolist/NewIssueDialog';
import type { AgentRef, UiIssue } from '../components/Todolist/types';
import {
  listIssues, getIssueByIdentifier, createIssue, type Issue, type IssueCreatePayload,
} from '../services/issuesService';
import { aiLibraryService } from '../services/aiLibraryService';
import { toAgentRef, toUiIssue, type ProjectNameMap } from '../components/Todolist/uiIssue';
import { fetchProjects } from '../services/projectsService';
import { useToast } from '../components/Toast';
import { useAuth } from '../contexts/AuthContext';
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
  const [projectsById, setProjectsById] = useState<ProjectNameMap>({});

  const [selectedIssue, setSelectedIssue] = useState<UiIssue | null>(null);
  const [selectedLoading, setSelectedLoading] = useState(false);
  const [selectedError, setSelectedError] = useState<string | null>(null);

  const [newIssueOpen, setNewIssueOpen] = useState(false);
  const [newIssueParentId, setNewIssueParentId] = useState<number | null>(null);
  const [viewMode, setViewMode] = useState<IssueViewMode>('list');

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
            className="mt-3 text-xs text-indigo-400 hover:text-indigo-300"
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
            onClose={() => { setNewIssueOpen(false); setNewIssueParentId(null); }}
            onSubmit={handleCreate}
          />
        )}
      </>
    );
  }

  return (
    <>
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
      />
      {newIssueOpen && (
        <NewIssueDialog
          agents={agents}
          teamId={teamIdNum}
          parentId={newIssueParentId}
          projects={projectOptions}
          onClose={() => { setNewIssueOpen(false); setNewIssueParentId(null); }}
          onSubmit={handleCreate}
        />
      )}
    </>
  );
}

export default TodolistPage;
