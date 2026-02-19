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
  Brain,
  Sparkles,
  Eye,
  Star,
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

// ─── AI Status Badge ──────────────────────────────────
const AIStatusBadge: React.FC<{ status?: string }> = ({ status }) => {
  switch (status) {
    case 'processing':
      return (
        <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-400">
          <Loader2 size={9} className="animate-spin" /> Processing
        </span>
      );
    case 'completed':
      return (
        <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400">
          <Check size={9} /> Done
        </span>
      );
    case 'failed':
      return (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/10 text-red-400">
          Failed
        </span>
      );
    case 'pending':
      return (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-500/10 text-zinc-500">
          Pending
        </span>
      );
    default:
      return null;
  }
};

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
  const [panelNotes, setPanelNotes] = useState('');
  const [panelRating, setPanelRating] = useState(0);
  const [panelHoverRating, setPanelHoverRating] = useState(0);
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

  // ─── Panel Rating & Notes ─────────────────────────
  useEffect(() => {
    setPanelNotes(selectedVideo?.notes || '');
    setPanelRating(selectedVideo?.rating || 0);
  }, [selectedVideo?.platform_id]);

  const handlePanelRating = (star: number) => {
    if (!selectedVideo) return;
    const newRating = star === panelRating ? 0 : star;
    setPanelRating(newRating);
    handleUpdateLibraryItem(selectedVideo.platform_id, { rating: newRating });
  };

  const handlePanelNotesBlur = () => {
    if (!selectedVideo) return;
    if (panelNotes !== (selectedVideo.notes || '')) {
      handleUpdateLibraryItem(selectedVideo.platform_id, { notes: panelNotes });
    }
  };

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
      {/* Toolbar — matches ResourcesView style */}
      <div
        className="px-6 py-3 border-b border-zinc-800/80"
        style={{ paddingRight: selectedVideo && showInfoPanel ? `${infoPanelWidth + 24}px` : undefined }}
      >
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <span className="text-sm text-zinc-200 font-medium truncate">{t('resources.downloads')}</span>
            {!isLoadingLibrary && (
              <span className="text-[11px] text-zinc-600 shrink-0 tabular-nums">
                {filteredLibrary.length} {filteredLibrary.length === 1 ? 'item' : 'items'}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {/* Refresh */}
            <button
              onClick={loadLibraryData}
              disabled={isLoadingLibrary}
              className="p-1.5 rounded-lg text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800/80 transition-colors"
              title="Refresh Data"
            >
              <RefreshCw size={14} className={isLoadingLibrary ? 'animate-spin' : ''} />
            </button>

            {/* Search */}
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
              className="w-52"
              library={library as any}
            />

            {/* View toggle */}
            <div className="hidden md:flex items-center">
              <button
                onClick={() => setLibraryViewMode('list')}
                className={`p-1.5 rounded-lg transition-colors ${libraryViewMode === 'list' ? 'bg-zinc-800/60 text-zinc-200' : 'text-zinc-500 hover:text-zinc-300'}`}
                title="List View"
              >
                <LayoutList size={14} />
              </button>
              <button
                onClick={() => setLibraryViewMode('grid')}
                className={`p-1.5 rounded-lg transition-colors ${libraryViewMode === 'grid' ? 'bg-zinc-800/60 text-zinc-200' : 'text-zinc-500 hover:text-zinc-300'}`}
                title="Grid View"
              >
                <LayoutGrid size={14} />
              </button>
              <button
                onClick={() => setLibraryViewMode('feed')}
                className={`p-1.5 rounded-lg transition-colors ${libraryViewMode === 'feed' ? 'bg-zinc-800/60 text-zinc-200' : 'text-zinc-500 hover:text-zinc-300'}`}
                title="Feed View"
              >
                <Smartphone size={14} />
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Content */}
      <div
        className="flex-1 overflow-y-auto px-5 pb-5"
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
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7 gap-2">
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

      {/* ── Right Info Panel: Video details (Eagle style) ── */}
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
              <h3 className="text-sm font-semibold text-white truncate">{t('resources.details', 'Details')}</h3>
              <button
                onClick={() => setSelectedVideo(null)}
                className="p-1.5 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
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

            {/* Title */}
            <div className="px-4 mt-4">
              <h4 className="text-sm font-medium text-white break-words leading-snug">
                {selectedVideo.title || 'Untitled'}
              </h4>
              {selectedVideo.description && (
                <p className="mt-1.5 text-xs text-zinc-500 line-clamp-3">
                  {selectedVideo.description}
                </p>
              )}
            </div>

            {/* Engagement */}
            <div className="px-4 mt-4">
              <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
                Engagement
              </h4>
              <div className="grid grid-cols-2 gap-1.5">
                <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-rose-500/5 border border-rose-500/10">
                  <Heart size={12} className="text-rose-400 shrink-0" />
                  <span className="text-xs text-zinc-300">{formatNumber(selectedVideo.like_count)}</span>
                </div>
                <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-sky-500/5 border border-sky-500/10">
                  <MessageCircle size={12} className="text-sky-400 shrink-0" />
                  <span className="text-xs text-zinc-300">{formatNumber(selectedVideo.comment_count)}</span>
                </div>
                <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-emerald-500/5 border border-emerald-500/10">
                  <Share2 size={12} className="text-emerald-400 shrink-0" />
                  <span className="text-xs text-zinc-300">{formatNumber(selectedVideo.share_count)}</span>
                </div>
                <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-amber-500/5 border border-amber-500/10">
                  <Bookmark size={12} className="text-amber-400 shrink-0" />
                  <span className="text-xs text-zinc-300">{formatNumber(selectedVideo.favorite_count)}</span>
                </div>
              </div>
            </div>

            {/* Tags */}
            {selectedVideo.tags && selectedVideo.tags.length > 0 && (
              <div className="px-4 mt-4">
                <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
                  Tags
                </h4>
                <div className="flex flex-wrap gap-1.5">
                  {selectedVideo.tags.map((tag, i) => (
                    <span key={i} className="text-[10px] px-2 py-0.5 rounded bg-zinc-800 text-zinc-400 border border-zinc-700">
                      #{tag}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Rating */}
            <div className="px-4 mt-4">
              <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
                Rating
              </h4>
              <div className="flex items-center gap-0.5">
                {[1, 2, 3, 4, 5].map(star => (
                  <button
                    key={star}
                    onClick={() => handlePanelRating(star)}
                    onMouseEnter={() => setPanelHoverRating(star)}
                    onMouseLeave={() => setPanelHoverRating(0)}
                    className="p-0.5 transition-colors"
                  >
                    <Star
                      size={16}
                      className={(panelHoverRating || panelRating) >= star
                        ? 'text-amber-400 fill-amber-400'
                        : 'text-zinc-600'}
                    />
                  </button>
                ))}
              </div>
            </div>

            {/* Notes */}
            <div className="px-4 mt-4">
              <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-1.5">
                Notes
              </h4>
              <textarea
                value={panelNotes}
                onChange={e => setPanelNotes(e.target.value)}
                onBlur={handlePanelNotesBlur}
                placeholder="Add notes..."
                className="w-full bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 text-xs text-zinc-300 placeholder-zinc-600 resize-none min-h-[60px] focus:outline-none focus:border-zinc-600 transition-colors"
                rows={3}
              />
            </div>

            {/* AI Status */}
            {(selectedVideo.transcript_status || selectedVideo.summary_status || selectedVideo.visual_analysis_status) && (
              <div className="px-4 mt-4 border-t border-zinc-800/60 pt-3">
                <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
                  AI
                </h4>
                <div className="space-y-1.5">
                  {selectedVideo.transcript_status && selectedVideo.transcript_status !== 'none' && (
                    <div className="flex items-center justify-between py-1">
                      <div className="flex items-center gap-2">
                        <Brain size={12} className="text-indigo-400" />
                        <span className="text-xs text-zinc-400">Transcript</span>
                      </div>
                      <AIStatusBadge status={selectedVideo.transcript_status} />
                    </div>
                  )}
                  {selectedVideo.summary_status && selectedVideo.summary_status !== 'none' && (
                    <div className="flex items-center justify-between py-1">
                      <div className="flex items-center gap-2">
                        <Sparkles size={12} className="text-indigo-400" />
                        <span className="text-xs text-zinc-400">Summary</span>
                      </div>
                      <AIStatusBadge status={selectedVideo.summary_status} />
                    </div>
                  )}
                  {selectedVideo.visual_analysis_status && selectedVideo.visual_analysis_status !== 'none' && (
                    <div className="flex items-center justify-between py-1">
                      <div className="flex items-center gap-2">
                        <Eye size={12} className="text-purple-400" />
                        <span className="text-xs text-zinc-400">Visual Analysis</span>
                      </div>
                      <AIStatusBadge status={selectedVideo.visual_analysis_status} />
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Properties */}
            <div className="px-4 mt-4 border-t border-zinc-800/60 pt-3">
              <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
                Properties
              </h4>
              <div className="space-y-0">
                {selectedVideo.author && (
                  <div className="flex justify-between items-center py-1.5">
                    <span className="text-xs text-zinc-500">Author</span>
                    <span className="text-xs text-zinc-300">@{selectedVideo.author}</span>
                  </div>
                )}
                {selectedVideo.duration && (
                  <div className="flex justify-between items-center py-1.5">
                    <span className="text-xs text-zinc-500">Duration</span>
                    <span className="text-xs text-zinc-300">{selectedVideo.duration}s</span>
                  </div>
                )}
                {selectedVideo.resolution && (
                  <div className="flex justify-between items-center py-1.5">
                    <span className="text-xs text-zinc-500">Resolution</span>
                    <span className="text-xs text-zinc-300">{selectedVideo.resolution}</span>
                  </div>
                )}
                {selectedVideo.datasize && (
                  <div className="flex justify-between items-center py-1.5">
                    <span className="text-xs text-zinc-500">Size</span>
                    <span className="text-xs text-zinc-300">{selectedVideo.datasize}</span>
                  </div>
                )}
                {selectedVideo.source_platform && (
                  <div className="flex justify-between items-center py-1.5">
                    <span className="text-xs text-zinc-500">Platform</span>
                    <span className="text-xs text-zinc-300 capitalize">{selectedVideo.source_platform}</span>
                  </div>
                )}
                {selectedVideo.published_at && (
                  <div className="flex justify-between items-center py-1.5">
                    <span className="text-xs text-zinc-500">Published</span>
                    <span className="text-xs text-zinc-300">{formatDate(selectedVideo.published_at)}</span>
                  </div>
                )}
              </div>
            </div>

            {/* Actions */}
            <div className="px-4 mt-4 border-t border-zinc-800/60 pt-3 pb-6 space-y-2">
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
