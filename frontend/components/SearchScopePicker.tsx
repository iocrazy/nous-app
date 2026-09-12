// frontend/components/SearchScopePicker.tsx
//
// Eagle-style search scope picker — funnel icon next to the search box,
// click opens a dropdown with one checkbox per searchable field. The user
// toggles which fields the text search scans; the choice persists in
// localStorage across reloads.

import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Filter } from 'lucide-react';
import type { SearchField } from '../services/searchService';
import { ALL_SEARCH_FIELDS, DEFAULT_SEARCH_FIELDS } from '../services/searchService';

const STORAGE_KEY = 'mediahub_search_scope_v2';

/** The pre-v2 default. Anyone whose stored scope is exactly this never
 *  touched the picker, so they get the widened default instead of being
 *  stranded on a scope that cannot find their tags. A stored set that
 *  differs in any way is a real choice and is preserved as-is. */
const LEGACY_STORAGE_KEY = 'mediahub_search_scope';
const LEGACY_DEFAULT: SearchField[] = [
  'title',
  'description',
  'author',
  'hashtags',
];

const sanitize = (parsed: unknown): SearchField[] | null => {
  if (!Array.isArray(parsed)) return null;
  const valid = parsed.filter((f): f is SearchField =>
    ALL_SEARCH_FIELDS.includes(f as SearchField),
  );
  return valid.length > 0 ? valid : null;
};

/** Read one key. A corrupt value is removed rather than left to throw on every
 *  load, and each key is read in its own try so a bad v2 value cannot skip the
 *  legacy migration below. */
const readScopeKey = (key: string): SearchField[] | null => {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    return sanitize(JSON.parse(raw));
  } catch {
    try {
      window.localStorage.removeItem(key);
    } catch {
      /* private mode — nothing to clean up */
    }
    return null;
  }
};

const isLegacyDefault = (scope: SearchField[]): boolean =>
  scope.length === LEGACY_DEFAULT.length &&
  LEGACY_DEFAULT.every((f) => scope.includes(f));

/** Persist the scope. Exported so every host writes the same key — an inline
 *  ``setItem`` in one view was still writing the pre-v2 key, which meant the
 *  choice made there was invisible to ``loadSearchScope``. */
export function saveSearchScope(scope: SearchField[]): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(scope));
  } catch {
    /* ignore quota / private mode errors */
  }
}

/** Read the selected scope, migrating the pre-v2 key exactly once.
 *
 *  The migration writes the resolved value to v2 and drops the old key, so the
 *  two can never disagree afterwards. Leaving the legacy key in place meant a
 *  stale tab (or a frontend rollback) still writing it would silently win over
 *  every choice made since. */
export function loadSearchScope(): SearchField[] {
  if (typeof window === 'undefined') return [...DEFAULT_SEARCH_FIELDS];

  const current = readScopeKey(STORAGE_KEY);
  if (current) return current;

  const legacy = readScopeKey(LEGACY_STORAGE_KEY);
  // Exactly the pre-v2 default means the picker was never opened, so the user
  // gets the widened default instead of being stranded on a scope that cannot
  // reach their tags. Anything else is a real choice and is preserved.
  const resolved =
    legacy && !isLegacyDefault(legacy) ? legacy : [...DEFAULT_SEARCH_FIELDS];

  if (legacy) {
    saveSearchScope(resolved);
    try {
      window.localStorage.removeItem(LEGACY_STORAGE_KEY);
    } catch {
      /* ignore private mode */
    }
  }

  return resolved;
}

interface SearchScopePickerProps {
  value: SearchField[];
  onChange: (next: SearchField[]) => void;
  /** Compact: smaller button, fewer paddings. Used in mobile overlay. */
  compact?: boolean;
}

export const SearchScopePicker: React.FC<SearchScopePickerProps> = ({
  value,
  onChange,
  compact = false,
}) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Click outside to close.
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const toggle = (field: SearchField) => {
    const next = value.includes(field)
      ? value.filter((f) => f !== field)
      : [...value, field];
    // Don't let the user disable everything — that would return zero
    // results regardless of query and be confusing. Re-add the field they
    // tried to remove if their action would empty the list.
    if (next.length === 0) return;
    onChange(next);
    saveSearchScope(next);
  };

  // Indigo tint when scope is "non-default" — helps user notice they have
  // a custom set active. Compares as a set so order doesn't matter.
  const isDefault =
    value.length === DEFAULT_SEARCH_FIELDS.length &&
    DEFAULT_SEARCH_FIELDS.every((f) => value.includes(f));
  const buttonClass = compact
    ? 'p-1.5 rounded-full text-ink-400 hover:text-ink-100 hover:bg-ink-800/40 transition-colors'
    : 'p-1.5 rounded-lg text-ink-500 hover:text-ink-200 hover:bg-ink-800/80 transition-colors';

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`${buttonClass} ${
          !isDefault ? 'text-[var(--accent-text)] hover:text-[var(--accent-text)]' : ''
        }`}
        title={t('search.scopeTooltip', 'Search scope')}
        aria-label={t('search.scopeTooltip', 'Search scope')}
      >
        <Filter size={compact ? 14 : 14} />
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-50 min-w-[200px] rounded-lg border border-ink-700 bg-ink-900 shadow-2xl">
          <div className="px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-ink-500 border-b border-ink-800">
            {t('search.scopeHeader', 'Search Scope')}
          </div>
          <div className="py-1">
            {ALL_SEARCH_FIELDS.map((field) => {
              const checked = value.includes(field);
              return (
                <button
                  key={field}
                  type="button"
                  onClick={() => toggle(field)}
                  className="flex w-full items-center justify-between gap-3 px-3 py-1.5 text-left text-[13px] text-ink-200 hover:bg-ink-800/60"
                >
                  <span>
                    {t(`search.scope.${field}`, defaultLabel(field))}
                  </span>
                  {checked && (
                    <Check size={14} className="shrink-0 text-[var(--accent-text)]" />
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};

function defaultLabel(field: SearchField): string {
  switch (field) {
    case 'title':
      return 'Title';
    case 'description':
      return 'Description';
    case 'author':
      return 'Author';
    case 'hashtags':
      return 'Hashtags';
    case 'transcript':
      return 'AI Transcript';
    case 'tags':
      return 'Tags';
    case 'notes':
      return 'Notes';
  }
}

export default SearchScopePicker;
