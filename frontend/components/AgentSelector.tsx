import React, { useState, useRef, useEffect, useCallback } from 'react';
import { ChevronDown } from 'lucide-react';

export interface AgentOption {
  id: string;
  name: string;
  description?: string;
}

export interface AgentSelectorProps {
  agents: AgentOption[];
  selectedId: string | null;
  onSelect: (agentId: string) => void;
}

export function AgentSelector({
  agents,
  selectedId,
  onSelect,
}: AgentSelectorProps): React.ReactElement {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const selectedAgent = agents.find((a) => a.id === selectedId) ?? null;

  const handleSelect = useCallback(
    (agentId: string) => {
      onSelect(agentId);
      setOpen(false);
    },
    [onSelect],
  );

  // Close on outside click
  useEffect(() => {
    if (!open) return;

    function handleOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }

    document.addEventListener('mousedown', handleOutside);
    return () => document.removeEventListener('mousedown', handleOutside);
  }, [open]);

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 px-2 py-1 rounded hover:bg-zinc-800 text-sm text-zinc-300 transition-colors"
        title="Switch agent"
      >
        <span className="max-w-[120px] truncate">
          {selectedAgent ? selectedAgent.name : 'No agent'}
        </span>
        <ChevronDown
          size={12}
          className={`flex-shrink-0 text-zinc-500 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div className="absolute top-full right-0 mt-1 w-52 bg-zinc-800 border border-zinc-700 rounded-lg shadow-xl z-50 py-1 overflow-hidden">
          {agents.length === 0 ? (
            <p className="px-3 py-2 text-xs text-zinc-500">No agents available</p>
          ) : (
            agents.map((agent) => (
              <button
                key={agent.id}
                type="button"
                onClick={() => handleSelect(agent.id)}
                className={`w-full flex flex-col items-start px-3 py-2 text-left transition-colors hover:bg-zinc-700 ${
                  agent.id === selectedId ? 'bg-zinc-700' : ''
                }`}
              >
                <span className="text-sm text-zinc-200 truncate w-full">{agent.name}</span>
                {agent.description && (
                  <span className="text-[11px] text-zinc-500 mt-0.5 line-clamp-1">
                    {agent.description}
                  </span>
                )}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
