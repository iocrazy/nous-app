/**
 * Column visibility popover (A9.1, paperclip-style).
 *
 * Lets the user toggle which columns the IssueListView renders, with a
 * Reset defaults footer. Persists per-team via localStorage so the
 * choice sticks across reloads. The Title column is implicit (always
 * shown — it's the issue label) and not in the toggle list.
 */

import React, { useEffect, useRef } from 'react';
import { Check, X } from 'lucide-react';

export type IssueColumnKey =
  | 'status'
  | 'id'
  | 'assignee'
  | 'project'
  | 'parent'
  | 'tags'
  | 'updated';

export const ISSUE_COLUMN_DEFS: Array<{
  key: IssueColumnKey;
  label: string;
  hint: string;
}> = [
  { key: 'status', label: 'Status', hint: 'Issue state chip on the left edge.' },
  { key: 'id', label: 'ID', hint: 'Ticket identifier like MH-101.' },
  { key: 'assignee', label: 'Assignee', hint: 'Assigned agent or board user.' },
  { key: 'project', label: 'Project', hint: 'Linked project pill with its color.' },
  { key: 'parent', label: 'Parent issue', hint: 'Parent issue identifier and title.' },
  { key: 'tags', label: 'Tags', hint: 'Issue labels and tags.' },
  { key: 'updated', label: 'Last updated', hint: 'Latest visible activity time.' },
];

export const DEFAULT_VISIBLE_COLUMNS: IssueColumnKey[] = ['status', 'id', 'updated'];

const STORAGE_KEY_PREFIX = 'mediahub:todolist:columns';

export function loadVisibleColumns(scopeKey: string): Set<IssueColumnKey> {
  try {
    const raw = window.localStorage.getItem(`${STORAGE_KEY_PREFIX}:${scopeKey}`);
    if (!raw) return new Set(DEFAULT_VISIBLE_COLUMNS);
    const arr = JSON.parse(raw) as IssueColumnKey[];
    if (!Array.isArray(arr)) return new Set(DEFAULT_VISIBLE_COLUMNS);
    const valid = arr.filter((k) => ISSUE_COLUMN_DEFS.some((d) => d.key === k));
    return new Set(valid);
  } catch {
    return new Set(DEFAULT_VISIBLE_COLUMNS);
  }
}

export function saveVisibleColumns(scopeKey: string, cols: Set<IssueColumnKey>): void {
  try {
    window.localStorage.setItem(
      `${STORAGE_KEY_PREFIX}:${scopeKey}`,
      JSON.stringify(Array.from(cols)),
    );
  } catch {
    /* ignore quota errors */
  }
}

interface IssueColumnPickerProps {
  visible: Set<IssueColumnKey>;
  onChange: (next: Set<IssueColumnKey>) => void;
  onClose: () => void;
}

export const IssueColumnPicker: React.FC<IssueColumnPickerProps> = ({ visible, onChange, onClose }) => {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  const toggle = (key: IssueColumnKey) => {
    const next = new Set(visible);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onChange(next);
  };

  const reset = () => onChange(new Set(DEFAULT_VISIBLE_COLUMNS));

  const summaryText = Array.from(visible).join(', ') || 'none';

  return (
    <div
      ref={ref}
      className="absolute right-0 top-full mt-1 w-72 bg-ink-950 border border-ink-800 rounded-lg shadow-2xl z-30"
    >
      <header className="flex items-start justify-between px-3 py-2.5 border-b border-ink-800">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-ink-500">Desktop issue rows</div>
          <h3 className="text-sm font-semibold text-ink-200 mt-0.5">Choose which issue columns stay visible</h3>
        </div>
        <button
          onClick={onClose}
          className="p-1 text-ink-500 hover:text-ink-300 rounded hover:bg-ink-800"
        >
          <X size={12} />
        </button>
      </header>
      <ul className="py-1 max-h-80 overflow-y-auto">
        {ISSUE_COLUMN_DEFS.map((def) => {
          const checked = visible.has(def.key);
          return (
            <li key={def.key}>
              <button
                type="button"
                onClick={() => toggle(def.key)}
                className="w-full flex items-start gap-2 px-3 py-2 text-left hover:bg-ink-900/60 transition"
              >
                <span className="w-3.5 mt-0.5 inline-flex justify-center">
                  {checked && <Check size={12} className="text-emerald-400" />}
                </span>
                <span className="flex-1">
                  <span className={`block text-xs font-medium ${checked ? 'text-ink-100' : 'text-ink-300'}`}>
                    {def.label}
                  </span>
                  <span className="block text-[11px] text-ink-500 mt-0.5 leading-snug">
                    {def.hint}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <footer className="flex items-center justify-between px-3 py-2 border-t border-ink-800 text-[11px]">
        <button
          type="button"
          onClick={reset}
          className="text-ink-300 hover:text-ink-50"
        >
          Reset defaults
        </button>
        <span className="text-ink-500 truncate ml-2 max-w-[55%] text-right" title={summaryText}>
          {summaryText}
        </span>
      </footer>
    </div>
  );
};
