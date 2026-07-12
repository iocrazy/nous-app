// features/canvas-core/ui/ShortcutHelpPanel.tsx
//
// Keyboard-shortcut help overlay (opened with ? — the checklist's missing
// "supply the help panel" gap). A theme-aware glass card listing every
// wired canvas shortcut, grouped. Portal to <body> so RF's transformed
// ancestors can't collapse the fixed backdrop (the OutputLightbox trap).

import { X } from 'lucide-react';
import { useEffect, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';

import { CANVAS_SHORTCUT_GROUPS, formatKey } from './canvasShortcuts';

export interface ShortcutHelpPanelProps {
  open: boolean;
  onClose: () => void;
}

export function ShortcutHelpPanel({ open, onClose }: ShortcutHelpPanelProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const isMac = useMemo(
    () =>
      typeof navigator !== 'undefined' &&
      /mac|iphone|ipad|ipod/i.test(navigator.platform || navigator.userAgent),
    [],
  );

  useEffect(() => {
    if (open) rootRef.current?.focus();
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div
      ref={rootRef}
      data-testid="shortcut-help"
      role="dialog"
      aria-label="Keyboard shortcuts"
      tabIndex={-1}
      onClick={onClose}
      onKeyDown={(e) => {
        if (e.key === 'Escape') onClose();
      }}
      className="mh-lightbox-backdrop nodrag nopan nowheel fixed inset-0 z-[70] flex items-center justify-center outline-none"
    >
      <div
        className="mh-pop-in canvas-island max-h-[80vh] w-[min(640px,90vw)] overflow-y-auto p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-bold uppercase tracking-wide text-canvas-text">
            Keyboard Shortcuts
          </h2>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="rounded-lg p-1 text-canvas-muted hover:text-canvas-text"
          >
            <X size={16} />
          </button>
        </div>
        <div className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
          {CANVAS_SHORTCUT_GROUPS.map((group) => (
            <div key={group.title}>
              <div className="mb-1.5 text-[10px] font-bold uppercase tracking-wider text-canvas-muted">
                {group.title}
              </div>
              <ul className="space-y-1">
                {group.entries.map((entry) => (
                  <li
                    key={entry.label}
                    className="flex items-center justify-between gap-3 text-[13px] text-canvas-text"
                  >
                    <span>{entry.label}</span>
                    <span className="flex shrink-0 items-center gap-1">
                      {entry.keys.map((k, i) => (
                        <kbd
                          key={i}
                          className="rounded border border-canvas-line bg-canvas-line/30 px-1.5 py-0.5 text-[11px] font-medium text-canvas-muted"
                        >
                          {formatKey(k, isMac)}
                        </kbd>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>
    </div>,
    document.body,
  );
}
