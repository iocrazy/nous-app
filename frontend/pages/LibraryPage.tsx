import React, { useState } from 'react';
import {
  Loader2, LayoutGrid, LayoutList, Smartphone, Folder,
  Video as VideoIcon, ArrowLeft, RefreshCw, CloudOff, Search, X,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useLocation } from 'react-router-dom';
import { Video } from '../types';
import { CompactMediaCard } from '../components/CompactMediaCard';
import { LibraryTable } from '../components/LibraryTable';
import { LibraryFeed } from '../components/LibraryFeed';
import { VideoDetailPanel } from '../components/VideoDetailPanel';
import { SemanticSearchBar } from '../components/SemanticSearchBar';
import { useAuth } from '../contexts/AuthContext';
import { useTeamContext } from '../contexts/TeamContext';
import { useLibrary } from '../hooks/useLibrary';

export function LibraryPage() {
  const { t } = useTranslation();
  const { selectedTeamId } = useTeamContext();

  const [currentResult, setCurrentResult] = useState<Video | null>(null);

  const {
    library, setLibrary, isLoadingLibrary, libraryError,
    selectedLibraryItem, setSelectedLibraryItem,
    filteredLibrary, hasMoreData, isLoadingMore, loadMoreRef,
    libraryViewMode, setLibraryViewMode, activeLibraryTab,
    searchQuery, setSearchQuery, searchResults, setSearchResults,
    isSearchActive, setIsSearchActive, searchQueryText, setSearchQueryText,
    collections, activeCollectionId, setActiveCollectionId, collectionVideoIds,
    isCreateCollectionModalOpen, setIsCreateCollectionModalOpen,
    selectedVideoCollectionIds, sharedVideoIds,
    loadLibraryData, handleCreateCollection, handleToggleVideoCollection,
    handleUpdateLibraryItem, handleDeleteLibraryItem,
  } = useLibrary({
    isAuthenticated: true,
    selectedTeamId,
    onVideoRealtimeUpdate: (video) => {
      setCurrentResult(prev => prev?.platform_id === video.platform_id ? video : prev);
    },
  });

  const [isMobileSearchOpen, setIsMobileSearchOpen] = useState(false);

  return (
    <div className="animate-in fade-in slide-in-from-bottom-4 duration-500 h-full flex flex-col">
      {selectedLibraryItem ? (
        <div className="flex flex-col h-full p-4 md:p-0">
           <div className="flex items-center gap-3 mb-4 shrink-0">
              <button
                onClick={() => setSelectedLibraryItem(null)}
                className="p-2 -ml-2 rounded-full hover:bg-zinc-800 text-zinc-400 hover:text-white transition-colors"
              >
                 <ArrowLeft size={24} />
              </button>
              <h2 className="text-xl font-bold text-white">Media Details</h2>
           </div>
           <div className="flex-1 min-h-0">
              <VideoDetailPanel
                video={selectedLibraryItem}
                onClose={() => setSelectedLibraryItem(null)}
                onUpdate={handleUpdateLibraryItem}
                onDelete={handleDeleteLibraryItem}
                collections={collections}
                videoCollectionIds={selectedVideoCollectionIds}
                onToggleCollection={handleToggleVideoCollection}
                onCreateCollection={handleCreateCollection}
              />
           </div>
        </div>
      ) : (
        <>
          {/* Desktop Header */}
          <header className="hidden md:flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
            <div>
              <div className="flex items-center gap-3">
                {activeCollectionId && (
                  <button
                    onClick={() => setActiveCollectionId(null)}
                    className="p-2 -ml-2 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
                    title={t('common.back')}
                  >
                    <ArrowLeft size={20} />
                  </button>
                )}
                {activeCollectionId && (
                  <div className="p-2 bg-indigo-500/20 rounded-lg">
                    <Folder size={20} className="text-indigo-400" />
                  </div>
                )}
                <h1 className="text-2xl font-bold text-white flex items-center gap-3">
                  {activeCollectionId
                    ? collections.find(c => c.id === activeCollectionId)?.name || 'Collection'
                    : t('library.title')}
                  {activeCollectionId && (
                    <span className="flex items-center gap-1 text-sm font-normal px-2 py-0.5 bg-zinc-800 rounded text-zinc-400">
                      <span>{collectionVideoIds.length}</span>
                      <VideoIcon size={14} className="text-indigo-400" />
                    </span>
                  )}
                </h1>
              </div>
              {!activeCollectionId && (
                <p className="text-zinc-400 text-sm">{t('library.subtitle')}</p>
              )}
            </div>

            <div className="flex flex-col md:flex-row gap-3 w-full md:w-auto">
               <button
                  onClick={loadLibraryData}
                  disabled={isLoadingLibrary}
                  className="p-2 bg-zinc-900 rounded-lg border border-zinc-800 text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
                  title="Refresh Data"
               >
                  <RefreshCw size={20} className={isLoadingLibrary ? "animate-spin" : ""} />
               </button>

               <SemanticSearchBar
                 onSearch={(results, query) => {
                   setSearchResults(results);
                   setSearchQueryText(query);
                   setIsSearchActive(true);
                 }}
                 onClear={() => {
                   setSearchResults([]);
                   setSearchQueryText('');
                   setIsSearchActive(false);
                 }}
                 placeholder={t('library.searchPlaceholder')}
                 className="flex-1 md:w-80"
                 library={library}
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

          {/* Library Content */}
          {(activeLibraryTab === 'my-library' || activeCollectionId) && (
          <div className="h-full relative flex-1 min-h-0">
            {isLoadingLibrary && library.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-96 text-zinc-500">
                <Loader2 className="w-8 h-8 animate-spin mb-4 text-indigo-500" />
                <p>Loading your collection...</p>
              </div>
            ) : (
              <>
                {libraryError && (
                  <div className="mb-4 mx-4 md:mx-0 p-3 bg-yellow-900/20 border border-yellow-900/50 rounded-lg flex items-center gap-3 text-sm text-yellow-200/80">
                    <CloudOff size={16} />
                    {libraryError}
                  </div>
                )}

                {libraryViewMode === 'grid' ? (
                  <div className="p-2 md:p-0 w-full">
                    {filteredLibrary.length > 0 ? (
                       <>
                         <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-3 w-full">
                            {filteredLibrary.map((item, idx) => (
                              <CompactMediaCard
                                key={`${item.platform_id}-${idx}`}
                                data={item}
                                onClick={() => setSelectedLibraryItem(item)}
                                isShared={sharedVideoIds.includes(item.platform_id)}
                              />
                            ))}
                         </div>
                         {!isSearchActive && (
                           <div ref={loadMoreRef} className="w-full py-8 flex justify-center">
                             {isLoadingMore ? (
                               <div className="flex items-center gap-2 text-zinc-500">
                                 <Loader2 className="w-5 h-5 animate-spin" />
                                 <span className="text-sm">Loading more...</span>
                               </div>
                             ) : hasMoreData ? (
                               <div className="text-zinc-600 text-sm">Scroll for more</div>
                             ) : library.length > 0 ? (
                               <div className="text-zinc-600 text-sm">All {library.length} items loaded</div>
                             ) : null}
                           </div>
                         )}
                       </>
                    ) : (
                      <div className="text-center py-20 bg-zinc-900/30 rounded-2xl border border-dashed border-zinc-800 text-zinc-500">
                        {isLoadingLibrary ? "Searching..." : "No items found matching your search."}
                      </div>
                    )}
                  </div>
                ) : libraryViewMode === 'list' ? (
                  <div className="p-4 md:p-0">
                    <LibraryTable
                      data={filteredLibrary}
                      onUpdate={handleUpdateLibraryItem}
                      onItemClick={(item) => setSelectedLibraryItem(item)}
                    />
                  </div>
                ) : (
                  <LibraryFeed data={filteredLibrary} />
                )}
              </>
            )}
          </div>
          )}

          {/* Mobile Library Search */}
          {!selectedLibraryItem && (activeLibraryTab === 'my-library' || activeCollectionId) && (
            <div className="md:hidden fixed top-0 left-0 right-0 z-30 p-4 flex justify-end items-start pointer-events-none bg-gradient-to-b from-black/60 to-transparent">
              <div className="pointer-events-auto flex items-center justify-end w-full max-w-[calc(100%-16px)]">
                {isMobileSearchOpen ? (
                  <div className="flex items-center bg-black/50 backdrop-blur-md rounded-full px-4 py-2.5 w-full animate-in slide-in-from-right-10 duration-200 border border-white/10 shadow-lg">
                    <Search size={16} className="text-zinc-300 mr-2 flex-shrink-0"/>
                    <input
                      autoFocus
                      className="bg-transparent border-none outline-none text-white text-sm w-full placeholder-zinc-400"
                      placeholder="Search collection..."
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                    />
                    <button
                      onClick={() => {
                        setIsMobileSearchOpen(false);
                        setSearchQuery('');
                      }}
                      className="ml-2 text-zinc-400 hover:text-white"
                    >
                      <X size={16} />
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setIsMobileSearchOpen(true)}
                    className="p-3 bg-black/20 backdrop-blur-md rounded-full text-white hover:bg-black/40 transition-colors shadow-lg border border-white/5"
                  >
                    <Search size={22} className="drop-shadow-md" />
                  </button>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
