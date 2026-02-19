import React, { useRef, useState, useCallback, useEffect, useMemo } from 'react';
import {
  RefreshCw,
  LayoutGrid,
  LayoutList,
  Smartphone,
  Loader2,
  Download,
  Trash2,
  X,
  Check,
  ChevronRight,
  ChevronLeft,
  ExternalLink,
  Clock,
  HardDrive,
  User,
  Heart,
  MessageCircle,
  Share2,
  Bookmark,
  Calendar,
  MonitorPlay,
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
import { getCoverUrl } from '../utils/awemeType';
import { useToast } from './Toast';
import { trashResourceByPlatformId } from '../services/resourceService';
import { getSupabaseClient } from '../supabaseClient';

export const RipVaultView: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { selectedTeamId } = useTeamContext();
  const { addToast } = useToast();

  // ─── Library data ──────────────────────────────────
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

  // ─── Resource ID mapping (for sprite scrub) ───────
  const [resourceIdMap, setResourceIdMap] = useState<Record<string, string>>({});

  useEffect(() => {
    if (library.length === 0) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;

    const mediaIds = library.map((item) => item.id).filter(Boolean);
    if (mediaIds.length === 0) return;

    supabase
      .from('resources')
      .select('id, media_id')
      .in('media_id', mediaIds)
      .then(({ data, error }) => {
        if (error || !data) return;
        const map: Record<string, string> = {};
        for (const row of data) {
          if (row.media_id) map[row.media_id] = String(row.id);
        }
        setResourceIdMap(map);
      });
  }, [library]);

  // ─── Selection state ───────────────────────────────
  const [selectedVideo, setSelectedVideo] = useState<Video | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [multiSelectMode, setMultiSelectMode] = useState(false);
  const [lastClickedId, setLastClickedId] = useState<string | null>(null);
  const [showInfoPanel, setShowInfoPanel] = useState(true);
  const [infoPanelWidth, setInfoPanelWidth] = useState(320);
  const resizeStartRef = useRef<{ x: number; width: number } | null>(null);

  // ─── Navigation ────────────────────────────────────
  const handleNavigateToDetail = useCallback((item: Video) => {
    if (!item.id) return;
    const teamPath = selectedTeamId ? `/t/${selectedTeamId}` : '';
    navigate(`${teamPath}/player/${item.id}?from=downloads`);
  }, [selectedTeamId, navigate]);

  // ─── Toggle select (multi-select) ─────────────────
  const handleToggleSelect = useCallback((platformId: string, e: React.MouseEvent) => {
    e.preventDefault();
    setMultiSelectMode(true);

    if (e.shiftKey && lastClickedId) {
      const allIds = filteredLibrary.map((v) => v.platform_id);
      const startIdx = allIds.indexOf(lastClickedId);
      const endIdx = allIds.indexOf(platformId);
      if (startIdx >= 0 && endIdx >= 0) {
        const [from, to] = startIdx < endIdx ? [startIdx, endIdx] : [endIdx, startIdx];
        const rangeIds = allIds.slice(from, to + 1);
        setSelectedIds((prev) => {
          const next = new Set(prev);
          rangeIds.forEach((id) => next.add(id));
          return next;
        });
      }
    } else {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        if (next.has(platformId)) next.delete(platformId);
        else next.add(platformId);
        return next;
      });
    }
    setLastClickedId(platformId);
  }, [lastClickedId, filteredLibrary]);

  // ─── Single click: select ─────────────────────────
  const handleVideoClick = useCallback((item: Video, e?: React.MouseEvent) => {
    if (e && (e.metaKey || e.ctrlKey || e.shiftKey)) {
      handleToggleSelect(item.platform_id, e);
      return;
    }
    if (selectedVideo?.platform_id === item.platform_id) {
      setSelectedVideo(null);
      setSelectedIds(new Set());
    } else {
      setSelectedVideo(item);
      setMultiSelectMode(false);
      setSelectedIds(new Set([item.platform_id]));
      setLastClickedId(item.platform_id);
    }
  }, [selectedVideo, handleToggleSelect]);

  // ─── Double click: navigate to detail ─────────────
  const handleVideoDoubleClick = useCallback((item: Video) => {
    handleNavigateToDetail(item);
  }, [handleNavigateToDetail]);

  // ─── Delete (soft-delete → move to recycle bin) ──
  const handleBatchDelete = useCallback(async () => {
    const platformIds = Array.from(selectedIds);
    let successCount = 0;
    for (const pid of platformIds) {
      try {
        await trashResourceByPlatformId(pid);
        successCount++;
      } catch (err) {
        console.error('Trash failed for', pid, err);
      }
    }
    if (selectedVideo && selectedIds.has(selectedVideo.platform_id)) {
      setSelectedVideo(null);
    }
    setSelectedIds(new Set());
    setMultiSelectMode(false);
    if (successCount > 0) {
      addToast(t('resources.movedToTrash'), 'success');
    }
    if (successCount < platformIds.length) {
      addToast(`Failed to remove ${platformIds.length - successCount} item(s)`, 'error');
    }
    loadLibraryData();
  }, [selectedIds, selectedVideo, addToast, loadLibraryData, t]);

  const handleDeleteSingle = useCallback(async (video: Video) => {
    try {
      await trashResourceByPlatformId(video.platform_id);
      if (selectedVideo?.platform_id === video.platform_id) {
        setSelectedVideo(null);
      }
      setSelectedIds((prev) => {
        const next = new Set(prev);
        next.delete(video.platform_id);
        return next;
      });
      addToast(t('resources.movedToTrash'), 'success');
      loadLibraryData();
    } catch (err) {
      console.error('Trash failed:', err);
      addToast('Failed to remove', 'error');
    }
  }, [selectedVideo, addToast, loadLibraryData, t]);

  // ─── Keyboard shortcuts ────────────────────────────
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (multiSelectMode) {
          setMultiSelectMode(false);
          setSelectedIds(new Set());
        }
        setSelectedVideo(null);
      }
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedIds.size > 0 && !e.metaKey) {
        e.preventDefault();
        handleBatchDelete();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [multiSelectMode, selectedIds, handleBatchDelete]);

  useEffect(() => {
    if (multiSelectMode && selectedIds.size === 0) {
      setMultiSelectMode(false);
    }
  }, [multiSelectMode, selectedIds.size]);

  // ─── Panel resize ─────────────────────────────────
  const handlePanelResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    resizeStartRef.current = { x: e.clientX, width: infoPanelWidth };
    const handleMove = (me: MouseEvent) => {
      if (!resizeStartRef.current) return;
      const delta = resizeStartRef.current.x - me.clientX;
      const newWidth = Math.max(280, Math.min(600, resizeStartRef.current.width + delta));
      setInfoPanelWidth(newWidth);
    };
    const handleUp = () => {
      resizeStartRef.current = null;
      document.removeEventListener('mousemove', handleMove);
      document.removeEventListener('mouseup', handleUp);
    };
    document.addEventListener('mousemove', handleMove);
    document.addEventListener('mouseup', handleUp);
  }, [infoPanelWidth]);

  // ─── Helpers ───────────────────────────────────────
  const formatNumber = (num?: number) => {
    if (!num) return '0';
    if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
    if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
    return num.toString();
  };

  const formatDate = (isoString?: string) => {
    if (!isoString) return '';
    try {
      return new Date(isoString).toLocaleDateString();
    } catch { return ''; }
  };

  // ─── Render ────────────────────────────────────────
  return (
    <div className="flex-1 min-w-0 flex flex-col h-full">
      {/* Header */}
      <header
        className="hidden md:flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6 px-6 pt-6"
        style={{ paddingRight: selectedVideo && showInfoPanel ? `${infoPanelWidth + 24}px` : undefined }}
      >
        <div>
          <h1 className="text-2xl font-bold text-white">{t('resources.downloads')}</h1>
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
      <div
        className="flex-1 overflow-y-auto px-6 pb-6"
        onClick={(e) => {
          // Click on empty area → deselect all (same as My Resources)
          const target = e.target as HTMLElement;
          if (!target.closest('[data-context-item]')) {
            if (selectedIds.size > 0 || multiSelectMode) {
              setSelectedIds(new Set());
              setMultiSelectMode(false);
            }
            setSelectedVideo(null);
          }
        }}
      >
        {libraryError && (
          <div className="mb-4 px-4 py-2 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
            {libraryError}
          </div>
        )}

        {isLoadingLibrary && library.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full min-h-[300px]">
            <Loader2 size={24} className="animate-spin text-zinc-500 mb-3" />
            <p className="text-zinc-500 text-sm">{t('common.loading', 'Loading...')}</p>
          </div>
        ) : filteredLibrary.length === 0 ? (
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
                    resourceId={resourceIdMap[item.id]}
                    onClick={(e) => handleVideoClick(item, e)}
                    onDoubleClick={() => handleVideoDoubleClick(item)}
                    isShared={sharedVideoIds.includes(item.platform_id)}
                    isSelected={selectedVideo?.platform_id === item.platform_id}
                    selectable
                    isChecked={selectedIds.has(item.platform_id)}
                    onToggleSelect={(e) => handleToggleSelect(item.platform_id, e)}
                    forceShowCheckbox={multiSelectMode}
                  />
                ))}
              </div>
            )}

            {/* List view */}
            {libraryViewMode === 'list' && (
              <LibraryTable
                data={filteredLibrary}
                onUpdate={handleUpdateLibraryItem}
                onItemClick={(item) => handleVideoClick(item)}
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

      {/* ── Right Info Panel: Video details ── */}
      {selectedVideo && (
        <div
          className={`fixed top-14 bottom-0 right-0 z-40 flex bg-zinc-900 border-l border-zinc-800 transition-transform duration-300 ease-in-out shadow-2xl ${
            showInfoPanel ? 'translate-x-0' : 'translate-x-full'
          }`}
          style={{ width: `${infoPanelWidth}px` }}
        >
          <button
            onClick={() => setShowInfoPanel(false)}
            className="absolute -left-10 bottom-8 w-10 h-12 bg-zinc-900 border-l border-y border-zinc-800 rounded-l-xl flex items-center justify-center text-zinc-400 hover:text-white cursor-pointer hover:bg-zinc-800 transition-colors z-10"
          >
            <ChevronRight size={20} />
          </button>

          <div
            onMouseDown={handlePanelResizeStart}
            className="w-1 h-full cursor-col-resize shrink-0 hover:bg-blue-500 active:bg-blue-500 transition-colors"
          />

          <div className="flex-1 overflow-y-auto">
            {/* Header */}
            <div className="sticky top-0 z-10 flex items-center justify-between px-4 py-3 bg-zinc-900 border-b border-zinc-800">
              <h3 className="text-sm font-medium text-white truncate">{t('resources.details', 'Details')}</h3>
              <button
                onClick={() => setSelectedVideo(null)}
                className="p-1 text-zinc-500 hover:text-white rounded transition-colors"
              >
                <X size={16} />
              </button>
            </div>

            {/* Cover */}
            <div className="relative w-full aspect-video bg-black">
              {getCoverUrl(selectedVideo) ? (
                <img
                  src={getCoverUrl(selectedVideo)!}
                  alt={selectedVideo.title || ''}
                  className="w-full h-full object-contain"
                  referrerPolicy="no-referrer"
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center">
                  <MonitorPlay size={32} className="text-zinc-700" />
                </div>
              )}
            </div>

            {/* Title & Description */}
            <div className="px-4 py-3 border-b border-zinc-800">
              <h4 className="text-sm font-medium text-white leading-snug">
                {selectedVideo.title || 'Untitled'}
              </h4>
              {selectedVideo.description && (
                <p className="mt-1.5 text-xs text-zinc-500 line-clamp-3">
                  {selectedVideo.description}
                </p>
              )}
            </div>

            {/* Metadata */}
            <div className="px-4 py-3 space-y-2.5 border-b border-zinc-800">
              {selectedVideo.author && (
                <div className="flex items-center gap-2 text-xs">
                  <User size={13} className="text-zinc-500 shrink-0" />
                  <span className="text-zinc-400">@{selectedVideo.author}</span>
                </div>
              )}
              {selectedVideo.duration && (
                <div className="flex items-center gap-2 text-xs">
                  <Clock size={13} className="text-zinc-500 shrink-0" />
                  <span className="text-zinc-400">{selectedVideo.duration}s</span>
                </div>
              )}
              {selectedVideo.resolution && (
                <div className="flex items-center gap-2 text-xs">
                  <MonitorPlay size={13} className="text-zinc-500 shrink-0" />
                  <span className="text-zinc-400">{selectedVideo.resolution}</span>
                </div>
              )}
              {selectedVideo.datasize && (
                <div className="flex items-center gap-2 text-xs">
                  <HardDrive size={13} className="text-zinc-500 shrink-0" />
                  <span className="text-zinc-400">{selectedVideo.datasize}</span>
                </div>
              )}
              {selectedVideo.published_at && (
                <div className="flex items-center gap-2 text-xs">
                  <Calendar size={13} className="text-zinc-500 shrink-0" />
                  <span className="text-zinc-400">{formatDate(selectedVideo.published_at)}</span>
                </div>
              )}
              {selectedVideo.source_platform && (
                <div className="flex items-center gap-2 text-xs">
                  <ExternalLink size={13} className="text-zinc-500 shrink-0" />
                  <span className="text-zinc-400 capitalize">{selectedVideo.source_platform}</span>
                </div>
              )}
            </div>

            {/* Engagement */}
            <div className="px-4 py-3 border-b border-zinc-800">
              <h5 className="text-xs font-medium text-zinc-500 mb-2 uppercase tracking-wider">Engagement</h5>
              <div className="grid grid-cols-2 gap-2">
                <div className="flex items-center gap-2 text-xs text-zinc-400">
                  <Heart size={13} className="text-rose-500" />
                  <span>{formatNumber(selectedVideo.like_count)}</span>
                </div>
                <div className="flex items-center gap-2 text-xs text-zinc-400">
                  <MessageCircle size={13} className="text-sky-500" />
                  <span>{formatNumber(selectedVideo.comment_count)}</span>
                </div>
                <div className="flex items-center gap-2 text-xs text-zinc-400">
                  <Share2 size={13} className="text-emerald-500" />
                  <span>{formatNumber(selectedVideo.share_count)}</span>
                </div>
                <div className="flex items-center gap-2 text-xs text-zinc-400">
                  <Bookmark size={13} className="text-amber-500" />
                  <span>{formatNumber(selectedVideo.favorite_count)}</span>
                </div>
              </div>
            </div>

            {/* Tags */}
            {selectedVideo.tags && selectedVideo.tags.length > 0 && (
              <div className="px-4 py-3 border-b border-zinc-800">
                <h5 className="text-xs font-medium text-zinc-500 mb-2 uppercase tracking-wider">Tags</h5>
                <div className="flex flex-wrap gap-1.5">
                  {selectedVideo.tags.map((tag, i) => (
                    <span key={i} className="text-[10px] px-2 py-0.5 rounded bg-zinc-800 text-zinc-400 border border-zinc-700">
                      #{tag}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Actions */}
            <div className="px-4 py-3 space-y-2">
              <button
                onClick={() => handleNavigateToDetail(selectedVideo)}
                className="w-full flex items-center justify-center gap-2 px-3 py-2 text-xs font-medium bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg transition-colors"
              >
                <ExternalLink size={14} />
                {t('resources.openDetail', 'Open Detail')}
              </button>
              <button
                onClick={() => handleDeleteSingle(selectedVideo)}
                className="w-full flex items-center justify-center gap-2 px-3 py-2 text-xs font-medium text-red-400 hover:text-red-300 hover:bg-red-900/20 border border-zinc-800 rounded-lg transition-colors"
              >
                <Trash2 size={14} />
                {t('resources.delete', 'Delete')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Expand tab — visible when panel is closed */}
      {selectedVideo && !showInfoPanel && (
        <button
          onClick={() => setShowInfoPanel(true)}
          className="fixed bottom-8 right-0 w-10 h-12 bg-zinc-900 border-l border-y border-zinc-800 rounded-l-xl flex items-center justify-center text-zinc-400 hover:text-white cursor-pointer hover:bg-zinc-800 transition-all z-50"
        >
          <ChevronLeft size={20} />
        </button>
      )}

      {/* ── Batch Selection Toolbar ── */}
      {selectedIds.size > 1 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 bg-zinc-900 border border-zinc-700 rounded-xl px-5 py-3 shadow-2xl">
          <span className="text-sm text-zinc-300 font-medium">
            {t('resources.selected', { count: selectedIds.size })}
          </span>
          <div className="w-px h-5 bg-zinc-700" />
          <button
            onClick={handleBatchDelete}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-red-400 hover:text-red-300 hover:bg-red-900/30 rounded-lg transition-colors"
          >
            <Trash2 size={14} />
            {t('common.delete', 'Delete')}
          </button>
          <button
            onClick={() => { setSelectedIds(new Set()); setMultiSelectMode(false); }}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 rounded-lg transition-colors"
          >
            <X size={14} />
            {t('common.cancel', 'Cancel')}
          </button>
        </div>
      )}
    </div>
  );
};
