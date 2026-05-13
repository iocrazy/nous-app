import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { ParsedMedia, Video, Collection } from '../types';
import { getSupabaseClient, isSupabaseConfigured } from '../supabaseClient';
import {
  fetchLibraryPaginated,
  updateItem,
  deleteItem,
  cleanupStaleDownloads,
  type FetchLibraryFilterParams,
  type LibraryCursor,
} from '../services/dataService';
import { fetchMyCollections, createCollection, fetchVideoCollections, addVideoToCollection, removeVideoFromCollection } from '../services/collectionService';
import { MOCK_LIBRARY } from '../constants';
import { LibraryTab } from '../components/LibraryTabs';
import { SearchResult } from '../services/searchService';

interface UseLibraryParams {
  isAuthenticated: boolean;
  selectedTeamId: string | null;
  onVideoRealtimeUpdate?: (video: Video) => void;
  /** Extra searchable text per item (keyed by item.id), e.g. tag names */
  extraSearchMap?: Record<string, string>;
}

export function useLibrary({ isAuthenticated, selectedTeamId, onVideoRealtimeUpdate, extraSearchMap }: UseLibraryParams) {
  // Active server-side filter params. Default empty = no filter. The
  // DownloadsView pushes its filter bar state through setFilterParams.
  const [filterParams, setFilterParams] = useState<FetchLibraryFilterParams>({});
  // Stable JSON fingerprint so effect deps don't churn on object identity.
  const filterParamsKey = useMemo(
    () => JSON.stringify(filterParams),
    [filterParams],
  );
  const filterParamsRef = useRef<FetchLibraryFilterParams>(filterParams);
  filterParamsRef.current = filterParams;

  // Core library state
  const [library, setLibrary] = useState<Video[]>([]);
  const [isLoadingLibrary, setIsLoadingLibrary] = useState(false);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [selectedLibraryItem, setSelectedLibraryItem] = useState<Video | null>(null);

  // Pagination — switched from OFFSET to cursor (resources.created_at) so
  // deep scrolls stay O(1). currentPage is kept for any consumer that
  // displays the page count, but the actual fetch keys off ``nextCursor``.
  const [currentPage, setCurrentPage] = useState(0);
  const [nextCursor, setNextCursor] = useState<LibraryCursor | null>(null);
  const [hasMoreData, setHasMoreData] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [totalCount, setTotalCount] = useState<number>(-1);
  const [initialLoadComplete, setInitialLoadComplete] = useState(false);
  // True only when the sentinel has actually scrolled into the viewport
  // (rootMargin: 0). Used by mobile UI to show a floating fallback button
  // when the user is at the bottom — the auto-load observer uses a 600px
  // preload zone, so this is strictly a later event.
  const [isSentinelVisible, setIsSentinelVisible] = useState(false);
  const loadMoreRef = useRef<HTMLDivElement>(null);

  // View mode
  const [libraryViewMode, setLibraryViewMode] = useState<'grid' | 'list' | 'feed'>(() => {
    const saved = localStorage.getItem('mediahub_library_preferences');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        if (parsed.viewMode && ['grid', 'list', 'feed'].includes(parsed.viewMode)) {
          return parsed.viewMode;
        }
      } catch {
        return 'grid';
      }
    }
    return 'grid';
  });

  // Library tab
  const [activeLibraryTab, setActiveLibraryTab] = useState<LibraryTab>(() => {
    const saved = localStorage.getItem('mediahub_library_preferences');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        return parsed.activeTab || 'my-library';
      } catch {
        return 'my-library';
      }
    }
    return 'my-library';
  });

  // Search state
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [isSearchActive, setIsSearchActive] = useState(false);
  const [searchQueryText, setSearchQueryText] = useState('');

  // Collections
  const [collections, setCollections] = useState<Collection[]>([]);
  const [activeCollectionId, setActiveCollectionId] = useState<string | null>(null);
  const [collectionVideoIds, setCollectionVideoIds] = useState<string[]>([]);
  const [isCreateCollectionModalOpen, setIsCreateCollectionModalOpen] = useState(false);
  const [selectedVideoCollectionIds, setSelectedVideoCollectionIds] = useState<string[]>([]);
  const [activeSmartCollectionId, setActiveSmartCollectionId] = useState<number | null>(null);

  // Team library / shared
  const [teamLibraryVideoIds, setTeamLibraryVideoIds] = useState<string[]>([]);
  const [sharedVideoIds, setSharedVideoIds] = useState<string[]>([]);

  // Derived
  const isTeamLibraryActive = activeLibraryTab === 'team-library';

  // --- Persistence ---
  useEffect(() => {
    localStorage.setItem('mediahub_library_preferences', JSON.stringify({
      activeTab: activeLibraryTab,
      selectedTeamId,
      viewMode: libraryViewMode,
    }));
  }, [activeLibraryTab, selectedTeamId, libraryViewMode]);

  // Abort controller for in-flight library queries — cancelled on re-load/unmount
  const libraryAbortRef = useRef<AbortController | null>(null);

  // State refs — read inside the useCallback-wrapped loaders so the
  // closure stays stable across renders even when state changes. Without
  // these, every state update gave `loadMoreLibrary` a fresh identity,
  // which made DownloadsView's "belt-and-suspenders infinite scroll"
  // useEffect (deps include `loadMoreLibrary`) re-fire on every realtime
  // tick + every state update. Each re-fire called checkSentinel() at
  // mount, which fired loadMore again, which aborted the in-flight
  // request → 284× AbortError on a single page mount (2026-05-13 bug).
  const stateRef = useRef({
    currentPage,
    nextCursor,
    hasMoreData,
    isLoadingMore,
  });
  stateRef.current = { currentPage, nextCursor, hasMoreData, isLoadingMore };

  // --- Data Loading ---
  const loadLibraryData = useCallback(async () => {
    // Cancel any previous in-flight request
    libraryAbortRef.current?.abort();
    const controller = new AbortController();
    libraryAbortRef.current = controller;

    setIsLoadingLibrary(true);
    setLibraryError(null);
    setCurrentPage(0);
    setNextCursor(null);
    setHasMoreData(true);
    try {
      if (isSupabaseConfigured()) {
        const isMobile = typeof window !== 'undefined' && window.innerWidth < 768;
        const result = await fetchLibraryPaginated(
          0,
          isMobile ? 10 : 20,
          controller.signal,
          filterParamsRef.current,
          null, // cursor: null = first page
        );
        if (controller.signal.aborted) return;
        setLibrary(result.data);
        setHasMoreData(result.hasMore);
        setNextCursor(result.nextCursor);
        setCurrentPage(0);
        if (result.totalCount >= 0) setTotalCount(result.totalCount);
      } else {
        setLibrary(MOCK_LIBRARY);
        setHasMoreData(false);
      }
    } catch (err: any) {
      // Swallow AbortError — it means a newer load/filter change took over
      // and the previous fetch was intentionally cancelled.
      if (
        err?.name === 'AbortError' ||
        controller.signal.aborted ||
        err?.message?.includes('aborted')
      ) {
        return;
      }
      console.error("Failed to load library:", err);
      setLibrary([]);
      setHasMoreData(false);
      const errorMessage = err?.message || (typeof err === 'object' ? JSON.stringify(err) : String(err));
      setLibraryError(errorMessage);
    } finally {
      // Only clear loading if this request wasn't superseded by a newer one.
      if (!controller.signal.aborted) {
        setIsLoadingLibrary(false);
        setInitialLoadComplete(true);
      }
    }
  // Stable identity: state is read via stateRef / filterParamsRef.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadMoreLibrary = useCallback(async () => {
    const s = stateRef.current;
    // Re-entrancy guard via ref — state updates batch, so two synchronous
    // calls in the same render tick would both see isLoadingMore=false
    // before setIsLoadingMore(true) flushes. The ref is mutated
    // synchronously and prevents the double-fire that caused the abort
    // storm.
    if (!s.hasMoreData || s.isLoadingMore || !isSupabaseConfigured()) return;
    if (!s.nextCursor) {
      // No cursor → no next page (or initial load hasn't completed yet).
      // Don't fall back to OFFSET — that would re-fetch page 1.
      setHasMoreData(false);
      return;
    }
    stateRef.current.isLoadingMore = true;
    setIsLoadingMore(true);
    try {
      const nextPage = s.currentPage + 1;
      const isMobile = typeof window !== 'undefined' && window.innerWidth < 768;
      const controller = new AbortController();
      libraryAbortRef.current?.abort();
      libraryAbortRef.current = controller;
      const result = await fetchLibraryPaginated(
        nextPage,
        isMobile ? 10 : 20,
        controller.signal,
        filterParamsRef.current,
        s.nextCursor,
      );
      if (controller.signal.aborted) return;
      if (result.data.length > 0) {
        setLibrary(prev => [...prev, ...result.data]);
        setCurrentPage(nextPage);
        setNextCursor(result.nextCursor);
        setHasMoreData(result.hasMore);
      } else {
        setHasMoreData(false);
        setNextCursor(null);
      }
    } catch (err: any) {
      if (err?.name === 'AbortError' || err?.message?.includes('aborted')) {
        return;
      }
      console.error("Failed to load more library data:", err);
    } finally {
      stateRef.current.isLoadingMore = false;
      setIsLoadingMore(false);
    }
  // Stable identity — see comment on loadLibraryData.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fetch library on auth
  useEffect(() => {
    if (isAuthenticated) {
      loadLibraryData();
    }
    // loadLibraryData is intentionally omitted — it reads state refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated]);

  // Re-fetch when filter params change (server-side filter, paginated
  // so totalCount + hasMore refresh correctly).
  useEffect(() => {
    if (!isAuthenticated) return;
    loadLibraryData();
    // filterParamsKey is a JSON fingerprint; loadLibraryData reads
    // filterParamsRef.current.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterParamsKey]);

  // Fetch collections + cleanup AFTER initial data load (reduce concurrent NAS requests)
  useEffect(() => {
    if (!initialLoadComplete || !isAuthenticated) return;
    fetchMyCollections().then(setCollections).catch(console.error);
    // Defer cleanup further to avoid competing with collections fetch
    const timer = setTimeout(() => {
      cleanupStaleDownloads().catch(() => {});
    }, 3000);
    return () => clearTimeout(timer);
  }, [initialLoadComplete, isAuthenticated]);

  // Reset on logout
  useEffect(() => {
    if (!isAuthenticated) {
      setLibrary([]);
      setCollections([]);
      setSelectedLibraryItem(null);
      setCollectionVideoIds([]);
      setTeamLibraryVideoIds([]);
      setSharedVideoIds([]);
    }
  }, [isAuthenticated]);

  // Refs to avoid stale closures in IntersectionObserver callback
  const hasMoreDataRef = useRef(hasMoreData);
  const isLoadingMoreRef = useRef(isLoadingMore);
  const isSearchActiveRef = useRef(isSearchActive);
  hasMoreDataRef.current = hasMoreData;
  isLoadingMoreRef.current = isLoadingMore;
  isSearchActiveRef.current = isSearchActive;

  // Intersection Observer for infinite scroll.
  //
  // 2026-05-13 calm-down: rootMargin shrunk from '600px' → '50px'. The
  // 600px preload zone caused an auto-fill cascade: after a load
  // succeeded, library.length grew → useEffect re-fired → observer
  // recreated → if the new sentinel was within 600px of the viewport
  // bottom (very common after a single page), `isIntersecting` was
  // immediately true → loadMoreLibrary() fired → load completes →
  // length grows → repeat. This presented as visible 抖动 (flashing)
  // on the Load More UI.
  //
  // 50px keeps a tiny preload margin (so the next page starts loading
  // just before the user hits the literal bottom) without firing
  // multiple cascading pages purely on layout change.
  //
  // The 'belt-and-suspenders' scroll listener in DownloadsView.tsx is
  // still active for cases where this IO can't see the sentinel
  // (intermediate scroll containers); it has its own debouncing via rAF.
  useEffect(() => {
    if (!loadMoreRef.current) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const [entry] = entries;
        if (entry.isIntersecting && hasMoreDataRef.current && !isLoadingMoreRef.current && !isSearchActiveRef.current) {
          loadMoreLibrary();
        }
      },
      { threshold: 0.01, rootMargin: '50px' }
    );
    observer.observe(loadMoreRef.current);
    return () => observer.disconnect();
  }, [hasMoreData, isLoadingMore, isSearchActive, currentPage, library.length]);

  // Visibility tracker for the floating fallback button on mobile.
  // Fires when sentinel actually enters the viewport (no preload zone).
  useEffect(() => {
    if (!loadMoreRef.current) {
      setIsSentinelVisible(false);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        const [entry] = entries;
        setIsSentinelVisible(entry.isIntersecting);
      },
      { threshold: 0, rootMargin: '0px' }
    );
    observer.observe(loadMoreRef.current);
    return () => observer.disconnect();
  }, [library.length]);

  // --- Collection Video Loading ---
  // NOTE: media_collections table has been dropped (076 migration).
  // Collection-video association now uses resource_items + folders.
  // For now, return empty until collection UI is rebuilt on new schema.
  const loadCollectionVideos = async (collectionId: string | null) => {
    setCollectionVideoIds([]);
  };

  useEffect(() => {
    loadCollectionVideos(activeCollectionId);
  }, [activeCollectionId]);

  // Team library video IDs
  // NOTE: media_collections table has been dropped (076 migration).
  // Team library filtering will be rebuilt on resource_items + folders.
  useEffect(() => {
    if (!isTeamLibraryActive) {
      setTeamLibraryVideoIds([]);
    }
  }, [isTeamLibraryActive]);

  // Shared video IDs
  // NOTE: media_collections table has been dropped (076 migration).
  // Shared video logic will be rebuilt on resource_items.
  const loadAllSharedVideos = async (_collectionsToCheck: Collection[]) => {
    setSharedVideoIds([]);
  };

  useEffect(() => {
    loadAllSharedVideos(collections);
  }, [isAuthenticated, collections]);

  // --- Realtime Subscriptions ---

  // Videos realtime — deferred until after initial load to reduce NAS connection contention.
  // parsed_media is a shared/global table with no creator_id column, so the Supabase
  // server filter can't narrow it. We mitigate by ignoring any event whose record
  // isn't already in the local library (or an owned resource for INSERTs).
  useEffect(() => {
    const supabase = getSupabaseClient();
    if (!initialLoadComplete || !isAuthenticated || !isSupabaseConfigured() || !supabase) return;

    let cancelled = false;
    let ownedUserId: string | null = null;

    const buildSubscriptions = async () => {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session?.user || cancelled) return null;
      ownedUserId = session.user.id;

      const channel = supabase
        .channel('parsed_media_realtime')
        .on(
          'postgres_changes',
          { event: '*', schema: 'public', table: 'parsed_media' },
          async (payload) => {
            if (!ownedUserId) return;

            const newRecord = payload.new as Video;
            const oldRecord = payload.old as Video;

            if (payload.eventType === 'INSERT') {
              // parsed_media is global — verify ownership via resources table.
              // Fast early-out: only query when the record is plausibly ours.
              const mediaId = newRecord.id;
              if (!mediaId) return;
              const { data: resource } = await supabase
                .from('resources')
                .select('id')
                .eq('media_id', mediaId)
                .eq('creator_id', ownedUserId)
                .maybeSingle();
              if (!resource) return;
              setLibrary(prev => {
                if (prev.find(item => item.platform_id === newRecord.platform_id)) return prev;
                return [newRecord, ...prev];
              });
            } else if (payload.eventType === 'UPDATE') {
              // Fast client-side filter: ignore events for media not in our library.
              setLibrary(prev => {
                const idx = prev.findIndex(item => item.platform_id === newRecord.platform_id);
                if (idx < 0) return prev;
                return prev.map((item, i) => i === idx ? newRecord : item);
              });
              setSelectedLibraryItem(prev =>
                prev?.platform_id === newRecord.platform_id ? newRecord : prev
              );
              onVideoRealtimeUpdate?.(newRecord);
            } else if (payload.eventType === 'DELETE') {
              setLibrary(prev => prev.filter(item => item.platform_id !== oldRecord?.platform_id));
            }
          }
        )
        .subscribe();

      // Resources realtime — tracks download status changes. Server-filter by
      // creator_id so events for other users never hit this client.
      const resourceChannel = supabase
        .channel('resources_download_realtime')
        .on(
          'postgres_changes',
          {
            event: 'UPDATE',
            schema: 'public',
            table: 'resources',
            filter: `creator_id=eq.${ownedUserId}`,
          },
          async (payload) => {
          const updatedResource = payload.new as any;
          const mediaId = updatedResource?.media_id;
          if (!mediaId) return;

          // Fetch fresh parsed_media to update library item
          const { data: pm } = await supabase
            .from('parsed_media')
            .select('*')
            .eq('id', mediaId)
            .maybeSingle();
          if (!pm) return;

          setLibrary(prev => {
            const idx = prev.findIndex(item => String(item.id) === String(mediaId));
            if (idx < 0) return prev;
            return prev.map((item, i) => i === idx ? { ...item, ...pm } : item);
          });
        }
      )
      .subscribe();

      return { channel, resourceChannel };
    };

    let channels: { channel: any; resourceChannel: any } | null = null;
    buildSubscriptions().then(result => {
      if (cancelled || !result) return;
      channels = result;
    });

    return () => {
      cancelled = true;
      if (channels) {
        supabase.removeChannel(channels.channel);
        supabase.removeChannel(channels.resourceChannel);
      }
    };
  }, [initialLoadComplete, isAuthenticated]);

  // NOTE: media_collections realtime subscription removed — table dropped in 076 migration.
  // Collection realtime will be rebuilt on resource_items when collection UI is migrated.

  // Video tags realtime — deferred until after initial load
  useEffect(() => {
    const supabase = getSupabaseClient();
    if (!initialLoadComplete || !isAuthenticated || !isSupabaseConfigured() || !supabase) return;

    // resource_tags is a junction table with no user_id; server-side filtering
    // requires a schema change. We server-filter by library member resources in
    // memory: only query parsed_media when the resource_id is one we own.
    const tagsChannel = supabase
      .channel('resource_tags_realtime')
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'resource_tags' },
        async (payload) => {
          const newRecord = payload.new as { resource_id: string; tag_id: string };
          const oldRecord = payload.old as { resource_id: string; tag_id: string };
          const resourceId = newRecord?.resource_id || oldRecord?.resource_id;
          if (!resourceId) return;

          // Look up the parsed_media record via resources table; RLS will
          // ensure we only see our own resources, so foreign events no-op.
          const { data: resource } = await supabase
            .from('resources')
            .select('media_id, creator_id')
            .eq('id', resourceId)
            .maybeSingle();

          if (!resource?.media_id) return;

          const { data: updatedVideo, error } = await supabase
            .from('parsed_media')
            .select('*')
            .eq('id', resource.media_id)
            .single();

          if (error || !updatedVideo) {
            console.error('Failed to fetch updated video:', error);
            return;
          }

          const videoId = String(resource.media_id);
          setLibrary(prev =>
            prev.map(item => item.id === videoId ? { ...item, tags: updatedVideo.tags || [] } : item)
          );
          setSelectedLibraryItem(prev =>
            prev?.id === videoId ? { ...prev, tags: updatedVideo.tags || [] } : prev
          );
        }
      )
      .subscribe();

    return () => {
      supabase.removeChannel(tagsChannel);
    };
  }, [initialLoadComplete, isAuthenticated]);

  // --- Selected Video Collections ---
  const loadSelectedVideoCollections = async (awemeId: string) => {
    try {
      const collectionIds = await fetchVideoCollections(awemeId);
      setSelectedVideoCollectionIds(collectionIds);
    } catch (err) {
      console.error('Failed to load video collections:', err);
      setSelectedVideoCollectionIds([]);
    }
  };

  useEffect(() => {
    if (selectedLibraryItem?.platform_id) {
      loadSelectedVideoCollections(selectedLibraryItem.platform_id);
    } else {
      setSelectedVideoCollectionIds([]);
    }
  }, [selectedLibraryItem?.platform_id]);

  // --- Handlers ---
  const handleLibraryTabChange = (tab: LibraryTab) => {
    setActiveLibraryTab(tab);
    setActiveCollectionId(null);
    setActiveSmartCollectionId(null);
  };

  const handleCreateCollection = async (name: string, teamId: string | null) => {
    const newCollection = await createCollection(name, teamId || undefined);
    setCollections(prev => [newCollection, ...prev]);
  };

  const handleToggleVideoCollection = async (collectionId: string) => {
    if (!selectedLibraryItem?.platform_id) return;
    const awemeId = selectedLibraryItem.platform_id;
    const isInCollection = selectedVideoCollectionIds.includes(collectionId);
    try {
      if (isInCollection) {
        await removeVideoFromCollection(collectionId, awemeId);
        setSelectedVideoCollectionIds(prev => prev.filter(id => id !== collectionId));
      } else {
        await addVideoToCollection(collectionId, awemeId);
        setSelectedVideoCollectionIds(prev => [...prev, collectionId]);
      }
    } catch (err) {
      console.error('Failed to toggle video collection:', err);
    }
  };

  const handleUpdateLibraryItem = async (id: string, updates: Partial<Video>) => {
    setLibrary(prev => prev.map(item =>
      item.platform_id === id ? { ...item, ...updates } : item
    ));
    try {
      if (isSupabaseConfigured()) {
        await updateItem(id, updates);
      }
    } catch (err) {
      console.error("Update failed:", err);
    }
  };

  const handleDeleteLibraryItem = async (id: string, deleteFiles: boolean) => {
    try {
      if (isSupabaseConfigured()) {
        await deleteItem(id, deleteFiles);
        setLibrary(prev => prev.filter(item => item.platform_id !== id));
        if (selectedLibraryItem?.platform_id === id) {
          setSelectedLibraryItem(null);
        }
      }
    } catch (err) {
      console.error("Delete failed:", err);
      throw err;
    }
  };

  // --- Filtered library memo ---
  const filteredLibrary = useMemo(() => {
    if (isSearchActive) {
      if (searchResults.length === 0) return [];
      const searchAwemeIds = new Set(searchResults.map(r => r.platform_id));
      return library
        .filter(item => searchAwemeIds.has(item.platform_id))
        .sort((a, b) => {
          const aScore = searchResults.find(r => r.platform_id === a.platform_id)?.similarity_score || 0;
          const bScore = searchResults.find(r => r.platform_id === b.platform_id)?.similarity_score || 0;
          return bScore - aScore;
        });
    }

    return library
      .filter(item => {
        if (activeCollectionId && !collectionVideoIds.includes(item.platform_id)) return false;
        if (isTeamLibraryActive && !activeCollectionId && !teamLibraryVideoIds.includes(item.platform_id)) return false;
        if (searchQuery.trim()) {
          const query = searchQuery.toLowerCase().trim();
          const title = (item.title || '').toLowerCase();
          const author = ((item as any).author_nickname || '').toLowerCase();
          const desc = (item.description || '').toLowerCase();
          const tags = ((item as any).video_tag || []).join(' ').toLowerCase();
          const extra = (extraSearchMap?.[item.id] || '').toLowerCase();
          return title.includes(query) || author.includes(query) || desc.includes(query) || tags.includes(query) || extra.includes(query);
        }
        return true;
      })
      .sort((a, b) => {
        const aTime = a.created_at ? new Date(a.created_at).getTime() : 0;
        const bTime = b.created_at ? new Date(b.created_at).getTime() : 0;
        return bTime - aTime;
      });
  }, [library, isSearchActive, searchResults, activeCollectionId, collectionVideoIds, isTeamLibraryActive, teamLibraryVideoIds, searchQuery, extraSearchMap]);

  return {
    // Core state
    library,
    setLibrary,
    isLoadingLibrary,
    libraryError,
    selectedLibraryItem,
    setSelectedLibraryItem,
    filteredLibrary,

    // Pagination
    totalCount,
    hasMoreData,
    isLoadingMore,
    loadMoreRef,
    isSentinelVisible,
    loadMoreLibrary,

    // View
    libraryViewMode,
    setLibraryViewMode,
    activeLibraryTab,
    setActiveLibraryTab,
    isTeamLibraryActive,

    // Search
    searchQuery,
    setSearchQuery,
    searchResults,
    setSearchResults,
    isSearchActive,
    setIsSearchActive,
    searchQueryText,
    setSearchQueryText,

    // Collections
    collections,
    setCollections,
    activeCollectionId,
    setActiveCollectionId,
    collectionVideoIds,
    isCreateCollectionModalOpen,
    setIsCreateCollectionModalOpen,
    selectedVideoCollectionIds,
    activeSmartCollectionId,
    setActiveSmartCollectionId,

    // Shared
    sharedVideoIds,

    // Functions
    loadLibraryData,
    handleLibraryTabChange,
    handleCreateCollection,
    handleToggleVideoCollection,
    handleUpdateLibraryItem,
    handleDeleteLibraryItem,

    // Server-side filter params (pushed to PostgREST on fetch)
    filterParams,
    setFilterParams,
  };
}
