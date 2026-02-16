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
      {/* Header — matches Library page layout */}
      <header className="hidden md:flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6 px-6 pt-6">
        <div>
          <h1 className="text-2xl font-bold text-white">RipVault</h1>
          <p className="text-zinc-400 text-sm">{t('library.subtitle', 'Manage your saved downloads')}</p>
        </div>

        <div className="flex flex-col md:flex-row gap-3 w-full md:w-auto">
          <button
            onClick={loadLibraryData}
            disabled={isLoadingLibrary}
            className="p-2 bg-zinc-900 rounded-lg border border-zinc-800 text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
            title="Refresh Data"
          >
            <RefreshCw size={20} className={isLoadingLibrary ? 'animate-spin' : ''} />
          </button>

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
            className="flex-1 md:w-80"
            library={library as any}
          />

          <div className="hidden md:flex bg-zinc-900 rounded-lg border border-zinc-800 p-1">
            <button
              onClick={() => setLibraryViewMode('list')}
              className={`p-1.5 rounded-md transition-all ${libraryViewMode === 'list' ? 'bg-zinc-800 text-white shadow-sm' : 'text-zinc-500 hover:text-zinc-300'}`}
              title="List View"
            >
              <LayoutList size={18} />
            </button>
            <button
              onClick={() => setLibraryViewMode('grid')}
              className={`p-1.5 rounded-md transition-all ${libraryViewMode === 'grid' ? 'bg-zinc-800 text-white shadow-sm' : 'text-zinc-500 hover:text-zinc-300'}`}
              title="Grid View"
            >
              <LayoutGrid size={18} />
            </button>
            <button
              onClick={() => setLibraryViewMode('feed')}
              className={`p-1.5 rounded-md transition-all ${libraryViewMode === 'feed' ? 'bg-zinc-800 text-white shadow-sm' : 'text-zinc-500 hover:text-zinc-300'}`}
              title="Feed View"
            >
              <Smartphone size={18} />
            </button>
          </div>
        </div>
      </header>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 pb-6">
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
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-3">
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
