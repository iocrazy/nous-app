/**
 * Paperclip-style reply composer at the bottom of an issue detail
 * (A8 UI). Plain textarea + agent picker chip + Send.
 */

import React, { useState } from 'react';
import { Paperclip, Send, ChevronDown } from 'lucide-react';
import type { AgentRef } from './types';
import { MOCK_AGENTS } from './fixtures';

interface IssueReplyBoxProps {
  defaultAgent?: AgentRef;
  onSubmit: (body: string, agentId: string | null) => void;
}

export const IssueReplyBox: React.FC<IssueReplyBoxProps> = ({ defaultAgent, onSubmit }) => {
  const [body, setBody] = useState('');
  const [agentId, setAgentId] = useState<string | null>(defaultAgent?.id ?? null);
  const [pickerOpen, setPickerOpen] = useState(false);

  const allAgents = Object.values(MOCK_AGENTS);
  const selectedAgent = allAgents.find((a) => a.id === agentId) ?? defaultAgent ?? null;

  const submit = () => {
    if (!body.trim()) return;
    onSubmit(body.trim(), agentId);
    setBody('');
  };

  return (
    <div className="border border-zinc-800 rounded-lg bg-zinc-900/50 mx-4 mb-4">
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        placeholder="Reply"
        rows={3}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
            e.preventDefault();
            submit();
          }
        }}
        className="w-full bg-transparent px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none resize-none"
      />
      <div className="flex items-center gap-2 px-2 pb-2 border-t border-zinc-800/80 pt-2">
        <button
          type="button"
          className="p-1.5 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded"
          title="Attach"
        >
          <Paperclip size={13} />
        </button>
        <span className="text-[10px] text-zinc-600 ml-1">Tip: ⌘↩ to send</span>
        <div className="relative ml-auto">
          <button
            type="button"
            onClick={() => setPickerOpen((v) => !v)}
            className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded bg-zinc-800 text-zinc-300 hover:bg-zinc-700"
          >
            {selectedAgent ? (
              <>
                <span
                  className={`inline-flex items-center justify-center rounded-full w-3.5 h-3.5 text-[8px] font-semibold text-white ${selectedAgent.avatar_color ?? 'bg-zinc-600'}`}
                >
                  {selectedAgent.name.slice(0, 1)}
                </span>
                {selectedAgent.name}
              </>
            ) : (
              'No agent'
            )}
            <ChevronDown size={11} />
          </button>
          {pickerOpen && (
            <div className="absolute right-0 bottom-full mb-1 w-44 bg-zinc-900 border border-zinc-800 rounded shadow-lg z-10">
              <button
                onClick={() => { setAgentId(null); setPickerOpen(false); }}
                className="w-full flex items-center gap-2 px-2 py-1.5 text-[11px] text-zinc-400 hover:bg-zinc-800 text-left"
              >
                No agent (just comment)
              </button>
              <div className="border-t border-zinc-800/60" />
              {allAgents.map((a) => (
                <button
                  key={a.id}
                  onClick={() => { setAgentId(a.id); setPickerOpen(false); }}
                  className="w-full flex items-center gap-2 px-2 py-1.5 text-[11px] text-zinc-300 hover:bg-zinc-800 text-left"
                >
                  <span className={`inline-flex items-center justify-center rounded-full w-4 h-4 text-[9px] text-white ${a.avatar_color ?? 'bg-zinc-600'}`}>
                    {a.name.slice(0, 1)}
                  </span>
                  {a.name}
                </button>
              ))}
            </div>
          )}
        </div>
        <button
          onClick={submit}
          disabled={!body.trim()}
          className="inline-flex items-center gap-1 px-3 py-1 text-[11px] rounded bg-indigo-500 text-white hover:bg-indigo-600 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Send size={11} /> Send
        </button>
      </div>
    </div>
  );
};
