/**
 * Sort menu (A9.3, paperclip-style).
 *
 * Six sort keys: Workflow / Status / Priority / Title / Created / Updated.
 * Clicking the same key toggles direction (asc ⇄ desc); clicking another
 * key picks it up at its natural default direction (text ascending,
 * status/priority by canonical order, dates descending).
 *
 * Sort state is held in the parent (IssueListView) and applied via
 * useMemo. Defaults: { key: 'updated', dir: 'desc' } — newest activity
 * at the top, matching the legacy behaviour.
 */

import React, { useEffect, useRef } from 'react';
import { ArrowDown, ArrowUp } from 'lucide-react';

export type IssueSortKey = 'workflow' | 'status' | 'priority' | 'title' | 'created' | 'updated';
export type SortDir = 'asc' | 'desc';

export interface IssueSort {
  key: IssueSortKey;
  dir: SortDir;
}

export const DEFAULT_SORT: IssueSort = { key: 'updated', dir: 'desc' };

const SORT_LABEL: Record<IssueSortKey, string> = {
  workflow: 'Workflow',
  status: 'Status',
  priority: 'Priority',
  title: 'Title',
  created: 'Created',
  updated: 'Updated',
};

const SORT_KEYS: IssueSortKey[] = ['workflow', 'status', 'priority', 'title', 'created', 'updated'];

const NATURAL_DIR: Record<IssueSortKey, SortDir> = {
  workflow: 'asc',
  status: 'asc',
  priority: 'asc',
  title: 'asc',
  created: 'desc',
  updated: 'desc',
};

interface IssueSortMenuProps {
  sort: IssueSort;
  onChange: (next: IssueSort) => void;
  onClose: () => void;
}

export const IssueSortMenu: React.FC<IssueSortMenuProps> = ({ sort, onChange, onClose }) => {
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

  const pick = (key: IssueSortKey) => {
    if (sort.key === key) {
      onChange({ key, dir: sort.dir === 'asc' ? 'desc' : 'asc' });
    } else {
      onChange({ key, dir: NATURAL_DIR[key] });
    }
  };

  return (
    <div
      ref={ref}
      className="absolute right-0 top-full mt-1 w-48 bg-ink-950 border border-ink-800 rounded-lg shadow-2xl z-30 py-1"
    >
      {SORT_KEYS.map((k) => {
        const active = sort.key === k;
        return (
          <button
            key={k}
            type="button"
            onClick={() => pick(k)}
            className={`w-full flex items-center gap-2 px-3 py-1.5 text-left text-xs transition ${
              active ? 'bg-ink-900 text-ink-100' : 'text-ink-300 hover:bg-ink-900/60'
            }`}
          >
            <span className="flex-1">{SORT_LABEL[k]}</span>
            {active && (
              sort.dir === 'asc'
                ? <ArrowUp size={11} className="text-ink-400" />
                : <ArrowDown size={11} className="text-ink-400" />
            )}
          </button>
        );
      })}
    </div>
  );
};

const STATUS_RANK: Record<string, number> = {
  in_progress: 0,
  todo: 1,
  in_review: 2,
  blocked: 3,
  backlog: 4,
  done: 5,
  cancelled: 6,
};

const PRIORITY_RANK: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

/**
 * Comparator for the chosen sort key/dir. The "workflow" key uses the
 * status rank (a richer order: in_progress before todo, etc.) so it
 * differs from the alphabetic "status" key by intent.
 */
export function compareIssues<T extends {
  status: string;
  priority: string;
  title: string;
  created_at: string;
  updated_at: string;
  last_activity_at?: string;
}>(a: T, b: T, sort: IssueSort): number {
  let cmp = 0;
  switch (sort.key) {
    case 'workflow':
      cmp = (STATUS_RANK[a.status] ?? 99) - (STATUS_RANK[b.status] ?? 99);
      break;
    case 'status':
      cmp = a.status.localeCompare(b.status);
      break;
    case 'priority':
      cmp = (PRIORITY_RANK[a.priority] ?? 99) - (PRIORITY_RANK[b.priority] ?? 99);
      break;
    case 'title':
      cmp = a.title.localeCompare(b.title);
      break;
    case 'created':
      cmp = a.created_at.localeCompare(b.created_at);
      break;
    case 'updated':
      cmp = (a.last_activity_at ?? a.updated_at).localeCompare(b.last_activity_at ?? b.updated_at);
      break;
  }
  return sort.dir === 'asc' ? cmp : -cmp;
}

export const SORT_LABEL_BY_KEY = SORT_LABEL;
