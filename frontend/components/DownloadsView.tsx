import React, { useRef, useState, useCallback, useEffect, useMemo } from 'react';
import { createPortal } from 'react-dom';
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
  Music,
  Pencil,
  Link,
  Search,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { useLibraryContext } from '../contexts/LibraryContext';
import { useTeamContext } from '../contexts/TeamContext';
import { Video } from '../types';
import { CompactMediaCard } from './CompactMediaCard';
import { LibraryTable } from './LibraryTable';
import { LibraryFeed } from './LibraryFeed';
import { ToolbarSearch } from './ToolbarSearch';
import { ShareModal } from './ShareModal';
import { getCoverUrl, getVideoUrl, formatResolution } from '../utils/awemeType';
import { semanticSearch, hybridSearch, localSearch } from '../services/searchService';
import { useToast } from './Toast';
import { trashResourceByPlatformId, updateResource, fetchResourceTags, addResourceTag, removeResourceTag } from '../services/resourceService';
import { fetchAllTags, createTag } from '../services/unifiedTagService';
import { getDownloadUrl, getMusicDownloadUrl } from '../services/dataService';
import { getSupabaseClient } from '../supabaseClient';
import { downloadFile, downloadWithAuth } from '../utils/download';
import { EagleTagPicker } from './EagleTagPicker';
import { useAuth } from '../contexts/AuthContext';

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

export const DownloadsView: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { selectedTeamId } = useTeamContext();
  const { addToast } = useToast();
  const { mediaToken } = useAuth();

  // ─── Tag search map (media_id → space-joined tag names) ───
  const [tagSearchMap, setTagSearchMap] = useState<Record<string, string>>({});

  // ─── Library data (shared via context — avoids duplicate Supabase fetch) ───
  const {
    library,
    isLoadingLibrary,
    libraryError,
    totalCount,
    hasMoreData,
    isLoadingMore,
    loadMoreRef,
    loadMoreLibrary,
    libraryViewMode,
    setLibraryViewMode,
    sharedVideoIds,
    setLibrary,
    loadLibraryData,
    handleUpdateLibraryItem,
  } = useLibraryContext();

  // ─── Local search state (independent from Library page's search) ───
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<import('../services/searchService').SearchResult[]>([]);
  const [isSearchActive, setIsSearchActive] = useState(false);
  const [searchQueryText, setSearchQueryText] = useState('');

  // ─── Local filtered library (supports tag search via extraSearchMap) ───
  const filteredLibrary = useMemo(() => {
    if (isSearchActive) {
      if (searchResults.length === 0) return [];
      const searchIds = new Set(searchResults.map(r => r.platform_id));
      return library
        .filter(item => searchIds.has(item.platform_id))
        .sort((a, b) => {
          const aScore = searchResults.find(r => r.platform_id === a.platform_id)?.similarity_score || 0;
          const bScore = searchResults.find(r => r.platform_id === b.platform_id)?.similarity_score || 0;
          return bScore - aScore;
        });
    }
    return library
      .filter(item => {
        if (searchQuery.trim()) {
          const q = searchQuery.toLowerCase().trim();
          const title = (item.title || '').toLowerCase();
          const author = ((item as any).author_nickname || '').toLowerCase();
          const desc = (item.description || '').toLowerCase();
          const extra = (tagSearchMap?.[item.id] || '').toLowerCase();
          return title.includes(q) || author.includes(q) || desc.includes(q) || extra.includes(q);
        }
        return true;
      })
      .sort((a, b) => {
        const aTime = a.created_at ? new Date(a.created_at).getTime() : 0;
        const bTime = b.created_at ? new Date(b.created_at).getTime() : 0;
        return bTime - aTime;
      });
  }, [library, isSearchActive, searchResults, searchQuery, tagSearchMap]);

  // ─── Resource data mapping (notes/rating/id from resources table) ───
  const [resourceDataMap, setResourceDataMap] = useState<Record<string, { id: string; notes: string | null; rating: number }>>({});

  useEffect(() => {
    if (library.length === 0) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;

    const mediaIds = library.map((item) => item.id).filter(Boolean);
    if (mediaIds.length === 0) return;

    supabase
      .from('resources')
      .select('id, media_id, notes, rating')
      .in('media_id', mediaIds)
      .then(({ data, error }) => {
        if (error || !data) return;
        const map: Record<string, { id: string; notes: string | null; rating: number }> = {};
        for (const row of data) {
          if (row.media_id) map[row.media_id] = { id: String(row.id), notes: row.notes, rating: row.rating || 0 };
        }
        setResourceDataMap(map);
      });
  }, [library]);

  // Convenience accessor: get resource ID by parsed_media ID
  const resourceIdMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const [mediaId, rd] of Object.entries(resourceDataMap)) {
      map[mediaId] = rd.id;
    }
    return map;
  }, [resourceDataMap]);

  // ─── All available tags (for tag picker) ────────────
  const [allTags, setAllTags] = useState<import('../types').Tag[]>([]);
  useEffect(() => {
    fetchAllTags().then(setAllTags).catch(() => {});
  }, []);

  // ─── Bulk load tag names for search ────────────────
  useEffect(() => {
    const resourceIds = Object.values(resourceDataMap).map(r => r.id);
    if (resourceIds.length === 0) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;

    supabase
      .from('resource_tags')
      .select('resource_id, tag:tags(name)')
      .in('resource_id', resourceIds)
      .then(({ data, error }) => {
        if (error || !data) return;
        const resourceToMediaId: Record<string, string> = {};
        for (const [mediaId, rd] of Object.entries(resourceDataMap)) {
          resourceToMediaId[rd.id] = mediaId;
        }
        const map: Record<string, string> = {};
        for (const row of data) {
          const mediaId = resourceToMediaId[String(row.resource_id)];
          if (!mediaId) continue;
          const tag = row.tag as { name: string } | { name: string }[] | null;
          const tagName = Array.isArray(tag) ? tag[0]?.name : tag?.name;
          if (tagName) {
            map[mediaId] = map[mediaId] ? `${map[mediaId]} ${tagName}` : tagName;
          }
        }
        setTagSearchMap(map);
      });
  }, [resourceDataMap]);

  // ─── Search handlers ──────────────────────────────
  const [isAISearching, setIsAISearching] = useState(false);

  const handleSearchQueryChange = useCallback((query: string) => {
    setIsSearchActive(false);
    setSearchResults([]);
    setSearchQuery(query);
  }, []);

  const handleAISearch = useCallback(async (query: string, mode: 'hybrid' | 'semantic') => {
    setIsAISearching(true);
    try {
      const response = mode === 'semantic'
        ? await semanticSearch(query, 20)
        : await hybridSearch(query, {}, 20, 0.5);
      setSearchResults(response.results as any);
      setIsSearchActive(true);
      setSearchQueryText(query);
    } catch (error) {
      console.error('AI search failed:', error);
      // Fallback to local search
      const fallback = localSearch(query, library as any, 20);
      setSearchResults(fallback.results as any);
      setIsSearchActive(true);
      setSearchQueryText(query);
    } finally {
      setIsAISearching(false);
    }
  }, [library]);

  const handleSearchClear = useCallback(() => {
    setSearchResults([]);
    setIsSearchActive(false);
    setSearchQueryText('');
    setSearchQuery('');
  }, []);

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
  const [renameTarget, setRenameTarget] = useState<Video | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [selectedVideoTags, setSelectedVideoTags] = useState<Array<{ tag: { id: string; name: string; color?: string } }>>([]);
  const [shareTargetResourceId, setShareTargetResourceId] = useState<string | null>(null);
  const [shareTargetName, setShareTargetName] = useState<string>('');
  const [isMobileSearchOpen, setIsMobileSearchOpen] = useState(false);
  const [mobileSearchQuery, setMobileSearchQuery] = useState('');

  // ─── Navigation ────────────────────────────────────
  const handleNavigateToDetail = useCallback((item: Video) => {
    const rid = (item as any).resource_id || item.id;
    if (!rid) return;
    const teamPath = selectedTeamId ? `/team/${selectedTeamId}` : '';
    navigate(`${teamPath}/resources/file/${rid}`);
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

  // ─── Single click: select (delayed to avoid conflict with double-click) ───
  const isMobileDevice = typeof window !== 'undefined' && window.innerWidth < 768;
  const clickTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleVideoClick = useCallback((item: Video, e?: React.MouseEvent) => {
    if (e && (e.metaKey || e.ctrlKey || e.shiftKey)) {
      handleToggleSelect(item.platform_id, e);
      return;
    }
    // Mobile: single tap navigates to detail (no double-click on touch)
    if (isMobileDevice) {
      handleNavigateToDetail(item);
      return;
    }
    // Delay single-click selection so double-click can cancel it,
    // preventing sidebar open → grid reflow → wrong card on second click
    if (clickTimerRef.current) clearTimeout(clickTimerRef.current);
    clickTimerRef.current = setTimeout(() => {
      clickTimerRef.current = null;
      if (selectedVideo?.platform_id === item.platform_id) {
        setSelectedVideo(null);
        setSelectedIds(new Set());
      } else {
        setSelectedVideo(item);
        setMultiSelectMode(false);
        setSelectedIds(new Set([item.platform_id]));
        setLastClickedId(item.platform_id);
      }
    }, 250);
  }, [selectedVideo, handleToggleSelect, isMobileDevice, handleNavigateToDetail]);

  // ─── Double click: navigate to detail ─────────────
  const handleVideoDoubleClick = useCallback((item: Video) => {
    // Cancel pending single-click selection to prevent grid reflow
    if (clickTimerRef.current) {
      clearTimeout(clickTimerRef.current);
      clickTimerRef.current = null;
    }
    handleNavigateToDetail(item);
  }, [handleNavigateToDetail]);

  // ─── Delete (soft-delete → move to recycle bin) ──
  const handleBatchDelete = useCallback(async () => {
    const platformIds = Array.from(selectedIds);
    const trashedIds = new Set<string>();
    for (const pid of platformIds) {
      try {
        await trashResourceByPlatformId(pid);
        trashedIds.add(pid);
      } catch (err) {
        console.error('Trash failed for', pid, err);
      }
    }
    if (selectedVideo && trashedIds.has(selectedVideo.platform_id)) {
      setSelectedVideo(null);
    }
    setSelectedIds(new Set());
    setMultiSelectMode(false);
    if (trashedIds.size > 0) {
      setLibrary(prev => prev.filter(item => !trashedIds.has(item.platform_id)));
      addToast(t('resources.movedToTrash'), 'success');
    }
    if (trashedIds.size < platformIds.length) {
      addToast(`Failed to remove ${platformIds.length - trashedIds.size} item(s)`, 'error');
    }
  }, [selectedIds, selectedVideo, addToast, setLibrary, t]);

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
      setLibrary(prev => prev.filter(item => item.platform_id !== video.platform_id));
      addToast(t('resources.movedToTrash'), 'success');
    } catch (err) {
      console.error('Trash failed:', err);
      addToast('Failed to remove', 'error');
    }
  }, [selectedVideo, addToast, setLibrary, t]);

  // ─── Keyboard shortcuts ────────────────────────────
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Skip shortcuts when user is typing in an input/textarea
      const tag = (e.target as HTMLElement)?.tagName;
      const isEditing = tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement)?.isContentEditable;

      if (e.key === 'Escape') {
        if (renameTarget) { setRenameTarget(null); return; }
        if (multiSelectMode) {
          setMultiSelectMode(false);
          setSelectedIds(new Set());
        }
        setSelectedVideo(null);
      }
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedIds.size > 0 && !e.metaKey && !isEditing) {
        e.preventDefault();
        handleBatchDelete();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [multiSelectMode, selectedIds, handleBatchDelete, renameTarget]);

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

  // ─── Panel Rating & Notes (from resources table) ──
  const selectedResourceData = selectedVideo?.id ? resourceDataMap[selectedVideo.id] : undefined;

  useEffect(() => {
    setPanelNotes(selectedResourceData?.notes || '');
    setPanelRating(selectedResourceData?.rating || 0);
  }, [selectedVideo?.platform_id, selectedResourceData]);

  // ─── Fetch tags for selected video ──────────────────
  useEffect(() => {
    if (!selectedResourceData?.id) {
      setSelectedVideoTags([]);
      return;
    }
    setSelectedVideoTags([]);
    fetchResourceTags(selectedResourceData.id)
      .then(setSelectedVideoTags)
      .catch(() => setSelectedVideoTags([]));
  }, [selectedResourceData?.id]);

  const handleAddTag = useCallback(async (tagId: string) => {
    if (!selectedResourceData?.id) return;
    try {
      await addResourceTag(selectedResourceData.id, tagId);
      const updated = await fetchResourceTags(selectedResourceData.id);
      setSelectedVideoTags(updated);
    } catch (err) {
      console.error('Failed to add tag:', err);
    }
  }, [selectedResourceData]);

  const handleRemoveTag = useCallback(async (tagId: string) => {
    if (!selectedResourceData?.id) return;
    try {
      await removeResourceTag(selectedResourceData.id, tagId);
      setSelectedVideoTags(prev => prev.filter(t => String(t.tag?.id) !== tagId));
    } catch (err) {
      console.error('Failed to remove tag:', err);
    }
  }, [selectedResourceData]);

  const handleCreateTag = useCallback(async (name: string, color: string) => {
    try {
      const tag = await createTag({ name, color, type: 'user' });
      setAllTags(prev => [...prev, tag]);
      return tag;
    } catch {
      return null;
    }
  }, []);

  const handlePanelRating = async (star: number) => {
    if (!selectedVideo || !selectedResourceData) return;
    const newRating = star === panelRating ? 0 : star;
    setPanelRating(newRating);
    try {
      await updateResource(selectedResourceData.id, { rating: newRating });
      // Update local cache
      setResourceDataMap(prev => ({
        ...prev,
        [selectedVideo.id!]: { ...prev[selectedVideo.id!], rating: newRating },
      }));
    } catch (err) {
      console.error('Failed to update rating:', err);
    }
  };

  const handlePanelNotesBlur = async () => {
    if (!selectedVideo || !selectedResourceData) return;
    if (panelNotes !== (selectedResourceData.notes || '')) {
      try {
        await updateResource(selectedResourceData.id, { notes: panelNotes });
        // Update local cache
        setResourceDataMap(prev => ({
          ...prev,
          [selectedVideo.id!]: { ...prev[selectedVideo.id!], notes: panelNotes },
        }));
      } catch (err) {
        console.error('Failed to update notes:', err);
      }
    }
  };

  // ─── Context menu ────────────────────────────────────
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; video: Video } | null>(null);

  const handleContextMenu = useCallback((e: React.MouseEvent, video: Video) => {
    setContextMenu({ x: e.clientX, y: e.clientY, video });
  }, []);

  // Close context menu on click outside or Escape
  useEffect(() => {
    if (!contextMenu) return;
    const close = () => setContextMenu(null);
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    document.addEventListener('click', close);
    document.addEventListener('keydown', handleKey);
    return () => { document.removeEventListener('click', close); document.removeEventListener('keydown', handleKey); };
  }, [contextMenu]);

  const handleCtxOpenNewTab = useCallback(() => {
    if (!contextMenu) return;
    const v = contextMenu.video;
    const teamPath = selectedTeamId ? `/team/${selectedTeamId}` : '';
    const rid = (v as any).resource_id || v.id;
    window.open(`${teamPath}/resources/file/${rid}`, '_blank');
    setContextMenu(null);
  }, [contextMenu, selectedTeamId]);

  const handleCtxDownloadVideo = useCallback(async () => {
    if (!contextMenu) return;
    const v = contextMenu.video;
    setContextMenu(null);
    const callbacks = {
      onSuccess: (f: string) => addToast(`Downloaded: ${f}`, 'success'),
      onError: (msg: string) => addToast(`Download failed (${msg})`, 'error'),
    };
    // Try backend API first, fallback to remote URL
    const baseName = (v.title || v.platform_id || 'media')
      .replace(/[\\/:*?"<>|]/g, '_')
      .trim()
      .slice(0, 100);
    if (v.download_path && v.video_download_status?.toLowerCase() === 'completed' && v.platform_id) {
      const ok = await downloadWithAuth(getDownloadUrl(v.platform_id), `${baseName}.mp4`, {
        onSuccess: callbacks.onSuccess,
      });
      if (ok) return;
    }
    const videoUrl = getVideoUrl(v, mediaToken ?? undefined);
    if (videoUrl) {
      await downloadFile(videoUrl, `${baseName}.mp4`, callbacks);
    } else {
      addToast('No video file available', 'error');
    }
  }, [contextMenu, addToast]);

  const handleCtxDownloadAudio = useCallback(async () => {
    if (!contextMenu) return;
    const v = contextMenu.video;
    setContextMenu(null);
    if (!v.music_download_path || !v.platform_id) {
      addToast('No audio file available. Use player page to extract audio.', 'info');
      return;
    }
    const url = getMusicDownloadUrl(v.platform_id);
    const audioBaseName = (v.title || v.platform_id || 'media')
      .replace(/[\\/:*?"<>|]/g, '_')
      .trim()
      .slice(0, 100);
    await downloadFile(url, `${audioBaseName}_audio.m4a`, {
      onSuccess: (f) => addToast(`Downloaded: ${f}`, 'success'),
      onError: (msg) => addToast(`Download failed (${msg})`, 'error'),
    });
  }, [contextMenu, addToast]);

  const handleCtxShare = useCallback(() => {
    if (!contextMenu) return;
    const video = contextMenu.video;
    const rid = resourceIdMap[video.id];
    setContextMenu(null);
    if (rid) {
      setShareTargetResourceId(rid);
      setShareTargetName(video.title || video.description || 'Shared Media');
    } else {
      addToast('Cannot share: no resource linked', 'error');
    }
  }, [contextMenu, addToast, resourceIdMap]);

  const handleCtxRename = useCallback(() => {
    if (!contextMenu) return;
    setRenameTarget(contextMenu.video);
    setRenameValue(contextMenu.video.title || '');
    setContextMenu(null);
  }, [contextMenu]);

  const handleRenameSubmit = useCallback(() => {
    if (!renameTarget || !renameValue.trim()) { setRenameTarget(null); return; }
    handleUpdateLibraryItem(renameTarget.platform_id, { title: renameValue.trim() });
    setRenameTarget(null);
    addToast('Renamed', 'success');
  }, [renameTarget, renameValue, handleUpdateLibraryItem, addToast]);

  const handleCtxDelete = useCallback(async () => {
    if (!contextMenu) return;
    const v = contextMenu.video;
    setContextMenu(null);
    try {
      await trashResourceByPlatformId(v.platform_id);
      if (selectedVideo?.platform_id === v.platform_id) setSelectedVideo(null);
      setSelectedIds(prev => { const next = new Set(prev); next.delete(v.platform_id); return next; });
      setLibrary(prev => prev.filter(item => item.platform_id !== v.platform_id));
      addToast(t('resources.movedToTrash'), 'success');
    } catch (err) {
      console.error('Trash failed:', err);
      addToast('Failed to remove', 'error');
    }
  }, [contextMenu, selectedVideo, addToast, setLibrary, t]);

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
    <div className={`flex-1 min-w-0 flex flex-col ${libraryViewMode === 'feed' ? '' : 'md:h-full'}`}>
      {/* Toolbar — matches ResourcesView style (hidden on mobile, search via overlay) */}
      <div
        className="hidden md:block px-6 py-2 border-b border-zinc-800/80"
        style={{ paddingRight: selectedVideo && showInfoPanel ? `${infoPanelWidth + 24}px` : undefined }}
      >
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <span className="text-sm text-zinc-200 font-medium truncate">{t('resources.downloads')}</span>
            {!isLoadingLibrary && (
              <span className="text-[11px] text-zinc-600 shrink-0 tabular-nums">
                {totalCount >= 0 ? totalCount : filteredLibrary.length} {(totalCount >= 0 ? totalCount : filteredLibrary.length) === 1 ? 'item' : 'items'}
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
            <ToolbarSearch
              onQueryChange={handleSearchQueryChange}
              onAISearch={handleAISearch}
              onClear={handleSearchClear}
              isSearching={isAISearching}
              placeholder={t('library.searchPlaceholder', 'Search title, tags, notes...')}
              className="w-52"
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
        className={`flex-1 md:min-h-0 md:overflow-y-auto md:px-5 md:pt-4 ${libraryViewMode === 'feed' ? 'px-0 pt-0 pb-0 h-full min-h-0' : 'px-3 pt-3 pb-5'}`}
        style={{ paddingRight: selectedVideo && showInfoPanel ? `${infoPanelWidth + 24}px` : undefined }}
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
        {/* Mobile header — hidden on feed view and desktop */}
        <div className={`md:hidden flex items-center justify-between mb-3 ${libraryViewMode === 'feed' ? 'hidden' : ''}`}>
          <div className="flex items-center gap-2">
            <span className="text-sm text-zinc-200 font-medium">{t('resources.downloads')}</span>
            {!isLoadingLibrary && (
              <span className="text-[11px] text-zinc-600 tabular-nums">
                {totalCount >= 0 ? totalCount : filteredLibrary.length} {(totalCount >= 0 ? totalCount : filteredLibrary.length) === 1 ? 'item' : 'items'}
              </span>
            )}
          </div>
          <button
            onClick={loadLibraryData}
            disabled={isLoadingLibrary}
            className="p-1.5 rounded-lg text-zinc-500 hover:text-zinc-200 transition-colors"
          >
            <RefreshCw size={14} className={isLoadingLibrary ? 'animate-spin' : ''} />
          </button>
        </div>

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
                    onContextMenu={handleContextMenu}
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

            {/* Load more sentinel + tap fallback */}
            {!isSearchActive && libraryViewMode !== 'feed' && (
              <div ref={loadMoreRef} className="w-full py-6 flex justify-center">
                {isLoadingMore ? (
                  <Loader2 size={20} className="animate-spin text-zinc-500" />
                ) : hasMoreData ? (
                  <button
                    onClick={() => loadMoreLibrary()}
                    className="px-6 py-2 text-sm text-zinc-400 hover:text-zinc-200 bg-zinc-800/60 hover:bg-zinc-800 rounded-full transition-colors"
                  >
                    Load More
                  </button>
                ) : library.length > 0 ? (
                  <span className="text-zinc-600 text-xs">All {library.length} items loaded</span>
                ) : null}
              </div>
            )}
          </>
        )}
      </div>

      {/* ── Right Info Panel: Video details (Eagle style, desktop only) ── */}
      {selectedVideo && (
        <div
          className={`hidden md:flex fixed top-14 bottom-0 right-0 z-40 bg-zinc-900 border-l border-zinc-800 transition-transform duration-300 ease-in-out shadow-2xl ${
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
            <div className="sticky top-0 z-10 flex items-center justify-between px-4 py-2 border-b border-zinc-800/80 bg-zinc-900">
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
              {getCoverUrl(selectedVideo, mediaToken ?? undefined) ? (
                <img
                  src={getCoverUrl(selectedVideo, mediaToken ?? undefined)!}
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

            {/* Tags - editable via UnifiedTagPicker */}
            {selectedResourceData && (
              <EagleTagPicker
                assignedTags={selectedVideoTags.map(item => item.tag).filter((t): t is import('../types').Tag => !!t)}
                allTags={allTags}
                onAdd={handleAddTag}
                onRemove={handleRemoveTag}
                onCreate={handleCreateTag}
              />
            )}

            {/* Platform hashtags (read-only) */}
            {selectedVideo?.hashtags && (
              <div className="px-4 mt-3">
                <h4 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-2">
                  Platform Tags
                </h4>
                <div className="flex flex-wrap gap-1.5">
                  {selectedVideo.hashtags.split(/\s+/).filter(h => h.startsWith('#') && h.length > 1).map((ht, i) => (
                    <span key={i} className="text-[10px] px-2 py-0.5 rounded bg-zinc-800/50 text-zinc-500 border border-zinc-700/50">
                      {ht}
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

            {/* Notes (from resources table) */}
            {selectedResourceData && (
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
            )}

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
                    <span className="text-xs text-zinc-300">{formatResolution(selectedVideo.resolution)}</span>
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

            {/* Bottom padding */}
            <div className="pb-6" />
          </div>
        </div>
      )}

      {/* Expand tab — visible when panel is closed (desktop only) */}
      {selectedVideo && !showInfoPanel && (
        <button
          onClick={() => setShowInfoPanel(true)}
          className="hidden md:flex fixed bottom-8 right-0 w-10 h-12 bg-zinc-900 border-l border-y border-zinc-800 rounded-l-xl items-center justify-center text-zinc-400 hover:text-white cursor-pointer hover:bg-zinc-800 transition-all z-50"
        >
          <ChevronLeft size={20} />
        </button>
      )}

      {/* ── Batch Selection Toolbar ── */}
      {selectedIds.size > 0 && (
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
          <div className="w-px h-5 bg-zinc-700" />
          <button
            onClick={() => { setSelectedIds(new Set()); setMultiSelectMode(false); }}
            className="p-1.5 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* ── Context Menu ── */}
      {contextMenu && (
        <div
          className="fixed z-[100] bg-zinc-900 border border-zinc-700 rounded-lg shadow-2xl overflow-hidden py-1 min-w-[180px]"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            onClick={() => {
              if (!contextMenu) return;
              const v = contextMenu.video;
              const teamPath = selectedTeamId ? `/team/${selectedTeamId}` : '';
              const rid = (v as any).resource_id || v.id;
              navigate(`${teamPath}/resources/file/${rid}`);
              setContextMenu(null);
            }}
            className="w-full px-3 py-2 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2.5 transition-colors"
          >
            <Eye size={14} className="text-zinc-500" />
            View Details
          </button>
          <button
            onClick={handleCtxOpenNewTab}
            className="w-full px-3 py-2 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2.5 transition-colors"
          >
            <ExternalLink size={14} className="text-zinc-500" />
            Open in New Tab
          </button>
          <div className="border-t border-zinc-800 my-1" />
          <button
            onClick={handleCtxDownloadVideo}
            className="w-full px-3 py-2 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2.5 transition-colors"
          >
            <Download size={14} className="text-zinc-500" />
            Download Original
          </button>
          {contextMenu.video.music_download_path && (
            <button
              onClick={handleCtxDownloadAudio}
              className="w-full px-3 py-2 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2.5 transition-colors"
            >
              <Music size={14} className="text-zinc-500" />
              Download Audio
            </button>
          )}
          <div className="border-t border-zinc-800 my-1" />
          <button
            onClick={handleCtxRename}
            className="w-full px-3 py-2 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2.5 transition-colors"
          >
            <Pencil size={14} className="text-zinc-500" />
            Rename
          </button>
          <button
            onClick={handleCtxShare}
            className="w-full px-3 py-2 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2.5 transition-colors"
          >
            <Share2 size={14} className="text-zinc-500" />
            Share
          </button>
          <button
            onClick={() => {
              if (!contextMenu) return;
              const url = contextMenu.video.original_url;
              if (url) {
                navigator.clipboard.writeText(url);
                addToast('Link copied', 'success');
              }
              setContextMenu(null);
            }}
            className="w-full px-3 py-2 text-left text-sm text-zinc-300 hover:bg-zinc-800 hover:text-white flex items-center gap-2.5 transition-colors"
          >
            <Link size={14} className="text-zinc-500" />
            Copy Link
          </button>
          <div className="border-t border-zinc-800 my-1" />
          <button
            onClick={handleCtxDelete}
            className="w-full px-3 py-2 text-left text-sm text-red-400 hover:bg-red-900/20 hover:text-red-300 flex items-center gap-2.5 transition-colors"
          >
            <Trash2 size={14} />
            Delete
          </button>
        </div>
      )}

      {/* ── Mobile Search Overlay (hidden in feed view) ── */}
      {libraryViewMode !== 'feed' && createPortal(
        <div className="md:hidden fixed top-14 left-0 right-0 z-40 p-3 flex justify-end items-start pointer-events-none">
          <div className="pointer-events-auto flex items-center justify-end w-full max-w-[calc(100%-16px)]">
            {isMobileSearchOpen ? (
              <div className="flex items-center bg-black/50 backdrop-blur-md rounded-full px-4 py-2.5 w-full animate-in slide-in-from-right-10 duration-200 border border-white/10 shadow-lg">
                <Search size={16} className="text-zinc-300 mr-2 flex-shrink-0" />
                <input
                  autoFocus
                  className="bg-transparent border-none outline-none text-white text-sm w-full placeholder-zinc-400"
                  placeholder="Search downloads..."
                  value={mobileSearchQuery}
                  onChange={(e) => {
                    setMobileSearchQuery(e.target.value);
                    handleSearchQueryChange(e.target.value);
                  }}
                />
                <button
                  onClick={() => {
                    setIsMobileSearchOpen(false);
                    setMobileSearchQuery('');
                    handleSearchQueryChange('');
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
        </div>,
        document.body,
      )}

      {/* ── Share Modal ── */}
      <ShareModal
        isOpen={!!shareTargetResourceId}
        onClose={() => { setShareTargetResourceId(null); setShareTargetName(''); }}
        resourceId={shareTargetResourceId || undefined}
        defaultName={shareTargetName}
      />

      {/* ── Rename Dialog ── */}
      {renameTarget && (
        <div
          className="fixed inset-0 z-[100] bg-black/60 flex items-center justify-center"
          onClick={() => setRenameTarget(null)}
        >
          <div
            className="bg-zinc-900 border border-zinc-700 rounded-xl shadow-2xl w-full max-w-sm mx-4 p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-sm font-semibold text-white mb-3">Rename</h3>
            <input
              autoFocus
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleRenameSubmit(); if (e.key === 'Escape') setRenameTarget(null); }}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-zinc-500 transition-colors"
            />
            <div className="flex justify-end gap-2 mt-4">
              <button
                onClick={() => setRenameTarget(null)}
                className="px-3 py-1.5 text-xs text-zinc-400 hover:text-white bg-zinc-800 hover:bg-zinc-700 rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleRenameSubmit}
                className="px-3 py-1.5 text-xs text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
