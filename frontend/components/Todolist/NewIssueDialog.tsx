/**
 * Paperclip-style "New Issue" dialog (A8.3 wired to real createIssue).
 */

import React, { useState } from 'react';
import { X, Send, FolderKanban, Check } from 'lucide-react';
import type { AgentRef } from './types';
import type { IssuePriority, IssueCreatePayload } from '../../services/issuesService';
import { PRIORITY_LABEL, PRIORITY_ORDER, PriorityIcon } from './IssueStatusIcon';
import { UiSelect } from '../ui';

/** Sentinel option value that triggers the inline "new project" input. */
const NEW_PROJECT_OPTION = '__new_project__';

interface NewIssueDialogProps {
  agents: AgentRef[];
  // Snowflake BIGINT — string preserves precision past 2^53; number kept for legacy callers.
  teamId: number | string | null;
  /** When set, the dialog opens in "sub-issue" mode and writes parent_id on submit. */
  parentId?: number | null;
  /** Preselect the assignee (paperclip "Assign Task" from an agent page). */
  defaultAgentId?: string | null;
  /** Workspace mode — pin the issue to this project (shown read-only, not a picker). */
  lockedProjectId?: string | null;
  lockedProjectName?: string;
  /** Team mode — offer a project picker. Ignored when lockedProjectId is set. */
  projects?: { id: string; name: string }[];
  /**
   * When provided (and not locked), the picker gains a "+ New project" row that
   * creates a project inline via this callback and auto-selects the result.
   * Resolves to the created project so the dialog can select it; rejects on
   * failure (the caller is expected to surface the error, e.g. via a toast).
   */
  onCreateProject?: (name: string) => Promise<{ id: string; name: string }>;
  onClose: () => void;
  onSubmit: (payload: IssueCreatePayload) => Promise<void>;
}

export const NewIssueDialog: React.FC<NewIssueDialogProps> = ({
  agents, teamId, parentId, defaultAgentId, lockedProjectId, lockedProjectName, projects, onCreateProject, onClose, onSubmit,
}) => {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [agentId, setAgentId] = useState<string | null>(defaultAgentId ?? null);
  const [priority, setPriority] = useState<IssuePriority>('medium');
  const [projectId, setProjectId] = useState<string | null>(lockedProjectId ?? null);
  const [submitting, setSubmitting] = useState(false);
  // Projects created inline this session, merged ahead of the passed-in list.
  const [extraProjects, setExtraProjects] = useState<{ id: string; name: string }[]>([]);
  const [creatingProject, setCreatingProject] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  const [projectBusy, setProjectBusy] = useState(false);

  const mergedProjects = [...(projects ?? []), ...extraProjects];
  const showPicker = !lockedProjectId && (!!onCreateProject || mergedProjects.length > 0);

  const commitNewProject = async () => {
    const name = newProjectName.trim();
    if (!name || projectBusy || !onCreateProject) return;
    setProjectBusy(true);
    try {
      const created = await onCreateProject(name);
      setExtraProjects((prev) => [...prev, created]);
      setProjectId(created.id);
      setCreatingProject(false);
      setNewProjectName('');
    } catch {
      // Caller surfaces the error (toast); keep the input open to retry.
    } finally {
      setProjectBusy(false);
    }
  };

  const submit = async () => {
    const t = title.trim();
    if (!t || submitting) return;
    setSubmitting(true);
    try {
      await onSubmit({
        title: t,
        description: description || undefined,
        priority,
        team_id: teamId ?? undefined,
        project_id: (lockedProjectId ?? projectId) ?? undefined,
        assignee_agent_id: agentId ?? undefined,
        parent_id: parentId ?? undefined,
      });
    } catch {
      // parent toasts; stay open
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 backdrop-blur-sm pt-24" onClick={onClose}>
      <div
        className="w-full max-w-xl bg-ink-950 border border-ink-800 rounded-lg shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between px-4 py-2.5 border-b border-ink-800">
          <h2 className="text-sm font-semibold text-ink-200">
            {parentId ? 'New Sub-issue' : 'New Issue'}
            {parentId && (
              <span className="ml-2 text-[11px] font-normal text-ink-500">
                under #{parentId}
              </span>
            )}
          </h2>
          <button onClick={onClose} className="p-1 text-ink-500 hover:text-ink-300 rounded hover:bg-ink-800">
            <X size={14} />
          </button>
        </header>
        <div className="p-4 space-y-3">
          <input
            type="text"
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Issue title"
            className="w-full bg-transparent text-base font-medium text-ink-100 placeholder-ink-600 focus:outline-none"
          />
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Add description…"
            rows={4}
            className="w-full bg-transparent text-sm text-ink-300 placeholder-ink-600 focus:outline-none resize-none"
          />
          <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-ink-800/80">
            <UiSelect
              value={agentId ?? ''}
              onChange={(e) => setAgentId(e.target.value || null)}
              className="h-8 text-xs"
            >
              <option value="">Unassigned</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </UiSelect>
            <UiSelect
              value={priority}
              onChange={(e) => setPriority(e.target.value as IssuePriority)}
              className="h-8 text-xs"
            >
              {PRIORITY_ORDER.map((p) => (
                <option key={p} value={p}>{PRIORITY_LABEL[p]}</option>
              ))}
            </UiSelect>
            <span className="ml-1"><PriorityIcon priority={priority} /></span>
            {lockedProjectId ? (
              <span className="ml-auto inline-flex items-center gap-1.5 px-2 py-1 text-xs rounded bg-ink-800/80 text-ink-300">
                <FolderKanban size={12} className="text-ink-500" />
                {lockedProjectName ?? 'Project'}
              </span>
            ) : showPicker ? (
              <UiSelect
                value={creatingProject ? NEW_PROJECT_OPTION : (projectId ?? '')}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v === NEW_PROJECT_OPTION) {
                    setCreatingProject(true);
                    return;
                  }
                  setCreatingProject(false);
                  setProjectId(v || null);
                }}
                className="ml-auto h-8 text-xs"
              >
                <option value="">No project</option>
                {mergedProjects.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
                {onCreateProject && (
                  <option value={NEW_PROJECT_OPTION}>+ New project</option>
                )}
              </UiSelect>
            ) : null}
          </div>
          {creatingProject && onCreateProject && (
            <div className="flex items-center gap-2 pt-1">
              <input
                type="text"
                autoFocus
                value={newProjectName}
                onChange={(e) => setNewProjectName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    void commitNewProject();
                  } else if (e.key === 'Escape') {
                    e.preventDefault();
                    setCreatingProject(false);
                    setNewProjectName('');
                  }
                }}
                placeholder="New project name…"
                className="flex-1 h-8 px-2 text-xs bg-ink-900 border border-ink-800 rounded focus:outline-none focus:ring-1 focus:ring-indigo-500/40 text-ink-200 placeholder-ink-600"
              />
              <button
                type="button"
                onClick={() => void commitNewProject()}
                disabled={!newProjectName.trim() || projectBusy}
                className="inline-flex items-center gap-1 px-2 h-8 text-xs rounded bg-indigo-500 text-white hover:bg-indigo-600 disabled:opacity-40"
                title="Create project"
              >
                <Check size={12} /> {projectBusy ? 'Creating…' : 'Add'}
              </button>
              <button
                type="button"
                onClick={() => { setCreatingProject(false); setNewProjectName(''); }}
                className="p-1 text-ink-500 hover:text-ink-300 rounded hover:bg-ink-800"
                title="Cancel"
              >
                <X size={12} />
              </button>
            </div>
          )}
        </div>
        <footer className="flex items-center justify-end gap-2 px-4 py-2.5 border-t border-ink-800">
          <button onClick={onClose} className="px-3 py-1 text-xs rounded border border-ink-700 text-ink-300 hover:bg-ink-800">
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={!title.trim() || submitting}
            className="inline-flex items-center gap-1 px-3 py-1 text-xs rounded bg-indigo-500 text-white hover:bg-indigo-600 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <Send size={11} /> {submitting ? 'Creating…' : 'Create'}
          </button>
        </footer>
      </div>
    </div>
  );
};
