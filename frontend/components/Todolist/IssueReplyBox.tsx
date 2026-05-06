/**
 * Paperclip-style reply composer at the bottom of an issue detail
 * (A8.3 wired to real agents via aiLibraryService).
 */

import React, { useState } from 'react';
import { Paperclip, Send, ChevronDown } from 'lucide-react';
import type { AgentRef } from './types';

interface IssueReplyBoxProps {
  agents: AgentRef[];
  defaultAgentId?: string | null;
  /** Parent owns submission, returns rejection on error so we can stay in textarea. */
  onSubmit: (body: string, agentId: string | null) => Promise<void>;
  disabled?: boolean;
}

export const IssueReplyBox: React.FC<IssueReplyBoxProps> = ({ agents, defaultAgentId, onSubmit, disabled }) => {
  const [body, setBody] = useState('');
  const [agentId, setAgentId] = useState<string | null>(defaultAgentId ?? null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const selectedAgent = agents.find((a) => a.id === agentId) ?? null;

  const submit = async () => {
    const trimmed = body.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    try {
      await onSubmit(trimmed, agentId);
      setBody('');
    } catch {
      // parent toasts; keep body so user can retry
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="border border-zinc-800 rounded-lg bg-zinc-900/50 mx-4 mb-4">
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        placeholder="Reply"
        rows={3}
        disabled={disabled || submitting}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
            e.preventDefault();
            void submit();
          }
        }}
        className="w-full bg-transparent px-3 py-2 text-[14px] text-zinc-200 placeholder-zinc-600 focus:outline-none resize-none disabled:opacity-50"
      />
      <div className="flex items-center gap-2 px-2 pb-2 border-t border-zinc-800/80 pt-2">
        <button
          type="button"
          className="p-1.5 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded"
          title="Attach (not wired)"
          disabled
        >
          <Paperclip size={13} />
        </button>
        <span className="text-[12px] text-zinc-600 ml-1">⌘↩ to send</span>
        <div className="relative ml-auto">
          <button
            type="button"
            onClick={() => setPickerOpen((v) => !v)}
            className="inline-flex items-center gap-1 px-2 py-1 text-[12px] rounded bg-zinc-800 text-zinc-300 hover:bg-zinc-700"
          >
            {selectedAgent ? (
              <>
                <span
                  className={`inline-flex items-center justify-center rounded-full w-3.5 h-3.5 text-[10px] font-semibold text-white ${selectedAgent.avatar_color ?? 'bg-zinc-600'}`}
                >
                  {selectedAgent.name.slice(0, 1).toUpperCase()}
                </span>
                {selectedAgent.name}
              </>
            ) : (
              'No agent'
            )}
            <ChevronDown size={11} />
          </button>
          {pickerOpen && (
            <div className="absolute right-0 bottom-full mb-1 w-56 max-h-72 overflow-y-auto bg-zinc-900 border border-zinc-800 rounded shadow-lg z-10">
              <button
                onClick={() => { setAgentId(null); setPickerOpen(false); }}
                className="w-full flex items-center gap-2 px-2 py-1.5 text-[12px] text-zinc-400 hover:bg-zinc-800 text-left"
              >
                No agent (just comment)
              </button>
              <div className="border-t border-zinc-800/60" />
              {agents.length === 0 && (
                <div className="px-2 py-2 text-[12px] text-zinc-500 italic">No agents available</div>
              )}
              {agents.map((a) => (
                <button
                  key={a.id}
                  onClick={() => { setAgentId(a.id); setPickerOpen(false); }}
                  className="w-full flex items-center gap-2 px-2 py-1.5 text-[12px] text-zinc-300 hover:bg-zinc-800 text-left"
                >
                  <span className={`inline-flex items-center justify-center rounded-full w-4 h-4 text-[10px] text-white ${a.avatar_color ?? 'bg-zinc-600'}`}>
                    {a.name.slice(0, 1).toUpperCase()}
                  </span>
                  {a.name}
                </button>
              ))}
            </div>
          )}
        </div>
        <button
          onClick={submit}
          disabled={!body.trim() || disabled || submitting}
          className="inline-flex items-center gap-1 px-3 py-1 text-[12px] rounded bg-indigo-500 text-white hover:bg-indigo-600 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Send size={11} /> {submitting ? 'Sending…' : 'Send'}
        </button>
      </div>
    </div>
  );
};
