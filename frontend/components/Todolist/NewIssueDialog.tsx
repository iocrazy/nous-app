/**
 * Paperclip-style "New Issue" dialog (A8.3 wired to real createIssue).
 */

import React, { useState } from 'react';
import { X, Send } from 'lucide-react';
import type { AgentRef } from './types';
import type { IssuePriority, IssueCreatePayload } from '../../services/issuesService';
import { PRIORITY_LABEL, PRIORITY_ORDER, PriorityIcon } from './IssueStatusIcon';

interface NewIssueDialogProps {
  agents: AgentRef[];
  teamId: number | null;
  /** When set, the dialog opens in "sub-issue" mode and writes parent_id on submit. */
  parentId?: number | null;
  /** Preselect the assignee (paperclip "Assign Task" from an agent page). */
  defaultAgentId?: string | null;
  onClose: () => void;
  onSubmit: (payload: IssueCreatePayload) => Promise<void>;
}

export const NewIssueDialog: React.FC<NewIssueDialogProps> = ({
  agents, teamId, parentId, defaultAgentId, onClose, onSubmit,
}) => {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [agentId, setAgentId] = useState<string | null>(defaultAgentId ?? null);
  const [priority, setPriority] = useState<IssuePriority>('medium');
  const [submitting, setSubmitting] = useState(false);

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
        className="w-full max-w-xl bg-zinc-950 border border-zinc-800 rounded-lg shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between px-4 py-2.5 border-b border-zinc-800">
          <h2 className="text-sm font-semibold text-zinc-200">
            {parentId ? 'New Sub-issue' : 'New Issue'}
            {parentId && (
              <span className="ml-2 text-[11px] font-normal text-zinc-500">
                under #{parentId}
              </span>
            )}
          </h2>
          <button onClick={onClose} className="p-1 text-zinc-500 hover:text-zinc-300 rounded hover:bg-zinc-800">
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
            className="w-full bg-transparent text-base font-medium text-zinc-100 placeholder-zinc-600 focus:outline-none"
          />
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Add description…"
            rows={4}
            className="w-full bg-transparent text-sm text-zinc-300 placeholder-zinc-600 focus:outline-none resize-none"
          />
          <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-zinc-800/80">
            <select
              value={agentId ?? ''}
              onChange={(e) => setAgentId(e.target.value || null)}
              className="text-[11px] bg-zinc-900 border border-zinc-800 rounded px-2 py-1 text-zinc-300"
            >
              <option value="">Unassigned</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value as IssuePriority)}
              className="text-[11px] bg-zinc-900 border border-zinc-800 rounded px-2 py-1 text-zinc-300"
            >
              {PRIORITY_ORDER.map((p) => (
                <option key={p} value={p}>{PRIORITY_LABEL[p]}</option>
              ))}
            </select>
            <span className="ml-1"><PriorityIcon priority={priority} /></span>
          </div>
        </div>
        <footer className="flex items-center justify-end gap-2 px-4 py-2.5 border-t border-zinc-800">
          <button onClick={onClose} className="px-3 py-1 text-xs rounded border border-zinc-700 text-zinc-300 hover:bg-zinc-800">
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
