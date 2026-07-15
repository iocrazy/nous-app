/**
 * WorkspaceTasks — the Tasks module inside a project workspace. Reuses the
 * team-level IssueListView but scopes every read to this project's
 * `project_id`, and every new issue created here is stamped with it, so the
 * project and its to-dos are no longer decoupled.
 *
 * Clicking a row navigates to the existing team-level detail route
 * (/team/:teamId/todolist/:identifier) — IssueListView builds that Link from
 * the teamId route param, which is present under the workspace route.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { IssueListView, type IssueViewMode } from '../Todolist/IssueListView';
import { NewIssueDialog } from '../Todolist/NewIssueDialog';
import { toAgentRef, toUiIssue, type ProjectNameMap } from '../Todolist/uiIssue';
import type { AgentRef, UiIssue } from '../Todolist/types';
import {
  listIssues, createIssue, type IssueCreatePayload,
} from '../../services/issuesService';
import { aiLibraryService } from '../../services/aiLibraryService';
import { useToast } from '../Toast';
import { useAuth } from '../../contexts/AuthContext';

interface WorkspaceTasksProps {
  projectId: string;
  projectName: string;
  teamId?: string;
}

export function WorkspaceTasks({ projectId, projectName, teamId }: WorkspaceTasksProps) {
  const { addToast } = useToast();
  const { currentUserId } = useAuth();

  const [issues, setIssues] = useState<UiIssue[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [agents, setAgents] = useState<AgentRef[]>([]);
  const [agentsById, setAgentsById] = useState<Record<string, AgentRef>>({});
  const [viewMode, setViewMode] = useState<IssueViewMode>('list');
  const [newIssueOpen, setNewIssueOpen] = useState(false);

  const projectsById: ProjectNameMap = { [projectId]: { name: projectName } };

  const refresh = useCallback(async (agentMap: Record<string, AgentRef>) => {
    setLoading(true);
    setError(null);
    try {
      const resp = await listIssues({ project_id: projectId, limit: 200 });
      setIssues(resp.items.map((r) => toUiIssue(r, agentMap, { [projectId]: { name: projectName } })));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load tasks');
    } finally {
      setLoading(false);
    }
  }, [projectId, projectName]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await aiLibraryService.listAgents();
        if (cancelled) return;
        const refs = list.map(toAgentRef);
        const map: Record<string, AgentRef> = {};
        for (const r of refs) map[r.id] = r;
        setAgents(refs);
        setAgentsById(map);
        await refresh(map);
      } catch {
        if (cancelled) return;
        await refresh({});
      }
    })();
    return () => { cancelled = true; };
  }, [refresh]);

  const handleCreate = async (payload: IssueCreatePayload) => {
    try {
      const created = await createIssue(payload);
      setIssues((prev) => [toUiIssue(created, agentsById, projectsById), ...prev]);
      setNewIssueOpen(false);
      addToast(`Created ${created.identifier}`, 'success');
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Create failed', 'error');
      throw err;
    }
  };

  return (
    <div className="h-full min-h-0 -mx-6 -mb-8">
      <IssueListView
        issues={issues}
        loading={loading}
        error={error}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        onNewIssue={() => setNewIssueOpen(true)}
        onRefresh={() => { void refresh(agentsById); }}
        agents={agents}
        currentUserId={currentUserId ?? undefined}
      />
      {newIssueOpen && (
        <NewIssueDialog
          agents={agents}
          teamId={teamId ? Number(teamId) : null}
          lockedProjectId={projectId}
          lockedProjectName={projectName}
          onClose={() => setNewIssueOpen(false)}
          onSubmit={handleCreate}
        />
      )}
    </div>
  );
}

export default WorkspaceTasks;
