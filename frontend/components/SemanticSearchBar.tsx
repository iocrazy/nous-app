/**
 * SemanticSearchBar Component - AI-powered semantic search
 */

import React, { useState, useRef, useEffect } from 'react';
import {
  Search,
  Sparkles,
  X,
  Loader2,
  Clock,
  Tag,
  User,
  Filter,
  ChevronDown,
} from 'lucide-react';
import { semanticSearch, hybridSearch, localSearch, SearchResultItem, SearchResponse } from '../services/searchService';

interface VideoItem {
  id?: number;
  aweme_id: string;
  video_title?: string;
  video_desc?: string;
  author?: string;
  video_hashtag_name?: string;
  cover_url?: string;
  view_count?: number;
  created_at?: string;
}

interface SemanticSearchBarProps {
  onSearch: (results: SearchResultItem[], query: string) => void;
  onClear: () => void;
  placeholder?: string;
  className?: string;
  /** Library data for instant local search (keyword mode) */
  library?: VideoItem[];
}

type SearchMode = 'hybrid' | 'semantic' | 'keyword';

export const SemanticSearchBar: React.FC<SemanticSearchBarProps> = ({
  onSearch,
  onClear,
  placeholder = 'Search videos...',
  className = '',
  library = [],
}) => {
  const [query, setQuery] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [searchMode, setSearchMode] = useState<SearchMode>('keyword'); // Default to instant local search
  const [showModeDropdown, setShowModeDropdown] = useState(false);
  const [recentSearches, setRecentSearches] = useState<string[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Load recent searches from localStorage
  useEffect(() => {
    const saved = localStorage.getItem('recentSearches');
    if (saved) {
      setRecentSearches(JSON.parse(saved));
    }
  }, []);

  // Close dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setShowModeDropdown(false);
        setShowSuggestions(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const saveRecentSearch = (searchQuery: string) => {
    const updated = [searchQuery, ...recentSearches.filter((s) => s !== searchQuery)].slice(0, 5);
    setRecentSearches(updated);
    localStorage.setItem('recentSearches', JSON.stringify(updated));
  };

  const handleSearch = async (searchQuery?: string) => {
    const q = searchQuery || query;
    if (!q.trim()) return;

    setIsSearching(true);
    setShowSuggestions(false);
    saveRecentSearch(q);

    try {
      let results: SearchResultItem[] = [];

      if (searchMode === 'keyword') {
        // Quick Search: 先本地搜索，结果不足时补充后端搜索
        const localResults = localSearch(q, library, 20);
        console.log(`Local search: ${localResults.total} results in ${localResults.processing_time_ms}ms`);
        results = localResults.results;

        // 如果本地结果少于 5 条，自动查后端补充（可能有更多在未缓存的数据中）
        if (results.length < 5 && library.length >= 100) {
          console.log('Local results insufficient, querying backend...');
          try {
            const backendResults = await hybridSearch(q, {}, 20, 0);
            // 合并结果，去重
            const existingIds = new Set(results.map(r => r.aweme_id));
            for (const item of backendResults.results) {
              if (!existingIds.has(item.aweme_id)) {
                results.push(item);
                existingIds.add(item.aweme_id);
              }
            }
            console.log(`Combined: ${results.length} total results`);
          } catch (backendError) {
            console.warn('Backend search failed, using local results only:', backendError);
          }
        }
      } else if (searchMode === 'semantic') {
        // AI Search: 直接走后端
        const response = await semanticSearch(q, 20);
        results = response.results;
      } else {
        // Smart Search: 直接走后端
        const response = await hybridSearch(q, {}, 20, 0.5);
        results = response.results;
      }

      onSearch(results, q);
    } catch (error) {
      console.error('Search failed:', error);
      // Fallback to local search on error
      if (library.length > 0) {
        const fallback = localSearch(q, library, 20);
        onSearch(fallback.results, q);
      } else {
        onSearch([], q);
      }
    } finally {
      setIsSearching(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSearch();
    } else if (e.key === 'Escape') {
      setShowSuggestions(false);
    }
  };

  const handleClear = () => {
    setQuery('');
    onClear();
    inputRef.current?.focus();
  };

  const searchModes = [
    {
      id: 'keyword' as SearchMode,
      label: 'Quick Search',
      description: 'Instant local search',
      icon: Search,
    },
    {
      id: 'hybrid' as SearchMode,
      label: 'Smart Search',
      description: 'AI + keywords (slower)',
      icon: Sparkles,
    },
    {
      id: 'semantic' as SearchMode,
      label: 'AI Search',
      description: 'Find by meaning (slower)',
      icon: Sparkles,
    },
  ];

  const currentMode = searchModes.find((m) => m.id === searchMode)!;

  return (
    <div className={`relative ${className}`} ref={dropdownRef}>
      <div className="relative flex items-center">
        {/* Search Mode Selector */}
        <button
          onClick={() => setShowModeDropdown(!showModeDropdown)}
          className="absolute left-3 flex items-center gap-1.5 px-2 py-1 rounded-md bg-zinc-800/50 hover:bg-zinc-800 text-zinc-400 hover:text-zinc-300 transition-colors z-10"
        >
          <currentMode.icon size={14} className={searchMode === 'semantic' || searchMode === 'hybrid' ? 'text-indigo-400' : ''} />
          <ChevronDown size={12} />
        </button>

        {/* Search Input */}
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => {
            const newValue = e.target.value;
            setQuery(newValue);
            // Auto-clear when user manually deletes all text
            if (!newValue.trim()) {
              onClear();
            }
          }}
          onKeyDown={handleKeyDown}
          onFocus={() => setShowSuggestions(true)}
          placeholder={placeholder}
          className="w-full pl-20 pr-24 py-3 bg-zinc-900 border border-zinc-700 rounded-xl text-white placeholder-zinc-500 focus:outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/30 transition-all"
        />

        {/* Right Actions */}
        <div className="absolute right-3 flex items-center gap-2">
          {query && (
            <button
              onClick={handleClear}
              className="p-1.5 text-zinc-500 hover:text-white transition-colors"
            >
              <X size={16} />
            </button>
          )}
          <button
            onClick={() => handleSearch()}
            disabled={isSearching || !query.trim()}
            className="flex items-center gap-2 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:bg-zinc-700 disabled:text-zinc-500 text-white text-sm font-medium rounded-lg transition-colors"
          >
            {isSearching ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Search size={14} />
            )}
            Search
          </button>
        </div>
      </div>

      {/* Mode Dropdown */}
      {showModeDropdown && (
        <div className="absolute top-full left-0 mt-2 w-56 bg-zinc-900 border border-zinc-700 rounded-xl shadow-xl z-50 overflow-hidden">
          <div className="p-2 space-y-1">
            {searchModes.map((mode) => (
              <button
                key={mode.id}
                onClick={() => {
                  setSearchMode(mode.id);
                  setShowModeDropdown(false);
                }}
                className={`w-full flex items-start gap-3 px-3 py-2.5 rounded-lg transition-colors ${
                  searchMode === mode.id
                    ? 'bg-indigo-600/10 text-indigo-400'
                    : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
                }`}
              >
                <mode.icon size={18} className="mt-0.5 flex-shrink-0" />
                <div className="text-left">
                  <div className="text-sm font-medium">{mode.label}</div>
                  <div className="text-xs text-zinc-500">{mode.description}</div>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Suggestions / Recent Searches */}
      {showSuggestions && recentSearches.length > 0 && !query && (
        <div className="absolute top-full left-0 right-0 mt-2 bg-zinc-900 border border-zinc-700 rounded-xl shadow-xl z-40 overflow-hidden">
          <div className="p-2">
            <div className="px-3 py-1.5 text-xs font-medium text-zinc-500 uppercase tracking-wider">
              Recent Searches
            </div>
            {recentSearches.map((search, index) => (
              <button
                key={index}
                onClick={() => {
                  setQuery(search);
                  handleSearch(search);
                }}
                className="w-full flex items-center gap-3 px-3 py-2 text-sm text-zinc-400 hover:bg-zinc-800 hover:text-white rounded-lg transition-colors"
              >
                <Clock size={14} />
                {search}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Search Tips */}
      {showSuggestions && !recentSearches.length && !query && (
        <div className="absolute top-full left-0 right-0 mt-2 bg-zinc-900 border border-zinc-700 rounded-xl shadow-xl z-40 overflow-hidden">
          <div className="p-4 space-y-3">
            <div className="text-sm font-medium text-white">Search Tips</div>
            <div className="space-y-2 text-xs text-zinc-400">
              <div className="flex items-center gap-2">
                <Sparkles size={12} className="text-indigo-400" />
                <span>Use natural language: "funny cat videos"</span>
              </div>
              <div className="flex items-center gap-2">
                <Tag size={12} className="text-emerald-400" />
                <span>Search by tag: #cooking</span>
              </div>
              <div className="flex items-center gap-2">
                <User size={12} className="text-amber-400" />
                <span>Search by author: @username</span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default SemanticSearchBar;
