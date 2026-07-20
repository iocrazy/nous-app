/**
 * LibraryPickerModal — the shared "Add from library" node picker (spec §4/§5).
 * Groups the 11-node bank by phase and calls `onPick` with the chosen stage.
 *
 * Reused by both the team template editor (append a node-bank stage to the
 * template draft) and the project workspace (W3-1, add a node to a live
 * instance). When `onAddBlank` is supplied it also renders a footer to create a
 * blank, named stage — the instance add path needs it; the template path omits
 * it and the footer disappears.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { X } from 'lucide-react';
import type { StageLibraryItem } from '../../types';

interface LibraryPickerModalProps {
  items: StageLibraryItem[];
  onPick: (item: StageLibraryItem) => void;
  onClose: () => void;
  /** When present, a footer lets the user add a blank stage by name (W3-1). */
  onAddBlank?: (name: string) => void;
}

export const LibraryPickerModal: React.FC<LibraryPickerModalProps> = ({
  items,
  onPick,
  onClose,
  onAddBlank,
}) => {
  const [blankName, setBlankName] = useState('');

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const byPhase = useMemo(() => {
    const groups: Record<string, StageLibraryItem[]> = {};
    for (const it of items) (groups[it.phase] ??= []).push(it);
    return groups;
  }, [items]);

  const submitBlank = () => {
    const name = blankName.trim();
    if (name && onAddBlank) onAddBlank(name);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 backdrop-blur-sm pt-24"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-label="Add node from library"
        className="w-full max-w-md overflow-hidden rounded-xl border border-line-strong bg-island shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        data-testid="workflow-library-picker"
      >
        <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <h2 className="text-sm font-semibold text-ink-100">Add from library</h2>
          <button onClick={onClose} className="rounded p-1 text-ink-500 hover:bg-ink-800 hover:text-ink-300">
            <X size={14} />
          </button>
        </header>
        <div className="max-h-[60vh] overflow-y-auto p-2">
          {Object.entries(byPhase).map(([phase, list]) => (
            <div key={phase} className="mb-2">
              <div className="px-2 py-1 text-[10px] font-medium uppercase tracking-wider text-ink-600">
                {phase}
              </div>
              {list.map((it) => (
                <button
                  key={it.id}
                  onClick={() => onPick(it)}
                  data-testid="workflow-library-item"
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] text-ink-200 hover:bg-ink-800"
                >
                  <span className="flex-1 truncate">{it.name}</span>
                  {it.default_role_label && (
                    <span className="text-[11px] text-ink-600">{it.default_role_label}</span>
                  )}
                </button>
              ))}
            </div>
          ))}
          {items.length === 0 && (
            <div className="px-2 py-4 text-center text-[13px] text-ink-500">
              No library nodes
            </div>
          )}
        </div>
        {onAddBlank && (
          <footer className="flex items-center gap-2 border-t border-line px-3 py-2.5">
            <input
              value={blankName}
              onChange={(e) => setBlankName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  submitBlank();
                }
              }}
              placeholder="Blank stage name…"
              data-testid="workflow-blank-stage-input"
              className="min-w-0 flex-1 rounded-md border border-line bg-ink-900 px-2.5 py-1.5 text-[13px] text-ink-100 placeholder:text-ink-600 focus:border-line-strong focus:outline-none"
            />
            <button
              onClick={submitBlank}
              disabled={!blankName.trim()}
              data-testid="workflow-add-blank-stage"
              className="shrink-0 rounded-md border border-line px-2.5 py-1.5 text-[12.5px] text-ink-300 transition hover:border-line-strong hover:text-ink-100 disabled:opacity-40"
            >
              Add blank
            </button>
          </footer>
        )}
      </div>
    </div>
  );
};
