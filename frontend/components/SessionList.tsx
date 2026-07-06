import React, { useState, useCallback, useRef, useEffect } from 'react';
import { ChevronDown, Pencil, Plus, X } from 'lucide-react';

export interface SessionItem {
  id: string;
  title?: string;
  message_count?: number;
  updated_at?: string;
  /** Set only in the cross-agent "All sessions" view — renders a badge. */
  agent_slug?: string;
}

export interface SessionListProps {
  sessions: SessionItem[];
  activeSessionId: string | null;
  onSelect: (sessionId: string) => void;
  onNew: () => void;
  onDelete: (sessionId: string) => void;
  /** When provided, a hover pencil enables inline title editing. */
  onRename?: (sessionId: string, title: string) => void;
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
  onRename,
}: SessionListProps): React.ReactElement {
  const [collapsed, setCollapsed] = useState(false);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  // Inline rename: which row is in edit mode + the draft title.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState('');
  const editInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingId) editInputRef.current?.select();
  }, [editingId]);

  const handleDelete = useCallback(
    (e: React.MouseEvent, sessionId: string) => {
      e.stopPropagation();
      onDelete(sessionId);
    },
    [onDelete],
  );

  const startEdit = useCallback(
    (e: React.MouseEvent, session: SessionItem) => {
      e.stopPropagation();
      setEditingId(session.id);
      setDraftTitle(session.title || '');
    },
    [],
  );

  const commitEdit = useCallback(() => {
    if (editingId && onRename) {
      const title = draftTitle.trim();
      if (title) onRename(editingId, title);
    }
    setEditingId(null);
  }, [editingId, draftTitle, onRename]);

  return (
    <div className="border-b border-ink-800 flex-shrink-0">
      {/* Section header */}
      <div className="flex items-center gap-1 px-3 py-1.5">
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          className="flex items-center gap-1 flex-1 text-left text-xs text-ink-500 hover:text-ink-300 transition-colors"
        >
          <ChevronDown
            size={12}
            className={`flex-shrink-0 transition-transform ${collapsed ? '-rotate-90' : ''}`}
          />
          <span className="font-medium uppercase tracking-wide">Sessions</span>
          <span className="ml-1 text-ink-600">({sessions.length})</span>
        </button>

        <button
          type="button"
          onClick={onNew}
          className="p-0.5 rounded hover:bg-ink-800 text-ink-500 hover:text-ink-300 transition-colors"
          title="New session"
        >
          <Plus size={13} />
        </button>
      </div>

      {/* Session items */}
      {!collapsed && (
        <div className="max-h-36 overflow-y-auto">
          {sessions.length === 0 ? (
            <p className="px-3 pb-2 text-xs text-ink-600">No sessions yet</p>
          ) : (
            sessions.map((session) => (
              <div
                key={session.id}
                onClick={() => {
                  if (editingId !== session.id) onSelect(session.id);
                }}
                onMouseEnter={() => setHoveredId(session.id)}
                onMouseLeave={() => setHoveredId(null)}
                className={`flex items-center gap-2 px-3 py-1.5 cursor-pointer transition-colors group ${
                  session.id === activeSessionId
                    ? 'bg-ink-800 text-ink-200'
                    : 'hover:bg-ink-800/60 text-ink-400'
                }`}
              >
                <div className="flex-1 min-w-0">
                  {editingId === session.id ? (
                    <input
                      ref={editInputRef}
                      type="text"
                      value={draftTitle}
                      maxLength={200}
                      onChange={(e) => setDraftTitle(e.target.value)}
                      onClick={(e) => e.stopPropagation()}
                      onBlur={commitEdit}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') commitEdit();
                        if (e.key === 'Escape') setEditingId(null);
                      }}
                      className="w-full rounded bg-ink-800 px-1 py-0.5 text-xs text-ink-200 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                    />
                  ) : (
                    <p className="text-xs truncate leading-tight">
                      {session.title || 'New conversation'}
                    </p>
                  )}
                  <p className="text-[10px] text-ink-600 mt-0.5 flex items-center gap-1.5">
                    {session.agent_slug && (
                      <span className="rounded bg-indigo-500/10 px-1 py-px font-medium text-indigo-400">
                        {session.agent_slug}
                      </span>
                    )}
                    {session.message_count != null && (
                      <span>{session.message_count} msgs</span>
                    )}
                    {session.updated_at && (
                      <span>{formatRelativeTime(session.updated_at)}</span>
                    )}
                  </p>
                </div>

                {hoveredId === session.id && editingId !== session.id && (
                  <div className="flex flex-shrink-0 items-center gap-0.5">
                    {onRename && (
                      <button
                        type="button"
                        onClick={(e) => startEdit(e, session)}
                        className="p-0.5 rounded hover:bg-ink-700 text-ink-600 hover:text-ink-300 transition-colors"
                        title="Rename session"
                      >
                        <Pencil size={11} />
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={(e) => handleDelete(e, session.id)}
                      className="p-0.5 rounded hover:bg-ink-700 text-ink-600 hover:text-red-400 transition-colors"
                      title="Delete session"
                    >
                      <X size={11} />
                    </button>
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
