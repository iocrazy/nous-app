import React, { useRef, useState, useCallback, useEffect, useMemo } from 'react';
import { createPortal } from 'react-dom';
import {
  Filter,
  RefreshCw,
  LayoutGrid,
  LayoutList,
  Smartphone,
  Loader2,
  Download,
  Trash2,
  X,
  Search,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { useLibraryContext } from '../contexts/LibraryContext';
import { useTeamContext } from '../contexts/TeamContext';
import { Video } from '../types';
import { FilterBar } from './resources/filter/FilterBar';
import { useFilterBarConfig } from '../hooks/useFilterBarConfig';
import { useFilterBarVisibility } from '../hooks/useFilterBarVisibility';
import { CompactMediaCard } from './CompactMediaCard';
import { LibraryTable } from './LibraryTable';
import { LibraryFeed } from './LibraryFeed';
import { ToolbarSearch } from './ToolbarSearch';
import { ShareModal } from './ShareModal';
import { getCoverUrl, getVideoUrl } from '../utils/awemeType';
import {
  semanticSearch,
  hybridSearch,
  localSearch,
  textSearch,
  type SearchField,
} from '../services/searchService';
import { SearchScopePicker, loadSearchScope } from './SearchScopePicker';
import { useToast } from './Toast';
import { trashResourceByPlatformId, updateResource } from '../services/resourceService';
import { createTag } from '../services/unifiedTagService';
import { getDownloadUrl, getMusicDownloadUrl } from '../services/dataService';
import { downloadFile, downloadWithAuth } from '../utils/download';
import { useAuth } from '../contexts/AuthContext';

import { DownloadInfoPanel } from './DownloadsView/DownloadInfoPanel';
import { DownloadContextMenu, ContextMenuState } from './DownloadsView/DownloadContextMenu';
import {
  useResourceDataMap,
  useTagSearchMap,
  useAllTags,
  useSelectedVideoTags,
} from './DownloadsView/useDownloadsData';

export const DownloadsView: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { selectedTeamId } = useTeamContext();
  const { addToast } = useToast();
  const { mediaToken } = useAuth();

  // ─── Library data (shared via context — avoids duplicate Supabase fetch) ───
  const {
    library,
    isLoadingLibrary,
    libraryError,
    totalCount,
    hasMoreData,
    isLoadingMore,
    loadMoreRef,
    isSentinelVisible,
    loadMoreLibrary,
    libraryViewMode,
    setLibraryViewMode,
    sharedVideoIds,
    setLibrary,
    loadLibraryData,
    handleUpdateLibraryItem,
    setFilterParams: setLibraryFilterParams,
  } = useLibraryContext();

  // ─── Filter bar (Eagle-style chip toolbar) ─────────────
  const filterBarConfig = useFilterBarConfig();
  const { visible: isFilterBarVisible, toggle: toggleFilterBar } = useFilterBarVisibility();

  // Push chip values to the library fetch as server-side filter params.
  // The Type chip uses ``types`` (mime-prefix style for the Resources
  // path); for the library/parsed_media path we map it to ``media_types``
  // and let the dataService translate to media_type wire values.
  const libraryFilterParams = useMemo(() => {
    const { types, ...rest } = filterBarConfig.toFilterParams();
    return {
      ...rest,
      ...(types && types.length > 0 ? { media_types: types } : {}),
    };
  }, [filterBarConfig]);
  const libraryFilterParamsKey = useMemo(
    () => JSON.stringify(libraryFilterParams),
    [libraryFilterParams],
  );
  useEffect(() => {
    setLibraryFilterParams(libraryFilterParams);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [libraryFilterParamsKey]);

  // Platforms observed in the current library — enriches the Source
  // chip dropdown beyond the hardcoded known list.
  const availablePlatforms = useMemo<string[]>(() => {
    const seen = new Set<string>();
    for (const item of library) {
      if (typeof item.source_platform === 'string' && item.source_platform) {
        seen.add(item.source_platform);
      }
    }
    return Array.from(seen).sort();
  }, [library]);

  // ─── Local search state ───────────────────────────────
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<import('../services/searchService').SearchResult[]>([]);
  /** Backend-hydrated full Video rows for every hit in ``searchResults``,
   *  keyed by platform_id. Populated on handleAISearch from the ``videos``
   *  field of SearchResponse. Used by ``filteredLibrary`` so the cards
   *  (including AI-status icons, counts, audio paths) render correctly even
   *  for hits that aren't in the paginated library yet. */
  const [searchVideoMap, setSearchVideoMap] = useState<Record<string, Video>>({});
  const [isSearchActive, setIsSearchActive] = useState(false);
  const [searchQueryText, setSearchQueryText] = useState('');
  const [isAISearching, setIsAISearching] = useState(false);
  // Eagle-style search-scope toggles (Title / Description / Author /
  // Hashtags). Persisted in localStorage so the choice survives reloads.
  const [searchScope, setSearchScope] = useState<SearchField[]>(() => loadSearchScope());

  // ─── Resource data ───────────────────────────────────
  // Need media_ids from the paginated library AND from the active search
  // result set, otherwise hits that fall outside the loaded slice render
  // with stale/default AI-status icons (transcript / summary / analysis
  // all gray). searchVideoMap is keyed by platform_id; its values carry
  // ``id`` (parsed_media id), so pull from there.
  const mediaIds = useMemo(() => {
    const ids = new Set<string>();
    for (const item of library) {
      if (item.id) ids.add(String(item.id));
    }
    for (const v of Object.values(searchVideoMap)) {
      const vid = (v as any).id;
      if (vid != null) ids.add(String(vid));
    }
    return Array.from(ids);
  }, [library, searchVideoMap]);
  const { resourceDataMap, setResourceDataMap, resourceIdMap, aiStatusMap } = useResourceDataMap(mediaIds);
  const tagSearchMap = useTagSearchMap(resourceDataMap);
  const { allTags, setAllTags } = useAllTags();

  // ─── Pull-to-refresh (mobile) ───
  const contentScrollRef = useRef<HTMLDivElement>(null);
  const pullStartY = useRef(0);
  const [pullDistance, setPullDistance] = useState(0);
  const [pullRefreshing, setPullRefreshing] = useState(false);

  const handlePullStart = useCallback((e: React.TouchEvent) => {
    if (window.innerWidth >= 768) return;
    const scrollTop = contentScrollRef.current?.scrollTop ?? 0;
    if (scrollTop <= 0) {
      pullStartY.current = e.touches[0].clientY;
    }
  }, []);

  const handlePullMove = useCallback((e: React.TouchEvent) => {
    if (window.innerWidth >= 768 || pullRefreshing) return;
    const scrollTop = contentScrollRef.current?.scrollTop ?? 0;
    if (scrollTop > 0) { setPullDistance(0); return; }
    const delta = e.touches[0].clientY - pullStartY.current;
    if (delta > 0) {
      setPullDistance(Math.min(delta * 0.4, 80));
    }
  }, [pullRefreshing]);

  const handlePullEnd = useCallback(() => {
    if (pullDistance >= 50 && !pullRefreshing) {
      setPullRefreshing(true);
      setPullDistance(50);
      loadLibraryData().finally(() => {
        setPullRefreshing(false);
        setPullDistance(0);
      });
    } else {
      setPullDistance(0);
    }
  }, [pullDistance, pullRefreshing, loadLibraryData]);

  // ─── Belt-and-suspenders infinite scroll (mobile-safe) ─────────────
  // useLibrary already has an IntersectionObserver with default root=viewport.
  // On mobile the layout chain can land us in a state where the scroll
  // happens on an intermediate container rather than the viewport, so that
  // observer never fires. This backup listens for scroll events on BOTH
  // the content container and the window, then checks whether the sentinel
  // is within the 600px preload zone using getBoundingClientRect() — works
  // regardless of which element is doing the scrolling.
  useEffect(() => {
    const sentinel = loadMoreRef.current;
    if (!sentinel) return;
    let rafId: number | null = null;
    const checkSentinel = () => {
      if (rafId != null) return; // debounce via rAF
      rafId = requestAnimationFrame(() => {
        rafId = null;
        if (!hasMoreData || isLoadingMore || isSearchActive) return;
        if (searchQuery.trim().length > 0) return;
        const rect = sentinel.getBoundingClientRect();
        const viewportH = window.innerHeight || document.documentElement.clientHeight;
        // Trigger when sentinel is within 600px of the bottom of the viewport.
        if (rect.top < viewportH + 600) {
          loadMoreLibrary();
        }
      });
    };
    window.addEventListener('scroll', checkSentinel, { passive: true });
    const scroller = contentScrollRef.current;
    if (scroller) {
      scroller.addEventListener('scroll', checkSentinel, { passive: true });
    }
    // NOTE (2026-05-13): the "also check once on mount" call was removed
    // here. This useEffect re-mounts on every library.length / isLoadingMore
    // change (deps below), and calling checkSentinel() synchronously on
    // each re-mount fired the auto-fill cascade: load returns → length
    // grows → effect remounts → checkSentinel() → loadMore again. The
    // IntersectionObserver in useLibrary already covers the initial
    // "sentinel in zone from page 1" case; scroll events drive the rest.
    return () => {
      if (rafId != null) cancelAnimationFrame(rafId);
      window.removeEventListener('scroll', checkSentinel);
      if (scroller) scroller.removeEventListener('scroll', checkSentinel);
    };
  }, [
    hasMoreData,
    isLoadingMore,
    isSearchActive,
    searchQuery,
    loadMoreLibrary,
    library.length,
  ]);

  // ─── Filtered library ─────────────────────────────────
  // Chip filters are now applied server-side (see libraryFilterParams
  // → useLibrary → fetchLibraryPaginated). Only the local
  // keyword-search pass + AI-search result ordering remain.
  const filteredLibrary = useMemo(() => {
    if (isSearchActive) {
      if (searchResults.length === 0) return [];
      // Priority order for assembling the row data per hit:
      //   1) Already-loaded library row (authoritative, includes resource_id
      //      overrides etc. that the search endpoint doesn't touch)
      //   2) Backend-hydrated ``videos`` from SearchResponse (full
      //      ParsedMedia — includes AI status, counts, paths)
      //   3) Slim SearchResultItem projection (last-resort, preserves card
      //      rendering even when a race drops the ``videos`` field)
      const libraryByPlatformId = new Map(
        library.map((v) => [v.platform_id, v]),
      );
      return searchResults
        .slice()
        .sort(
          (a, b) =>
            (b.similarity_score || 0) - (a.similarity_score || 0),
        )
        .map((r): Video => {
          const existing = libraryByPlatformId.get(r.platform_id);
          if (existing) return existing;
          const hydrated = searchVideoMap[r.platform_id];
          if (hydrated) return hydrated;
          return {
            id: r.media_id != null ? String(r.media_id) : undefined,
            platform_id: r.platform_id,
            original_url: '',
            title: r.title,
            author: r.author ?? undefined,
            description: r.description ?? undefined,
            cover_urls: r.cover_url ? [r.cover_url] : undefined,
            tags: r.tags,
            created_at: r.created_at,
          } as Video;
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
  }, [library, isSearchActive, searchResults, searchVideoMap, searchQuery, tagSearchMap]);

  // ─── Search handlers ──────────────────────────────────
  const handleSearchQueryChange = useCallback((query: string) => {
    setIsSearchActive(false);
    setSearchResults([]);
    setSearchVideoMap({});
    setSearchQuery(query);
  }, []);

  const handleAISearch = useCallback(async (query: string, mode: 'hybrid' | 'semantic' | 'text' = 'text') => {
    setIsAISearching(true);
    try {
      // Default ``text`` = plain ILIKE returning EVERY match (up to 1000),
      // no top-N ranking. This is what users mean when they type a keyword
      // — "show me all videos containing 'memory'", not "top 20 semantically
      // similar". ``hybrid`` / ``semantic`` remain available for callers
      // that explicitly want ranking.
      const response =
        mode === 'semantic'
          ? await semanticSearch(query, 100)
          : mode === 'hybrid'
            ? await hybridSearch(query, {}, 100, 0.5)
            : await textSearch(query, 1000, searchScope);
      setSearchResults(response.results as any);
      // Backend now attaches full ParsedMedia rows in ``videos``. Index them
      // by platform_id so filteredLibrary can render AI-status icons etc.
      // for hits outside the paginated library.
      const hydrated: Record<string, Video> = {};
      for (const v of response.videos ?? []) {
        const pid = (v as any).platform_id;
        if (typeof pid === 'string') hydrated[pid] = v as unknown as Video;
      }
      setSearchVideoMap(hydrated);
      setIsSearchActive(true);
      setSearchQueryText(query);
    } catch (error) {
      console.error('AI search failed:', error);
      const fallback = localSearch(query, library as any, 20);
      setSearchResults(fallback.results as any);
      setIsSearchActive(true);
      setSearchQueryText(query);
    } finally {
      setIsAISearching(false);
    }
  }, [library, searchScope]);

  const handleSearchClear = useCallback(() => {
    setSearchResults([]);
    setSearchVideoMap({});
    setIsSearchActive(false);
    setSearchQueryText('');
    setSearchQuery('');
  }, []);

  // Quick Search auto-promotion: when the user types in keyword mode the
  // local filter only sees the paginated slice that's been loaded so far
  // (e.g. 50 of 417). Debounce-fire ``textSearch`` against the backend so
  // the visible result set reflects the whole library, not just what
  // happened to be loaded. This piggybacks on the same ``isSearchActive``
  // state that AI / Smart search use, so the existing "Found N matching
  // items" footer + hidden Load More UX kicks in for keyword mode too.
  useEffect(() => {
    const trimmed = searchQuery.trim();
    if (trimmed.length < 2) return;
    const timer = setTimeout(async () => {
      try {
        const response = await textSearch(trimmed, 1000, searchScope);
        setSearchResults(response.results as any);
        const hydrated: Record<string, Video> = {};
        for (const v of response.videos ?? []) {
          const pid = (v as any).platform_id;
          if (typeof pid === 'string') hydrated[pid] = v as unknown as Video;
        }
        setSearchVideoMap(hydrated);
        setIsSearchActive(true);
        setSearchQueryText(trimmed);
      } catch (err) {
        console.error('quick-search backend fetch failed', err);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery, searchScope]);

  const hasActiveQuery = isSearchActive || searchQuery.trim().length > 0;

  // ─── Selection state ───────────────────────────────────
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
  const [shareTargetResourceId, setShareTargetResourceId] = useState<string | null>(null);
  const [shareTargetName, setShareTargetName] = useState<string>('');
  const [isMobileSearchOpen, setIsMobileSearchOpen] = useState(false);
  const [mobileSearchQuery, setMobileSearchQuery] = useState('');
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);

  // ─── Selected video tag / resource data ───────────────
  const selectedResourceData = selectedVideo?.id ? resourceDataMap[selectedVideo.id] : undefined;
  const { selectedVideoTags, handleAddTag, handleRemoveTag } = useSelectedVideoTags(
    selectedResourceData?.id
  );

  useEffect(() => {
    setPanelNotes(selectedResourceData?.notes || '');
    setPanelRating(selectedResourceData?.rating || 0);
  }, [selectedVideo?.platform_id, selectedResourceData]);

  // ─── Navigation ────────────────────────────────────────
  // Prefer resource_id (per-user). Search hydration in PR includes it,
  // but legacy callers / stale cards may still come through with only
  // parsed_media.id — in that case we abort the navigation rather than
  // routing to /resources/file/<parsed_media_id>, which 404s on the
  // detail page (parsed_media.id ≠ resource.id).
  //
  // We pass the full item via router state so ResourceDetailPage can
  // render the card chrome immediately while it fetches the rest —
  // matches the pattern most video sites use (search → detail without
  // a blank-screen pause).
  const handleNavigateToDetail = useCallback((item: Video) => {
    const rid = (item as any).resource_id;
    if (!rid) {
      console.warn('[DownloadsView] item has no resource_id, skipping nav', item);
      return;
    }
    const teamPath = selectedTeamId ? `/team/${selectedTeamId}` : '';
    navigate(`${teamPath}/resources/file/${rid}`, { state: { preloaded: item } });
  }, [selectedTeamId, navigate]);

  // ─── Multi-select ──────────────────────────────────────
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

  const isMobileDevice = typeof window !== 'undefined' && window.innerWidth < 768;
  const clickTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleVideoClick = useCallback((item: Video, e?: React.MouseEvent) => {
    if (e && (e.metaKey || e.ctrlKey || e.shiftKey)) {
      handleToggleSelect(item.platform_id, e);
      return;
    }
    if (isMobileDevice) {
      handleNavigateToDetail(item);
      return;
    }
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

  const handleVideoDoubleClick = useCallback((item: Video) => {
    if (clickTimerRef.current) {
      clearTimeout(clickTimerRef.current);
      clickTimerRef.current = null;
    }
    handleNavigateToDetail(item);
  }, [handleNavigateToDetail]);

  // ─── Delete ────────────────────────────────────────────
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

  // ─── Keyboard shortcuts ────────────────────────────────
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
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

  // ─── Panel resize ──────────────────────────────────────
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

  // ─── Panel rating / notes ──────────────────────────────
  const handlePanelRating = async (star: number) => {
    if (!selectedVideo || !selectedResourceData) return;
    const newRating = star === panelRating ? 0 : star;
    setPanelRating(newRating);
    try {
      await updateResource(selectedResourceData.id, { rating: newRating });
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
        setResourceDataMap(prev => ({
          ...prev,
          [selectedVideo.id!]: { ...prev[selectedVideo.id!], notes: panelNotes },
        }));
      } catch (err) {
        console.error('Failed to update notes:', err);
      }
    }
  };

  const handleCreateTag = useCallback(async (name: string, color: string) => {
    try {
      const tag = await createTag({ name, color, type: 'user' });
      setAllTags(prev => [...prev, tag]);
      return tag;
    } catch {
      return null;
    }
  }, [setAllTags]);

  // ─── Context menu ──────────────────────────────────────
  const handleContextMenu = useCallback((e: React.MouseEvent, video: Video) => {
    const menuHeight = 320;
    const menuWidth = 180;
    const x = Math.min(e.clientX, window.innerWidth - menuWidth - 8);
    const y = e.clientY + menuHeight > window.innerHeight
      ? Math.max(8, e.clientY - menuHeight)
      : e.clientY;
    setContextMenu({ x, y, video });
  }, []);

  const handleCtxViewDetails = useCallback(() => {
    if (!contextMenu) return;
    const v = contextMenu.video;
    const rid = (v as any).resource_id;
    if (!rid) {
      console.warn('[DownloadsView] context-menu item has no resource_id', v);
      setContextMenu(null);
      return;
    }
    const teamPath = selectedTeamId ? `/team/${selectedTeamId}` : '';
    navigate(`${teamPath}/resources/file/${rid}`, { state: { preloaded: v } });
    setContextMenu(null);
  }, [contextMenu, selectedTeamId, navigate]);

  const handleCtxOpenNewTab = useCallback(() => {
    if (!contextMenu) return;
    const v = contextMenu.video;
    const rid = (v as any).resource_id;
    if (!rid) {
      console.warn('[DownloadsView] context-menu item has no resource_id', v);
      setContextMenu(null);
      return;
    }
    const teamPath = selectedTeamId ? `/team/${selectedTeamId}` : '';
    // Cross-tab navigation can't carry router state, so the new tab will
    // re-fetch from the network — same behavior as a hard refresh.
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
  }, [contextMenu, addToast, mediaToken]);

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

  const handleCtxCopyLink = useCallback(() => {
    if (!contextMenu) return;
    const url = contextMenu.video.original_url;
    if (url) {
      navigator.clipboard.writeText(url);
      addToast('Link copied', 'success');
    }
    setContextMenu(null);
  }, [contextMenu, addToast]);

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

  const handleRenameSubmit = useCallback(() => {
    if (!renameTarget || !renameValue.trim()) { setRenameTarget(null); return; }
    handleUpdateLibraryItem(renameTarget.platform_id, { title: renameValue.trim() });
    setRenameTarget(null);
    addToast('Renamed', 'success');
  }, [renameTarget, renameValue, handleUpdateLibraryItem, addToast]);

  // ─── Render ────────────────────────────────────────────
  return (
    <div className={`flex-1 min-w-0 flex flex-col ${libraryViewMode === 'feed' ? '' : 'md:h-full'}`}>
      {/* Toolbar */}
      <div
        className="hidden md:block px-6 py-2 border-b border-zinc-800/80 space-y-2"
        style={{ paddingRight: selectedVideo && showInfoPanel ? `${infoPanelWidth + 24}px` : undefined }}
      >
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <span className="text-sm text-zinc-200 font-medium truncate">{t('resources.downloads')}</span>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={loadLibraryData}
              disabled={isLoadingLibrary}
              className="p-1.5 rounded-lg text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800/80 transition-colors"
              title="Refresh Data"
            >
              <RefreshCw size={14} className={isLoadingLibrary ? 'animate-spin' : ''} />
            </button>

            <ToolbarSearch
              onQueryChange={handleSearchQueryChange}
              onAISearch={handleAISearch}
              onClear={handleSearchClear}
              isSearching={isAISearching}
              placeholder={t('library.searchPlaceholder', 'Search title, tags, notes...')}
              className="w-52"
              searchScope={searchScope}
              onSearchScopeChange={(next) => {
                setSearchScope(next as SearchField[]);
                // Persist Eagle-style — same key as the standalone picker
                // so the choice carries across views.
                try {
                  window.localStorage.setItem(
                    'mediahub_search_scope',
                    JSON.stringify(next),
                  );
                } catch {
                  /* ignore quota / private mode errors */
                }
              }}
            />

            {/* Filter bar visibility toggle — plain funnel, mirrors the
                Resources view. Shows / hides the chip row below. */}
            <button
              type="button"
              onClick={toggleFilterBar}
              className={`p-1.5 rounded-lg transition-colors ${
                isFilterBarVisible
                  ? 'text-indigo-400 bg-indigo-500/10 hover:bg-indigo-500/20'
                  : 'text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800/80'
              }`}
              title={
                isFilterBarVisible
                  ? t('resources.filter.hideFilterBar', 'Hide filter bar')
                  : t('resources.filter.showFilterBar', 'Show filter bar')
              }
              aria-label={
                isFilterBarVisible
                  ? t('resources.filter.hideFilterBar', 'Hide filter bar')
                  : t('resources.filter.showFilterBar', 'Show filter bar')
              }
              aria-pressed={isFilterBarVisible}
            >
              <Filter size={14} />
            </button>

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

        {/* Pinnable filter bar — shared component with Resources. Type
            chip has no meaningful target in Downloads (everything is a
            video) but the rest (rating / tags / source / AI / date /
            duration / aspect / social) all apply via matchesChipFilters. */}
        {isFilterBarVisible && (
          <FilterBar
            config={filterBarConfig}
            allTags={allTags}
            availablePlatforms={availablePlatforms}
          />
        )}
      </div>

      {/* Content */}
      <div
        ref={contentScrollRef}
        className={`flex-1 md:min-h-0 md:overflow-y-auto md:px-5 md:pt-4 ${
          libraryViewMode === 'feed'
            ? 'px-0 pt-0 pb-0 h-full min-h-0'
            // Mobile: leave enough room so the last grid row is never
            // covered by the floating Load More pill + tab bar stack
            // (~7rem + safe-area). Desktop reverts to the original 20 px.
            : 'px-3 pt-3 pb-[calc(7rem+env(safe-area-inset-bottom,6px))] md:pb-5'
        }`}
        style={{ paddingRight: selectedVideo && showInfoPanel ? `${infoPanelWidth + 24}px` : undefined }}
        onTouchStart={handlePullStart}
        onTouchMove={handlePullMove}
        onTouchEnd={handlePullEnd}
        onClick={(e) => {
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
        {/* Pull-to-refresh indicator (mobile only) */}
        {pullDistance > 0 && (
          <div
            className="md:hidden flex items-center justify-center transition-all"
            style={{ height: pullDistance, opacity: Math.min(pullDistance / 60, 1) }}
          >
            <RefreshCw
              size={18}
              className={`text-zinc-400 transition-transform ${pullRefreshing ? 'animate-spin' : ''}`}
              style={{ transform: `rotate(${Math.min(pullDistance * 3, 360)}deg)` }}
            />
          </div>
        )}

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
            {libraryViewMode === 'grid' && (
              <div className="grid grid-cols-2 gap-3 downloads-grid">
                {filteredLibrary.map((item) => (
                  <CompactMediaCard
                    key={item.platform_id}
                    data={item}
                    resourceId={resourceIdMap[item.id]}
                    aiStatus={aiStatusMap[item.id]}
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

            {libraryViewMode === 'list' && (
              <LibraryTable
                data={filteredLibrary}
                onUpdate={handleUpdateLibraryItem}
                onItemClick={(item) => handleVideoClick(item)}
              />
            )}

            {libraryViewMode === 'feed' && (
              <LibraryFeed
                data={filteredLibrary}
                hasMore={!hasActiveQuery && hasMoreData}
                isLoadingMore={isLoadingMore}
                onLoadMore={() => { void loadMoreLibrary(); }}
              />
            )}

            {/* Search-results count — shown whenever a query is active.
                The ``Load More`` block below is hidden in search mode so
                users otherwise have no signal for how many hits were
                returned. While the backend search is debouncing, the
                local filter on the loaded slice is rendered, so the count
                may briefly under-report before the backend response
                lands. */}
            {hasActiveQuery && filteredLibrary.length > 0 && libraryViewMode !== 'feed' && (
              <div className="w-full py-6 flex justify-center">
                <span className="text-zinc-600 text-xs">
                  {t('library.searchResultsCount', 'Found {{count}} matching items', {
                    count: filteredLibrary.length,
                  })}
                </span>
              </div>
            )}

            {!hasActiveQuery && libraryViewMode !== 'feed' && (
              <>
                {/* Invisible sentinel: IntersectionObserver target.
                    Stays at the natural end-of-list position so the
                    600px rootMargin pre-trigger still works. */}
                <div ref={loadMoreRef} aria-hidden="true" className="w-full h-px" />

                {/* End-of-list status — show the count actually on screen,
                    which is filteredLibrary.length when chip filters are
                    active, otherwise equals library.length. */}
                {!hasMoreData && filteredLibrary.length > 0 && (
                  <div className="w-full py-6 flex justify-center">
                    <span className="text-zinc-600 text-xs">
                      All {filteredLibrary.length} items loaded
                    </span>
                  </div>
                )}

                {/* Desktop: inline Load More / spinner at the end of list. */}
                {hasMoreData && (
                  <div className="hidden md:flex w-full py-6 justify-center">
                    {isLoadingMore ? (
                      <Loader2 size={20} className="animate-spin text-zinc-500" />
                    ) : (
                      <button
                        onClick={() => loadMoreLibrary()}
                        className="px-6 py-2 text-sm text-zinc-400 hover:text-zinc-200 bg-zinc-800/60 hover:bg-zinc-800 rounded-full transition-colors"
                      >
                        Load More
                      </button>
                    )}
                  </div>
                )}

                {/* Mobile: floating pill above the fixed tab bar. Only
                    visible when the sentinel has actually entered the
                    viewport (user scrolled to the bottom) — the 600px
                    auto-load observer fires well before this, so the
                    pill is strictly a fallback when auto-load didn't
                    catch the user (e.g. slow network, or scrolling
                    past the rootMargin in one gesture). */}
                {hasMoreData && isSentinelVisible && (
                  <div
                    className="md:hidden fixed left-1/2 -translate-x-1/2 z-30"
                    style={{
                      // 5.5rem = 88px leaves a comfortable ~22 px gap above
                      // the tab bar on devices without safe-area inset (e.g.
                      // desktop emulation, non-notched phones) while staying
                      // visually balanced on iPhones where env(safe-area-
                      // inset-bottom) adds 20-34 px on top.
                      bottom:
                        'calc(env(safe-area-inset-bottom, 6px) + 5.5rem)',
                    }}
                  >
                    {isLoadingMore ? (
                      <div className="px-5 py-2 bg-zinc-900/95 backdrop-blur-md border border-zinc-800/60 rounded-full shadow-2xl">
                        <Loader2 size={18} className="animate-spin text-zinc-400" />
                      </div>
                    ) : (
                      <button
                        onClick={() => loadMoreLibrary()}
                        className="px-5 py-2 text-sm text-zinc-200 bg-zinc-900/95 backdrop-blur-md border border-zinc-700/60 hover:bg-zinc-800 rounded-full shadow-2xl transition-colors"
                      >
                        Load More
                      </button>
                    )}
                  </div>
                )}
              </>
            )}
          </>
        )}
      </div>

      {/* Info Panel */}
      {selectedVideo && (
        <DownloadInfoPanel
          selectedVideo={selectedVideo}
          showInfoPanel={showInfoPanel}
          infoPanelWidth={infoPanelWidth}
          panelNotes={panelNotes}
          panelRating={panelRating}
          panelHoverRating={panelHoverRating}
          selectedResourceData={selectedResourceData}
          selectedVideoTags={selectedVideoTags}
          allTags={allTags}
          mediaToken={mediaToken}
          onClose={() => setSelectedVideo(null)}
          onTogglePanel={setShowInfoPanel}
          onResizeStart={handlePanelResizeStart}
          onAddTag={handleAddTag}
          onRemoveTag={handleRemoveTag}
          onCreateTag={handleCreateTag}
          onRating={handlePanelRating}
          onHoverRating={setPanelHoverRating}
          onNotesChange={setPanelNotes}
          onNotesBlur={handlePanelNotesBlur}
        />
      )}

      {/* Batch Selection Toolbar */}
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

      {/* Context Menu */}
      <DownloadContextMenu
        contextMenu={contextMenu}
        onClose={() => setContextMenu(null)}
        onViewDetails={handleCtxViewDetails}
        onOpenNewTab={handleCtxOpenNewTab}
        onDownloadVideo={handleCtxDownloadVideo}
        onDownloadAudio={handleCtxDownloadAudio}
        onRename={handleCtxRename}
        onShare={handleCtxShare}
        onCopyLink={handleCtxCopyLink}
        onDelete={handleCtxDelete}
        onNavigate={navigate}
      />

      {/* Mobile Search Overlay */}
      {libraryViewMode !== 'feed' && createPortal(
        <div className="md:hidden fixed top-[calc(env(safe-area-inset-top,0px)+10px)] right-3 z-40 flex justify-end items-start pointer-events-none">
          <div className="pointer-events-auto flex items-center justify-end">
            {isMobileSearchOpen ? (
              <div className="flex items-center bg-black/50 backdrop-blur-md rounded-full px-4 py-2.5 w-[calc(100vw-80px)] max-w-sm animate-in slide-in-from-right-10 duration-200 border border-white/10 shadow-lg">
                {isAISearching ? (
                  <Loader2
                    size={16}
                    className="mr-2 flex-shrink-0 animate-spin text-indigo-300"
                  />
                ) : (
                  <Search size={16} className="text-zinc-300 mr-2 flex-shrink-0" />
                )}
                <input
                  autoFocus
                  enterKeyHint="search"
                  className="bg-transparent border-none outline-none text-white text-sm w-full placeholder-zinc-400"
                  placeholder="Search (press Enter for library)"
                  value={mobileSearchQuery}
                  onChange={(e) => {
                    setMobileSearchQuery(e.target.value);
                    // Instant local filter for already-loaded items
                    handleSearchQueryChange(e.target.value);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      const q = mobileSearchQuery.trim();
                      if (q.length > 0) {
                        // ``text`` = plain ILIKE on title/description/author/
                        // hashtags returning EVERY match (not top-N). Mobile
                        // default because users typing a keyword on a small
                        // screen almost always mean "show me all of them".
                        void handleAISearch(q, 'text');
                        // Let the virtual keyboard close so results become visible.
                        (e.target as HTMLInputElement).blur();
                      }
                    }
                  }}
                />
                {/* Eagle-style scope picker — compact variant for the
                    mobile overlay. Click funnel → toggle which fields to
                    search next time the user hits Enter. */}
                <div className="ml-1 flex-shrink-0">
                  <SearchScopePicker
                    value={searchScope}
                    onChange={setSearchScope}
                    compact
                  />
                </div>
                <button
                  onClick={() => {
                    setIsMobileSearchOpen(false);
                    setMobileSearchQuery('');
                    handleSearchQueryChange('');
                    handleSearchClear();
                  }}
                  className="ml-2 text-zinc-400 hover:text-white"
                >
                  <X size={16} />
                </button>
              </div>
            ) : (
              <button
                onClick={() => setIsMobileSearchOpen(true)}
                className="p-2 bg-black/20 backdrop-blur-md rounded-full text-white hover:bg-black/40 transition-colors shadow-lg border border-white/5"
              >
                <Search size={20} className="drop-shadow-md" />
              </button>
            )}
          </div>
        </div>,
        document.body,
      )}

      {/* Share Modal */}
      <ShareModal
        isOpen={!!shareTargetResourceId}
        onClose={() => { setShareTargetResourceId(null); setShareTargetName(''); }}
        resourceId={shareTargetResourceId || undefined}
        defaultName={shareTargetName}
      />

      {/* Rename Dialog */}
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
