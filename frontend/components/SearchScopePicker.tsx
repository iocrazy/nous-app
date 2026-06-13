// frontend/components/SearchScopePicker.tsx
//
// Eagle-style search scope picker — funnel icon next to the search box,
// click opens a dropdown with field checkboxes (Title / Description /
// Author / Hashtags). User toggles which fields the text search scans.
// Persists in localStorage so the choice survives page reloads.

import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Filter } from 'lucide-react';
import type { SearchField } from '../services/searchService';
import { ALL_SEARCH_FIELDS, DEFAULT_SEARCH_FIELDS } from '../services/searchService';

const STORAGE_KEY = 'mediahub_search_scope';

/** Read selected scope from localStorage; default = the four
 *  parsed_media direct columns. The three Eagle extras (transcript /
 *  tags / notes) are opt-in because they're heavier — user must check
 *  them once and the choice persists. */
export function loadSearchScope(): SearchField[] {
  if (typeof window === 'undefined') return DEFAULT_SEARCH_FIELDS;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_SEARCH_FIELDS;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return DEFAULT_SEARCH_FIELDS;
    const valid = parsed.filter((f): f is SearchField =>
      ALL_SEARCH_FIELDS.includes(f as SearchField),
    );
    return valid.length > 0 ? valid : DEFAULT_SEARCH_FIELDS;
  } catch {
    return DEFAULT_SEARCH_FIELDS;
  }
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
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* ignore quota / private mode errors */
    }
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
          !isDefault ? 'text-indigo-400 hover:text-indigo-300' : ''
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
                    <Check size={14} className="shrink-0 text-indigo-400" />
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
