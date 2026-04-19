import React, { useState, useCallback } from 'react';
import { ChevronDown, Plus, X } from 'lucide-react';

export interface SessionItem {
  id: string;
  title?: string;
  message_count?: number;
  updated_at?: string;
}

export interface SessionListProps {
  sessions: SessionItem[];
  activeSessionId: string | null;
  onSelect: (sessionId: string) => void;
  onNew: () => void;
  onDelete: (sessionId: string) => void;
}

function formatRelativeTime(isoString?: string): string {
  if (!isoString) return '';
  const diff = Date.now() - new Date(isoString).getTime();
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function SessionList({
  sessions,
  activeSessionId,
  onSelect,
  onNew,
  onDelete,
}: SessionListProps): React.ReactElement {
  const [collapsed, setCollapsed] = useState(false);
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  const handleDelete = useCallback(
    (e: React.MouseEvent, sessionId: string) => {
      e.stopPropagation();
      onDelete(sessionId);
    },
    [onDelete],
  );

  return (
    <div className="border-b border-zinc-800 flex-shrink-0">
      {/* Section header */}
      <div className="flex items-center gap-1 px-3 py-1.5">
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          className="flex items-center gap-1 flex-1 text-left text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
        >
          <ChevronDown
            size={12}
            className={`flex-shrink-0 transition-transform ${collapsed ? '-rotate-90' : ''}`}
          />
          <span className="font-medium uppercase tracking-wide">Sessions</span>
          <span className="ml-1 text-zinc-600">({sessions.length})</span>
        </button>

        <button
          type="button"
          onClick={onNew}
          className="p-0.5 rounded hover:bg-zinc-800 text-zinc-500 hover:text-zinc-300 transition-colors"
          title="New session"
        >
          <Plus size={13} />
        </button>
      </div>

      {/* Session items */}
      {!collapsed && (
        <div className="max-h-36 overflow-y-auto">
          {sessions.length === 0 ? (
            <p className="px-3 pb-2 text-xs text-zinc-600">No sessions yet</p>
          ) : (
            sessions.map((session) => (
              <div
                key={session.id}
                onClick={() => onSelect(session.id)}
                onMouseEnter={() => setHoveredId(session.id)}
                onMouseLeave={() => setHoveredId(null)}
                className={`flex items-center gap-2 px-3 py-1.5 cursor-pointer transition-colors group ${
                  session.id === activeSessionId
                    ? 'bg-zinc-800 text-zinc-200'
                    : 'hover:bg-zinc-800/60 text-zinc-400'
                }`}
              >
                <div className="flex-1 min-w-0">
                  <p className="text-xs truncate leading-tight">
                    {session.title || 'New conversation'}
                  </p>
                  <p className="text-[10px] text-zinc-600 mt-0.5 flex items-center gap-1.5">
                    {session.message_count != null && (
                      <span>{session.message_count} msgs</span>
                    )}
                    {session.updated_at && (
                      <span>{formatRelativeTime(session.updated_at)}</span>
                    )}
                  </p>
                </div>

                {hoveredId === session.id && (
                  <button
                    type="button"
                    onClick={(e) => handleDelete(e, session.id)}
                    className="flex-shrink-0 p-0.5 rounded hover:bg-zinc-700 text-zinc-600 hover:text-red-400 transition-colors"
                    title="Delete session"
                  >
                    <X size={11} />
                  </button>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
