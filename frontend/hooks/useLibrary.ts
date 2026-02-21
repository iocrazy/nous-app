import React, { useState, useEffect, useRef, useMemo } from 'react';
import { ParsedMedia, Video, Collection } from '../types';
import { getSupabaseClient, isSupabaseConfigured } from '../supabaseClient';
import { fetchLibraryPaginated, updateItem, deleteItem, cleanupStaleDownloads } from '../services/dataService';
import { fetchMyCollections, createCollection, fetchVideoCollections, addVideoToCollection, removeVideoFromCollection } from '../services/collectionService';
import { MOCK_LIBRARY } from '../constants';
import { LibraryTab } from '../components/LibraryTabs';
import { SearchResult } from '../services/searchService';

interface UseLibraryParams {
  isAuthenticated: boolean;
  selectedTeamId: string | null;
  onVideoRealtimeUpdate?: (video: Video) => void;
}

export function useLibrary({ isAuthenticated, selectedTeamId, onVideoRealtimeUpdate }: UseLibraryParams) {
  // Core library state
  const [library, setLibrary] = useState<Video[]>([]);
  const [isLoadingLibrary, setIsLoadingLibrary] = useState(false);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [selectedLibraryItem, setSelectedLibraryItem] = useState<Video | null>(null);

  // Pagination
  const [currentPage, setCurrentPage] = useState(0);
  const [hasMoreData, setHasMoreData] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
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

  // --- Data Loading ---
  const loadLibraryData = async () => {
    setIsLoadingLibrary(true);
    setLibraryError(null);
    setCurrentPage(0);
    setHasMoreData(true);
    try {
      if (isSupabaseConfigured()) {
        // Clean up downloads stuck in 'downloading' state before loading
        await cleanupStaleDownloads().catch(() => {});
        const result = await fetchLibraryPaginated(0);
        setLibrary(result.data);
        setHasMoreData(result.hasMore);
        setCurrentPage(0);
        if (result.data.length === 0) {
          console.log("Supabase connected but returned no data.");
        }
      } else {
        setLibrary(MOCK_LIBRARY);
        setHasMoreData(false);
      }
    } catch (err: any) {
      console.error("Failed to load library:", err);
      setLibrary(MOCK_LIBRARY);
      setHasMoreData(false);
      const errorMessage = err?.message || (typeof err === 'object' ? JSON.stringify(err) : String(err));
      setLibraryError(`Could not fetch real data (${errorMessage}). Using local cache.`);
    } finally {
      setIsLoadingLibrary(false);
    }
  };

  const loadMoreLibrary = async () => {
    if (!hasMoreData || isLoadingMore || !isSupabaseConfigured()) return;
    setIsLoadingMore(true);
    try {
      const nextPage = currentPage + 1;
      const result = await fetchLibraryPaginated(nextPage);
      if (result.data.length > 0) {
        setLibrary(prev => [...prev, ...result.data]);
        setCurrentPage(nextPage);
        setHasMoreData(result.hasMore);
      } else {
        setHasMoreData(false);
      }
    } catch (err) {
      console.error("Failed to load more library data:", err);
    } finally {
      setIsLoadingMore(false);
    }
  };

  // Fetch library on auth
  useEffect(() => {
    if (isAuthenticated) {
      loadLibraryData();
    }
  }, [isAuthenticated]);

  // Fetch collections on auth
  useEffect(() => {
    if (isAuthenticated) {
      fetchMyCollections().then(setCollections).catch(console.error);
    }
  }, [isAuthenticated]);

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

  // Intersection Observer for infinite scroll
  useEffect(() => {
    if (!loadMoreRef.current) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const [entry] = entries;
        if (entry.isIntersecting && hasMoreData && !isLoadingMore && !isSearchActive) {
          loadMoreLibrary();
        }
      },
      { threshold: 0.1, rootMargin: '100px' }
    );
    observer.observe(loadMoreRef.current);
    return () => observer.disconnect();
  }, [hasMoreData, isLoadingMore, isSearchActive, currentPage]);

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

  // Videos realtime
  useEffect(() => {
    const supabase = getSupabaseClient();
    if (!isAuthenticated || !isSupabaseConfigured() || !supabase) return;

    const channel = supabase
      .channel('parsed_media_realtime')
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'parsed_media' },
        async (payload) => {
          console.log('Realtime update:', payload.eventType, payload);
          const { data: { user } } = await supabase.auth.getUser();
          if (!user) return;

          const newRecord = payload.new as Video;
          const oldRecord = payload.old as Video;

          if (payload.eventType === 'INSERT' && newRecord.user_id === user.id) {
            setLibrary(prev => {
              if (prev.find(item => item.platform_id === newRecord.platform_id)) return prev;
              return [newRecord, ...prev];
            });
          } else if (payload.eventType === 'UPDATE' && newRecord.user_id === user.id) {
            setLibrary(prev =>
              prev.map(item => item.platform_id === newRecord.platform_id ? newRecord : item)
            );
            setSelectedLibraryItem(prev =>
              prev?.platform_id === newRecord.platform_id ? newRecord : prev
            );
            onVideoRealtimeUpdate?.(newRecord);
          } else if (payload.eventType === 'DELETE' && oldRecord?.user_id === user.id) {
            setLibrary(prev => prev.filter(item => item.platform_id !== oldRecord.platform_id));
          }
        }
      )
      .subscribe((status) => {
        console.log('Realtime subscription status:', status);
      });

    return () => {
      console.log('Unsubscribing from realtime channel');
      supabase.removeChannel(channel);
    };
  }, [isAuthenticated]);

  // NOTE: media_collections realtime subscription removed — table dropped in 076 migration.
  // Collection realtime will be rebuilt on resource_items when collection UI is migrated.

  // Video tags realtime
  useEffect(() => {
    const supabase = getSupabaseClient();
    if (!isAuthenticated || !isSupabaseConfigured() || !supabase) return;

    const tagsChannel = supabase
      .channel('resource_tags_realtime')
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'resource_tags' },
        async (payload) => {
          console.log('Resource tags realtime update:', payload.eventType, payload);
          const newRecord = payload.new as { resource_id: string; tag_id: string };
          const oldRecord = payload.old as { resource_id: string; tag_id: string };
          const resourceId = newRecord?.resource_id || oldRecord?.resource_id;
          if (!resourceId) return;

          // Look up the parsed_media record via resources table
          const { data: resource } = await supabase
            .from('resources')
            .select('media_id')
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
      .subscribe((status) => {
        console.log('Resource tags realtime subscription status:', status);
      });

    return () => {
      console.log('Unsubscribing from video tags realtime channel');
      supabase.removeChannel(tagsChannel);
    };
  }, [isAuthenticated]);

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
          return title.includes(query) || author.includes(query) || desc.includes(query) || tags.includes(query);
        }
        return true;
      })
      .sort((a, b) => {
        const aTime = a.created_at ? new Date(a.created_at).getTime() : 0;
        const bTime = b.created_at ? new Date(b.created_at).getTime() : 0;
        return bTime - aTime;
      });
  }, [library, isSearchActive, searchResults, activeCollectionId, collectionVideoIds, isTeamLibraryActive, teamLibraryVideoIds, searchQuery]);

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
    hasMoreData,
    isLoadingMore,
    loadMoreRef,

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
  };
}
