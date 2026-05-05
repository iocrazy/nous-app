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
import { IssueListView } from '../components/Todolist/IssueListView';
import { IssueDetailView } from '../components/Todolist/IssueDetailView';
import { NewIssueDialog } from '../components/Todolist/NewIssueDialog';
import type { AgentRef, UiIssue } from '../components/Todolist/types';
import {
  listIssues, getIssueByIdentifier, createIssue, type Issue, type IssueCreatePayload,
} from '../services/issuesService';
import { aiLibraryService } from '../services/aiLibraryService';
import type { AILibraryAgent } from '../types';
import { useToast } from '../components/Toast';
import { useAuth } from '../contexts/AuthContext';
// useAuth gives currentUserId via context; UserProfile shape doesn't carry id.

const AGENT_PALETTE = [
  'bg-amber-500', 'bg-emerald-500', 'bg-blue-500', 'bg-purple-500',
  'bg-pink-500', 'bg-cyan-500', 'bg-orange-500', 'bg-indigo-500',
];

function colorForAgent(slug: string): string {
  let h = 0;
  for (const ch of slug) h = (h * 31 + ch.charCodeAt(0)) & 0xfffff;
  return AGENT_PALETTE[h % AGENT_PALETTE.length];
}

function toAgentRef(a: AILibraryAgent): AgentRef {
  return {
    id: a.id,
    slug: a.slug,
    name: a.name,
    avatar_color: colorForAgent(a.slug),
  };
}

function toUiIssue(raw: Issue, agentsById: Record<string, AgentRef>): UiIssue {
  const lastActivity = raw.updated_at ?? raw.created_at;
  return {
    id: raw.id,
    identifier: raw.identifier,
    title: raw.title,
    description: raw.description,
    status: raw.status,
    priority: raw.priority,
    assignee: raw.assignee_agent_id ? agentsById[raw.assignee_agent_id] : undefined,
    assignee_user_label: raw.assignee_user_id ? `User ${raw.assignee_user_id.slice(0, 6)}` : undefined,
    project: raw.project_id ? { id: raw.project_id, name: `Project ${raw.project_id}` } : undefined,
    parent_id: raw.parent_id ?? null,
    created_at: raw.created_at,
    updated_at: raw.updated_at ?? raw.created_at,
    last_activity_at: lastActivity,
    raw,
  };
}

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

  const [selectedIssue, setSelectedIssue] = useState<UiIssue | null>(null);
  const [selectedLoading, setSelectedLoading] = useState(false);
  const [selectedError, setSelectedError] = useState<string | null>(null);

  const [newIssueOpen, setNewIssueOpen] = useState(false);

  const teamIdNum = useMemo(() => {
    if (!teamId) return null;
    const n = Number(teamId);
    return Number.isFinite(n) ? n : null;
  }, [teamId]);

  const refreshIssues = useCallback(async (agentMap: Record<string, AgentRef>) => {
    setIssuesLoading(true);
    setIssuesError(null);
    try {
      const filters = teamIdNum ? { team_id: teamIdNum, limit: 200 } : { limit: 200 };
      const resp = await listIssues(filters);
      setIssues(resp.items.map((r) => toUiIssue(r, agentMap)));
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
        const list = await aiLibraryService.listAgents();
        if (cancelled) return;
        const refs = list.map(toAgentRef);
        const map: Record<string, AgentRef> = {};
        for (const r of refs) map[r.id] = r;
        setAgents(refs);
        setAgentsById(map);
        await refreshIssues(map);
      } catch (err) {
        if (cancelled) return;
        addToast(err instanceof Error ? err.message : 'Failed to load agents', 'error');
        await refreshIssues({});
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [refreshIssues, addToast]);

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
        setSelectedIssue(toUiIssue(raw, agentsById));
      } catch (err) {
        if (cancelled) return;
        setSelectedError(err instanceof Error ? err.message : 'Failed to load issue');
      } finally {
        if (!cancelled) setSelectedLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [identifier, agentsById]);

  const handleCreate = async (payload: IssueCreatePayload) => {
    try {
      const created = await createIssue(payload);
      const ui = toUiIssue(created, agentsById);
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
        <div className="flex flex-col items-center justify-center h-64 text-zinc-500">
          <ListTodo size={32} className="mb-3 text-zinc-700 animate-pulse" />
          <p className="text-sm">Loading {identifier}…</p>
        </div>
      );
    }
    if (selectedError || !selectedIssue) {
      return (
        <div className="flex flex-col items-center justify-center h-64 text-zinc-500">
          <ListTodo size={32} className="mb-3 text-zinc-700" />
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
      <IssueDetailView
        issue={selectedIssue}
        agents={agents}
        agentsById={agentsById}
        selfUserId={currentUserId ?? undefined}
      />
    );
  }

  return (
    <>
      <IssueListView
        issues={issues}
        loading={issuesLoading}
        error={issuesError}
        onNewIssue={() => setNewIssueOpen(true)}
        onRefresh={() => { void refreshIssues(agentsById); }}
      />
      {newIssueOpen && (
        <NewIssueDialog
          agents={agents}
          teamId={teamIdNum}
          onClose={() => setNewIssueOpen(false)}
          onSubmit={handleCreate}
        />
      )}
    </>
  );
}

export default TodolistPage;
