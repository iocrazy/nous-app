// features/canvas-core/smart/WorkflowLibraryPicker.tsx
//
// Library import picker (②-4): a small glass panel over the composer that
// lists the team library's workflow JSON files (search endpoint, name
// filtered to *.json) — one click imports into the open canvas.
//
// Portal-rendered to document.body + `position: fixed` (DateRangePopover's
// pattern, frontend/components/common/DateRangePopover.tsx) so it floats
// above the composer's toolbar regardless of the toolbar's own overflow
// clipping: `overflow-x-auto` on the composer's `role="toolbar"` implicitly
// computes `overflow-y: auto` per CSS 2.1 §11.1.1 (only one axis can stay
// `visible`), which silently clipped this panel's old `absolute bottom-full`
// placement — real clicks landed on nothing (canvas-feel.spec.ts:228).
// Positioned off the trigger button's `getBoundingClientRect()`, opening
// upward by default with a measured-height flip to below when the viewport
// doesn't have room above (mirrors DateRangePopover's `place()`).

import { FileJson, Loader2, X } from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { useResourceSearch } from '../../../hooks/useResourceSearch';

const PICKER_WIDTH = 320; // w-80

export function WorkflowLibraryPicker({
  anchorEl,
  teamId,
  onPick,
  onClose,
}: {
  /** Trigger button's element — the panel positions itself relative to it. */
  anchorEl: HTMLElement | null;
  teamId: string;
  onPick: (resourceId: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('workflow');
  const { data, loading } = useResourceSearch(query, 'doc', teamId);
  const rows = data.results.filter((r) =>
    String(r.name ?? '').toLowerCase().endsWith('.json'),
  );

  const ref = useRef<HTMLDivElement>(null);

  const place = useCallback(() => {
    if (!anchorEl || !ref.current) return;
    const r = anchorEl.getBoundingClientRect();
    const h = ref.current.offsetHeight;
    // Default: open upward, 8px gap above the anchor (old `mb-2`). Flip to
    // below when there isn't room — measured against the *real* height, not
    // an estimate, same as DateRangePopover.
    let top = r.top - 8 - h;
    if (top < 8) top = r.bottom + 8;
    const centerX = r.left + r.width / 2;
    const left = Math.max(
      8,
      Math.min(centerX - PICKER_WIDTH / 2, window.innerWidth - PICKER_WIDTH - 8),
    );
    ref.current.style.top = `${top}px`;
    ref.current.style.left = `${left}px`;
  }, [anchorEl]);

  useLayoutEffect(place, [place, loading, rows.length]);

  useEffect(() => {
    if (!anchorEl) return undefined;
    window.addEventListener('resize', place);
    return () => window.removeEventListener('resize', place);
  }, [anchorEl, place]);

  useEffect(() => {
    if (!anchorEl) return undefined;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    const onMouseDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (ref.current && !ref.current.contains(target) && !anchorEl.contains(target)) {
        onClose();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    // Deferred so the click that opened the panel doesn't immediately close it.
    const timer = setTimeout(() => document.addEventListener('mousedown', onMouseDown), 0);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      clearTimeout(timer);
      document.removeEventListener('mousedown', onMouseDown);
    };
  }, [anchorEl, onClose]);

  if (!anchorEl) return null;

  return createPortal(
    <div
      ref={ref}
      data-testid="workflow-library-picker"
      className="mh-pop-in canvas-island z-50 w-80 p-2"
      style={{ position: 'fixed', top: 0, left: 0 }}
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
    </div>,
    document.body,
  );
}
