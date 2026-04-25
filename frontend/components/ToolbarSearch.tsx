/**
 * ToolbarSearch — Compact unified search bar for toolbars.
 * Supports 3 modes: Quick (instant local), Smart (AI + keywords), AI (semantic).
 * Keyword mode fires onQueryChange on every keystroke for instant filtering.
 * AI modes fire onAISearch on Enter.
 */

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Search, Sparkles, X, Loader2, ChevronDown, Check } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { SearchField } from '../services/searchService';
import { ALL_SEARCH_FIELDS } from '../services/searchService';

export type SearchMode = 'keyword' | 'hybrid' | 'semantic';

interface ToolbarSearchProps {
  /** Called on every keystroke in keyword mode */
  onQueryChange: (query: string) => void;
  /** Called on Enter in AI modes (hybrid / semantic) */
  onAISearch: (query: string, mode: 'hybrid' | 'semantic') => Promise<void>;
  /** Called when search is cleared */
  onClear: () => void;
  /** Whether an AI search is in progress */
  isSearching?: boolean;
  placeholder?: string;
  className?: string;
  /** Search scope (which fields to scan). When provided, the magnifier
   *  dropdown also shows scope checkboxes — Eagle-style. */
  searchScope?: SearchField[];
  onSearchScopeChange?: (next: SearchField[]) => void;
}

const SCOPE_LABELS: Record<SearchField, string> = {
  title: 'Title',
  description: 'Description',
  author: 'Author',
  hashtags: 'Hashtags',
  transcript: 'AI Transcript',
  tags: 'Tags',
  notes: 'Notes',
};

const MODES: Array<{ id: SearchMode; label: string; desc: string; Icon: typeof Search }> = [
  { id: 'keyword', label: 'Quick Search', desc: 'Instant local filter', Icon: Search },
  { id: 'hybrid',  label: 'Smart Search', desc: 'AI + keywords', Icon: Sparkles },
  { id: 'semantic', label: 'AI Search',   desc: 'Search by meaning', Icon: Sparkles },
];

export const ToolbarSearch: React.FC<ToolbarSearchProps> = ({
  onQueryChange,
  onAISearch,
  onClear,
  isSearching = false,
  placeholder,
  className = '',
  searchScope,
  onSearchScopeChange,
}) => {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<SearchMode>('keyword');
  const [showDropdown, setShowDropdown] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  const currentMode = MODES.find(m => m.id === mode)!;
  const isAI = mode !== 'keyword';

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const handleChange = useCallback((value: string) => {
    setQuery(value);
    if (!value.trim()) {
      onClear();
      return;
    }
    if (!isAI) {
      onQueryChange(value);
    }
  }, [isAI, onQueryChange, onClear]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && query.trim()) {
      if (isAI) {
        onAISearch(query.trim(), mode as 'hybrid' | 'semantic');
      } else {
        // In keyword mode, Enter also triggers (already filtered, but can be used to confirm)
        onQueryChange(query);
      }
    }
    if (e.key === 'Escape') {
      setShowDropdown(false);
      inputRef.current?.blur();
    }
  }, [query, isAI, mode, onAISearch, onQueryChange]);

  const handleClear = useCallback(() => {
    setQuery('');
    onClear();
    inputRef.current?.focus();
  }, [onClear]);

  const handleModeChange = useCallback((newMode: SearchMode) => {
    setMode(newMode);
    setShowDropdown(false);
    // If switching to keyword with existing query, trigger filter
    if (newMode === 'keyword' && query.trim()) {
      onClear(); // Clear any AI results first
      onQueryChange(query);
    }
    // If switching to AI mode, clear instant filter
    if (newMode !== 'keyword' && query.trim()) {
      onClear();
    }
    inputRef.current?.focus();
  }, [query, onQueryChange, onClear]);

  return (
    <div className={`relative ${className}`} ref={wrapperRef}>
      <div className="relative flex items-center">
        {/* Mode selector button */}
        <button
          onClick={() => setShowDropdown(!showDropdown)}
          className="absolute left-2 z-10 flex items-center gap-0.5 px-1 py-0.5 rounded text-zinc-500 hover:text-zinc-300 hover:bg-zinc-700/50 transition-colors"
        >
          <currentMode.Icon
            size={13}
            className={isAI ? 'text-indigo-400' : ''}
          />
          <ChevronDown size={10} />
        </button>

        {/* Input */}
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => handleChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder || (isAI ? t('search.aiPlaceholder', 'AI search... ↵') : t('search.placeholder', 'Search...'))}
          className={`w-full pl-[42px] pr-7 py-1.5 text-xs bg-zinc-800/60 border rounded-lg text-zinc-200 placeholder-zinc-500 focus:outline-none transition-colors ${
            isAI
              ? 'border-indigo-500/30 focus:border-indigo-500/60 focus:ring-1 focus:ring-indigo-500/20'
              : 'border-zinc-700/50 focus:border-indigo-500'
          }`}
        />

        {/* Right side: loading / clear */}
        <div className="absolute right-2 flex items-center gap-1">
          {isSearching && (
            <Loader2 size={12} className="animate-spin text-indigo-400" />
          )}
          {query && !isSearching && (
            <button
              onClick={handleClear}
              className="text-zinc-500 hover:text-zinc-300 transition-colors"
            >
              <X size={12} />
            </button>
          )}
        </div>
      </div>

      {/* Mode dropdown — also embeds the search-scope checkbox section
          when the host passes ``searchScope`` + ``onSearchScopeChange``. */}
      {showDropdown && (
        <div className="absolute top-full left-0 mt-1 w-56 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl z-50 overflow-hidden py-1">
          {MODES.map((m) => (
            <button
              key={m.id}
              onClick={() => handleModeChange(m.id)}
              className={`w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors ${
                mode === m.id
                  ? 'bg-indigo-600/10 text-indigo-400'
                  : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
              }`}
            >
              <m.Icon size={14} className={m.id !== 'keyword' ? 'text-indigo-400' : ''} />
              <div>
                <div className="text-xs font-medium">{m.label}</div>
                <div className="text-[10px] text-zinc-500">{m.desc}</div>
              </div>
            </button>
          ))}
          {searchScope && onSearchScopeChange && (
            <>
              <div className="my-1 border-t border-zinc-800" />
              <div className="px-3 py-1.5 text-[11px] font-medium uppercase tracking-wider text-zinc-500">
                {t('search.scopeHeader', 'Search Scope')}
              </div>
              {ALL_SEARCH_FIELDS.map((field) => {
                const checked = searchScope.includes(field);
                return (
                  <button
                    key={field}
                    type="button"
                    onClick={() => {
                      const next = checked
                        ? searchScope.filter((f) => f !== field)
                        : [...searchScope, field];
                      // Refuse to disable everything — empty scope returns
                      // zero results and confuses the user.
                      if (next.length === 0) return;
                      onSearchScopeChange(next);
                    }}
                    className="flex w-full items-center justify-between gap-3 px-3 py-1.5 text-left text-[12px] text-zinc-300 hover:bg-zinc-800/60"
                  >
                    <span>{t(`search.scope.${field}`, SCOPE_LABELS[field])}</span>
                    {checked && <Check size={13} className="shrink-0 text-indigo-400" />}
                  </button>
                );
              })}
            </>
          )}
        </div>
      )}
    </div>
  );
};
