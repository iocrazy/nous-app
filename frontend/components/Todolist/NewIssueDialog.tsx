/**
 * Paperclip-style "New Issue" dialog (A8 UI scaffold).
 * UI-only; submit is mocked until the backend layer arrives.
 */

import React, { useState } from 'react';
import { X, Send } from 'lucide-react';
import type { IssuePriority } from './types';
import { MOCK_AGENTS, MOCK_PROJECTS } from './fixtures';
import { PRIORITY_LABEL, PriorityIcon } from './IssueStatusIcon';

interface NewIssueDialogProps {
  onClose: () => void;
  onSubmit: (form: { title: string; description: string; agentId: string | null; projectId: string | null; priority: IssuePriority }) => void;
}

const PRIORITIES: IssuePriority[] = ['no_priority', 'urgent', 'high', 'medium', 'low'];

export const NewIssueDialog: React.FC<NewIssueDialogProps> = ({ onClose, onSubmit }) => {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [agentId, setAgentId] = useState<string | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [priority, setPriority] = useState<IssuePriority>('no_priority');

  const submit = () => {
    if (!title.trim()) return;
    onSubmit({ title: title.trim(), description, agentId, projectId, priority });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 backdrop-blur-sm pt-24" onClick={onClose}>
      <div
        className="w-full max-w-xl bg-zinc-950 border border-zinc-800 rounded-lg shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between px-4 py-2.5 border-b border-zinc-800">
          <h2 className="text-sm font-semibold text-zinc-200">New Issue</h2>
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
              value={projectId ?? ''}
              onChange={(e) => setProjectId(e.target.value || null)}
              className="text-[11px] bg-zinc-900 border border-zinc-800 rounded px-2 py-1 text-zinc-300"
            >
              <option value="">No project</option>
              {Object.values(MOCK_PROJECTS).map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
            <select
              value={agentId ?? ''}
              onChange={(e) => setAgentId(e.target.value || null)}
              className="text-[11px] bg-zinc-900 border border-zinc-800 rounded px-2 py-1 text-zinc-300"
            >
              <option value="">Unassigned</option>
              {Object.values(MOCK_AGENTS).map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value as IssuePriority)}
              className="text-[11px] bg-zinc-900 border border-zinc-800 rounded px-2 py-1 text-zinc-300"
            >
              {PRIORITIES.map((p) => (
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
            disabled={!title.trim()}
            className="inline-flex items-center gap-1 px-3 py-1 text-xs rounded bg-indigo-500 text-white hover:bg-indigo-600 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <Send size={11} /> Create
          </button>
        </footer>
      </div>
    </div>
  );
};
