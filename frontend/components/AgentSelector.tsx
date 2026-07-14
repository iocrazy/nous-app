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
        className="flex items-center gap-1 px-2 py-1 rounded hover:bg-ink-800 text-sm text-ink-300 transition-colors"
        title="Switch agent"
      >
        <span className="max-w-[120px] truncate">
          {selectedAgent ? selectedAgent.name : 'No agent'}
        </span>
        <ChevronDown
          size={12}
          className={`flex-shrink-0 text-ink-500 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div className="absolute top-full right-0 z-50 mt-1 w-56 overflow-hidden rounded-[8px] border border-ink-700 bg-card p-1 shadow-[0_12px_34px_rgba(0,0,0,0.22)]">
          {agents.length === 0 ? (
            <p className="px-3 py-2 text-xs text-content-3">No agents available</p>
          ) : (
            agents.map((agent) => {
              const isSelected = agent.id === selectedId;
              return (
                <button
                  key={agent.id}
                  type="button"
                  onClick={() => handleSelect(agent.id)}
                  className={`flex w-full flex-col items-start rounded-[6px] px-3 py-2 text-left transition-colors ${
                    isSelected
                      ? 'bg-[color-mix(in_srgb,var(--accent)_13%,transparent)]'
                      : 'hover:bg-ink-700'
                  }`}
                >
                  <span className="w-full truncate text-sm text-content">
                    {agent.name}
                  </span>
                  {agent.description && (
                    <span className="mt-0.5 line-clamp-1 text-[11px] text-content-3">
                      {agent.description}
                    </span>
                  )}
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
