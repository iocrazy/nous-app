// features/canvas-core/smart/WorkflowLibraryPicker.tsx
//
// Library import picker (②-4): a small glass panel over the composer that
// lists the team library's workflow JSON files (search endpoint, name
// filtered to *.json) — one click imports into the open canvas.

import { FileJson, Loader2, X } from 'lucide-react';
import { useState } from 'react';

import { useResourceSearch } from '../../../hooks/useResourceSearch';

export function WorkflowLibraryPicker({
  teamId,
  onPick,
  onClose,
}: {
  teamId: string;
  onPick: (resourceId: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('workflow');
  const { data, loading } = useResourceSearch(query, 'doc', teamId);
  const rows = data.results.filter((r) =>
    String(r.name ?? '').toLowerCase().endsWith('.json'),
  );

  return (
    <div
      data-testid="workflow-library-picker"
      className="canvas-island absolute bottom-full left-1/2 mb-2 w-80 -translate-x-1/2 p-2"
    >
      <div className="mb-1.5 flex items-center justify-between px-1">
        <span className="mh-node-title">Workflow Library</span>
        <button aria-label="Close" onClick={onClose} className="text-canvas-muted hover:text-canvas-text">
          <X size={13} />
        </button>
      </div>
      <input
        autoFocus
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search workflows…"
        className="mb-1.5 w-full rounded-full border border-canvas-line bg-transparent px-3 py-1 text-xs text-canvas-text outline-none placeholder:text-canvas-muted focus:ring-1 focus:ring-canvas-strong/40"
      />
      <div className="max-h-56 overflow-y-auto">
        {loading && (
          <div className="flex h-12 items-center justify-center">
            <Loader2 size={14} className="animate-spin text-canvas-muted" />
          </div>
        )}
        {!loading && rows.length === 0 && (
          <div className="px-2 py-3 text-center text-xs text-canvas-muted">
            No workflow files in the library yet — use Save first.
          </div>
        )}
        {rows.map((r) => (
          <button
            key={String(r.id)}
            onClick={() => onPick(String(r.id))}
            className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs text-canvas-text hover:bg-canvas-line/40"
          >
            <FileJson size={13} className="shrink-0 text-canvas-muted" />
            <span className="truncate">{String(r.name ?? r.id)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
