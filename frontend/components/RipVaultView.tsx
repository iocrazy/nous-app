import React, { useRef } from 'react';
import {
  RefreshCw,
  LayoutGrid,
  LayoutList,
  Smartphone,
  Loader2,
  Download,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { useLibrary } from '../hooks/useLibrary';
import { useTeamContext } from '../contexts/TeamContext';
import { Video } from '../types';
import { CompactMediaCard } from './CompactMediaCard';
import { LibraryTable } from './LibraryTable';
import { LibraryFeed } from './LibraryFeed';
import { SemanticSearchBar } from './SemanticSearchBar';

export const RipVaultView: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { selectedTeamId } = useTeamContext();
  const loadMoreSentinel = useRef<HTMLDivElement>(null);

  const handleItemClick = (item: Video) => {
    if (!item.display_id) return;
    const teamPath = selectedTeamId ? `/t/${selectedTeamId}` : '';
    navigate(`${teamPath}/player/${item.display_id}?from=downloads`);
  };

  const {
    library,
    isLoadingLibrary,
    libraryError,
    filteredLibrary,
    hasMoreData,
    isLoadingMore,
    loadMoreRef,
    libraryViewMode,
    setLibraryViewMode,
    setSearchResults,
    isSearchActive,
    setIsSearchActive,
    setSearchQueryText,
    sharedVideoIds,
    loadLibraryData,
    handleUpdateLibraryItem,
  } = useLibrary({ isAuthenticated: true, selectedTeamId: null });

  return (
    <div className="flex-1 min-w-0 flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-3 px-6 py-3 border-b border-zinc-800">
        {/* Left: Title */}
        <div className="text-sm font-medium text-zinc-300 shrink-0">
          RipVault
        </div>

        {/* Center: Search */}
        <div className="flex-1 max-w-md">
          <SemanticSearchBar
            onSearch={(results, query) => {
              setSearchResults(results as any);
              setIsSearchActive(true);
              setSearchQueryText(query);
            }}
            onClear={() => {
              setSearchResults([]);
              setIsSearchActive(false);
              setSearchQueryText('');
            }}
            placeholder={t('library.searchPlaceholder', 'Search title, tags, notes...')}
            library={library as any}
          />
        </div>

        {/* Right: Refresh + View toggles */}
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={loadLibraryData}
            disabled={isLoadingLibrary}
            className="p-1.5 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors disabled:opacity-50"
            title="Refresh"
          >
            <RefreshCw size={14} className={isLoadingLibrary ? 'animate-spin' : ''} />
          </button>

          <div className="flex bg-zinc-800 rounded-lg p-0.5">
            <button
              onClick={() => setLibraryViewMode('grid')}
              className={`p-1.5 rounded-md transition-colors ${
                libraryViewMode === 'grid' ? 'bg-zinc-700 text-white' : 'text-zinc-400 hover:text-zinc-200'
              }`}
              title="Grid"
            >
              <LayoutGrid size={14} />
            </button>
            <button
              onClick={() => setLibraryViewMode('list')}
              className={`p-1.5 rounded-md transition-colors ${
                libraryViewMode === 'list' ? 'bg-zinc-700 text-white' : 'text-zinc-400 hover:text-zinc-200'
              }`}
              title="List"
            >
              <LayoutList size={14} />
            </button>
            <button
              onClick={() => setLibraryViewMode('feed')}
              className={`p-1.5 rounded-md transition-colors ${
                libraryViewMode === 'feed' ? 'bg-zinc-700 text-white' : 'text-zinc-400 hover:text-zinc-200'
              }`}
              title="Feed"
            >
              <Smartphone size={14} />
            </button>
          </div>

          {/* Count badge */}
          {!isLoadingLibrary && (
            <span className="bg-zinc-800 text-zinc-400 rounded-full px-2.5 py-0.5 text-xs font-medium">
              {filteredLibrary.length}
            </span>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {/* Error banner */}
        {libraryError && (
          <div className="mb-4 px-4 py-2 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
            {libraryError}
          </div>
        )}

        {/* Loading state */}
        {isLoadingLibrary && library.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full min-h-[300px]">
            <Loader2 size={24} className="animate-spin text-zinc-500 mb-3" />
            <p className="text-zinc-500 text-sm">{t('common.loading', 'Loading...')}</p>
          </div>
        ) : filteredLibrary.length === 0 ? (
          /* Empty state */
          <div className="flex flex-col items-center justify-center h-full min-h-[300px] text-center">
            <Download size={48} className="text-zinc-600 mb-4" />
            <p className="text-zinc-400 text-sm">{t('resources.noDownloads', 'No downloaded content yet')}</p>
            <p className="text-zinc-500 text-xs mt-1">{t('resources.noDownloadsHint', 'Use Parser to download media and they will appear here')}</p>
          </div>
        ) : (
          <>
            {/* Grid view */}
            {libraryViewMode === 'grid' && (
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
                {filteredLibrary.map((item) => (
                  <CompactMediaCard
                    key={item.platform_id}
                    data={item}
                    onClick={() => handleItemClick(item)}
                    isShared={sharedVideoIds.includes(item.platform_id)}
                  />
                ))}
              </div>
            )}

            {/* List view */}
            {libraryViewMode === 'list' && (
              <LibraryTable
                data={filteredLibrary}
                onUpdate={handleUpdateLibraryItem}
                onItemClick={(item) => handleItemClick(item)}
              />
            )}

            {/* Feed view */}
            {libraryViewMode === 'feed' && (
              <LibraryFeed data={filteredLibrary} />
            )}

            {/* Load more sentinel */}
            {!isSearchActive && libraryViewMode !== 'feed' && (
              <div ref={loadMoreRef} className="w-full py-8 flex justify-center">
                {isLoadingMore ? (
                  <Loader2 size={20} className="animate-spin text-zinc-500" />
                ) : hasMoreData ? (
                  <span className="text-zinc-600 text-xs">Scroll to load more</span>
                ) : library.length > 0 ? (
                  <span className="text-zinc-600 text-xs">All {library.length} items loaded</span>
                ) : null}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};
