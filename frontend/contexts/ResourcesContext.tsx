import React, { createContext, useContext, useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useParams, useNavigate, useLocation, type NavigateFunction } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Folder, ResourceItem, Tag, SmartCollection, Library } from '../types';
import {
  fetchFolders,
  fetchChildFolders,
  fetchResources,
  trashResource,
  restoreResource,
  permanentDeleteResource,
  permanentDeleteFolder,
  fetchTrashedResources,
  fetchDownloadedResources,
  fetchDownloadedResourceCount,
  fetchResourceCount,
  fetchResourceTags,
  addResourceTag,
  removeResourceTag,
  fetchSmartFolders,
  fetchSmartFolderResults,
  trashResources,
  getFolderPreview,
  updateResource,
  fetchTrashedFolders,
  restoreFolder,
  fetchFolderContents,
  type FetchResourcesParams,
} from '../services/resourceService';
import { fetchLibraries } from '../services/libraryService';
import { fetchAllTags as fetchTags } from '../services/unifiedTagService';
import { createTag } from '../services/unifiedTagService';
import { useTaskManager } from './TaskManagerContext';
import { useAuth } from './AuthContext';
import { usePermission } from '../hooks/usePermission';
import { useToast } from '../components/Toast';
import { getSupabaseClient } from '../supabaseClient';
import type { Resource } from '../types';

// ─── Types ─────────────────────────────────────────────

export type SidebarView = 'resources' | 'shared' | 'recycle' | 'downloads' | 'temp';
export type SortBy = 'newest' | 'oldest' | 'name-az' | 'name-za' | 'largest' | 'smallest';

/** Subset of fetchResources params that the filter bar contributes.
 *  Kept separate from the FetchResourcesParams type so consumers don't
 *  accidentally override scope / folder / library. */
export type ResourcesFilterParams = Omit<
  FetchResourcesParams,
  'isPersonal' | 'scopeId' | 'folderId' | 'libraryId'
>;

const EMPTY_FILTER_PARAMS: ResourcesFilterParams = {};

export interface ResourcesContextType {
  // ── Scope / URL-derived state ──
  isPersonal: boolean;
  scopeId: string;
  teamId: string | undefined;
  sidebarView: SidebarView;
  selectedFolderId: string | null;
  selectedSmartFolderId: string | null;
  selectedLibraryId: string | null;
  resPath: (path: string) => string;
  navigate: NavigateFunction;

  // ── Derived view flags ──
  isResourcesView: boolean;
  isRecycleView: boolean;
  isSharedView: boolean;
  isDownloadsView: boolean;
  isTempView: boolean;
  canUpload: boolean;

  // ── Temp view state ──
  tempFolderId: string | null;
  tempResources: ResourceItem[];
  reloadTemp: () => void;

  // ── Data state ──
  resources: ResourceItem[];
  setResources: React.Dispatch<React.SetStateAction<ResourceItem[]>>;
  folders: Folder[];
  childFolders: Folder[];
  folderPreviews: Record<string, Array<{ resource_id: string | null; thumbnail_path: string | null; cover_image_path: string | null; mime_type: string | null }>>;
  trashedResources: ResourceItem[];
  trashedFolders: Folder[];
  downloadedResources: ResourceItem[];
  libraries: Library[];
  setLibraries: React.Dispatch<React.SetStateAction<Library[]>>;
  smartFolders: SmartCollection[];
  setSmartFolders: React.Dispatch<React.SetStateAction<SmartCollection[]>>;
  allTags: Tag[];
  setAllTags: React.Dispatch<React.SetStateAction<Tag[]>>;
  refreshTags: () => Promise<void>;
  /** Total count of user-uploaded resources (non-web, across all folders) for the current scope.
   *  Used by the sidebar to render a count badge on the "My Uploads" entry.
   *  (Variable name retained for historical reasons; UI label is "My Uploads".)
   *  Null while loading. */
  myResourcesCount: number | null;
  /** Total count of "My Downloads" (web-sourced resources) for the current scope.
   *  Used by the sidebar to render a count badge on the "My Downloads" entry.
   *  Null while loading. */
  downloadsCount: number | null;
  /** Trigger a refresh of both sidebar counts (e.g. after trash/restore/upload). */
  refreshSidebarCounts: () => void;
  resourceTagNamesMap: Record<string, string>;
  /**
   * Per-resource set of tag ids. Populated alongside resourceTagNamesMap
   * so the filter bar can match by id without refetching. Values are
   * Sets (stable identity per rebuild) — iterate, don't mutate.
   */
  resourceTagIdsMap: Record<string, Set<string>>;
  loading: boolean;
  setLoading: React.Dispatch<React.SetStateAction<boolean>>;
  folderChain: Folder[];

  // ── Recycle bin navigation ──
  recycleFolderId: string | null;
  setRecycleFolderId: React.Dispatch<React.SetStateAction<string | null>>;
  recycleFolderItems: ResourceItem[];
  pendingPermanentDelete: string | null;
  setPendingPermanentDelete: React.Dispatch<React.SetStateAction<string | null>>;
  pendingBatchPermanentDelete: string[] | null;
  setPendingBatchPermanentDelete: React.Dispatch<React.SetStateAction<string[] | null>>;
  pendingBatchPermanentDeleteFolders: string[] | null;
  setPendingBatchPermanentDeleteFolders: React.Dispatch<React.SetStateAction<string[] | null>>;

  // ── Selection state ──
  selectedResource: ResourceItem | null;
  setSelectedResource: React.Dispatch<React.SetStateAction<ResourceItem | null>>;
  selectedFolder: Folder | null;
  setSelectedFolder: React.Dispatch<React.SetStateAction<Folder | null>>;
  selectedResourceTags: Array<{ tag: Tag }>;
  setSelectedResourceTags: React.Dispatch<React.SetStateAction<Array<{ tag: Tag }>>>;
  selectedIds: Set<string>;
  setSelectedIds: React.Dispatch<React.SetStateAction<Set<string>>>;
  lastClickedId: string | null;
  setLastClickedId: React.Dispatch<React.SetStateAction<string | null>>;
  multiSelectMode: boolean;
  setMultiSelectMode: React.Dispatch<React.SetStateAction<boolean>>;

  // ── View / UI state ──
  viewMode: 'grid' | 'list' | 'justified';
  setViewMode: React.Dispatch<React.SetStateAction<'grid' | 'list' | 'justified'>>;
  sortBy: SortBy;
  setSortBy: React.Dispatch<React.SetStateAction<SortBy>>;
  searchQuery: string;
  setSearchQuery: React.Dispatch<React.SetStateAction<string>>;
  debouncedSearch: string;
  setDebouncedSearch: React.Dispatch<React.SetStateAction<string>>;
  showInfoPanel: boolean;
  setShowInfoPanel: React.Dispatch<React.SetStateAction<boolean>>;
  infoPanelWidth: number;
  setInfoPanelWidth: React.Dispatch<React.SetStateAction<number>>;

  // ── Server-side filter params (contributed by the filter bar) ──
  /** Current server-side filter params. Changes trigger a resource
   *  re-fetch. Immutable — always replace, never mutate. */
  filterParams: ResourcesFilterParams;
  /** Replace the active filter params. Passing an empty object clears. */
  setFilterParams: (p: ResourcesFilterParams) => void;
  /** Re-fetch resources respecting the active scope, folder, library,
   *  and filter params. Use after mutations (move/copy/trash) to pull
   *  a fresh server-filtered list. */
  reloadResources: () => Promise<void>;

  // ── Actions ──
  loadFolders: () => Promise<void>;
  loadChildFolders: () => Promise<void>;
  loadTrashedResources: () => Promise<void>;
  loadDownloadedResources: () => Promise<void>;
  handleTrashResource: (resourceId: string) => Promise<void>;
  handleRestoreResource: (resourceId: string) => Promise<void>;
  handlePermanentDelete: (resourceId: string) => void;
  confirmPermanentDelete: () => Promise<void>;
  handleAddTag: (tagId: string) => Promise<void>;
  handleRemoveTag: (tagId: string) => Promise<void>;
  handleCreateTag: (name: string, color: string) => Promise<Tag | null>;
  handleResourceUpdate: (data: Partial<Resource>) => Promise<void>;

  // ── Permission + Toast ──
  canDo: (action: string) => boolean;
  addToast: (message: string, type: 'success' | 'error' | 'info') => void;

  // ── Computed / derived data ──
  transcodingResourceIds: Set<string>;
}

const ResourcesContext = createContext<ResourcesContextType | null>(null);

// ─── Provider ──────────────────────────────────────────

interface ResourcesProviderProps {
  isPersonal: boolean;
  scopeId: string;
  children: React.ReactNode;
}

export const ResourcesProvider: React.FC<ResourcesProviderProps> = ({
  isPersonal,
  scopeId,
  children,
}) => {
  const { t } = useTranslation();
  const { tasks: allUnifiedTasks } = useTaskManager();
  const { currentUserId } = useAuth();
  const { teamId, section, folderId: urlFolderId, smartFolderId: urlSmartFolderId, libraryId: urlLibraryId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const resPath = useCallback((path: string) => teamId ? `/team/${teamId}${path}` : path, [teamId]);

  // ── URL-driven state ──
  const sidebarView: SidebarView = urlFolderId || urlSmartFolderId || urlLibraryId
    ? 'resources'
    : (['shared', 'recycle', 'downloads', 'temp'].includes(section || '') ? section as SidebarView : 'resources');
  const selectedFolderId = urlFolderId ?? null;
  const selectedSmartFolderId = urlSmartFolderId ?? null;
  const selectedLibraryId = urlLibraryId ?? null;


  // ── Data state ──
  const [folders, setFolders] = useState<Folder[]>([]);
  const [childFolders, setChildFolders] = useState<Folder[]>([]);
  const [folderPreviews, setFolderPreviews] = useState<Record<string, Array<{ resource_id: string | null; thumbnail_path: string | null; cover_image_path: string | null; mime_type: string | null }>>>({});
  const [resources, setResources] = useState<ResourceItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [trashedResources, setTrashedResources] = useState<ResourceItem[]>([]);
  const [trashedFolders, setTrashedFolders] = useState<Folder[]>([]);
  const [recycleFolderId, setRecycleFolderId] = useState<string | null>(null);
  const [recycleFolderItems, setRecycleFolderItems] = useState<ResourceItem[]>([]);
  const [pendingPermanentDelete, setPendingPermanentDelete] = useState<string | null>(null);
  const [pendingBatchPermanentDelete, setPendingBatchPermanentDelete] = useState<string[] | null>(null);
  const [pendingBatchPermanentDeleteFolders, setPendingBatchPermanentDeleteFolders] = useState<string[] | null>(null);
  const [downloadedResources, setDownloadedResources] = useState<ResourceItem[]>([]);
  const [allTags, setAllTags] = useState<Tag[]>([]);
  const [resourceTagNamesMap, setResourceTagNamesMap] = useState<Record<string, string>>({});
  const [resourceTagIdsMap, setResourceTagIdsMap] = useState<Record<string, Set<string>>>({});
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [smartFolders, setSmartFolders] = useState<SmartCollection[]>([]);
  const [folderChain, setFolderChain] = useState<Folder[]>([]);

  // ── Sidebar counts (for "My Downloads" / "My Uploads" menu badges) ──
  // Note: variable name `myResourcesCount` is retained to avoid churn; it
  // corresponds to the "My Uploads" entry in the UI.
  const [myResourcesCount, setMyResourcesCount] = useState<number | null>(null);
  const [downloadsCount, setDownloadsCount] = useState<number | null>(null);
  const [countsRefreshTick, setCountsRefreshTick] = useState(0);
  const refreshSidebarCounts = useCallback(() => {
    setCountsRefreshTick((v) => v + 1);
  }, []);

  // ── Selection state ──
  const [selectedResource, setSelectedResource] = useState<ResourceItem | null>(null);
  const [selectedFolder, setSelectedFolder] = useState<Folder | null>(null);
  const [selectedResourceTags, setSelectedResourceTags] = useState<Array<{ tag: Tag }>>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [lastClickedId, setLastClickedId] = useState<string | null>(null);
  const [multiSelectMode, setMultiSelectMode] = useState(false);

  // ── View / UI state ──
  const [viewMode, setViewMode] = useState<'grid' | 'list' | 'justified'>('grid');
  const [sortBy, setSortBy] = useState<SortBy>('newest');
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [showInfoPanel, setShowInfoPanel] = useState(true);
  const [infoPanelWidth, setInfoPanelWidth] = useState(320);

  // ── Server-side filter params (from the filter bar) ──
  // We JSON.stringify in the effect dep array, not this value directly,
  // so children can pass fresh objects per render without thrashing.
  const [filterParams, setFilterParamsState] =
    useState<ResourcesFilterParams>(EMPTY_FILTER_PARAMS);
  const setFilterParams = useCallback((p: ResourcesFilterParams) => {
    setFilterParamsState(p);
  }, []);
  // Stable key for the effect dep array — the object identity shifts
  // every render in practice (toFilterParams returns a fresh object).
  const filterParamsKey = useMemo(
    () => JSON.stringify(filterParams),
    [filterParams],
  );

  // Ref mirror so post-mutation reload helpers can see the latest params
  // without recreating on every filter tweak.
  const filterParamsRef = useRef(filterParams);
  filterParamsRef.current = filterParams;

  // ── Derived view flags ──
  const isResourcesView = sidebarView === 'resources';
  const isRecycleView = sidebarView === 'recycle';
  const isSharedView = sidebarView === 'shared';
  const isDownloadsView = sidebarView === 'downloads';
  const isTempView = sidebarView === 'temp';

  // ── Temp view state ──
  const [tempFolderId, setTempFolderId] = useState<string | null>(null);
  const [tempResources, setTempResources] = useState<ResourceItem[]>([]);
  // Bumped to force the temp-view list to re-fetch (e.g. after a temp resource
  // is promoted to permanent, so it drops out of the temp view).
  const [tempRefreshTick, setTempRefreshTick] = useState(0);
  const reloadTemp = useCallback(() => setTempRefreshTick((t) => t + 1), []);

  // ── Permission check ──
  const permObjectType = selectedLibraryId ? 'library' : selectedFolderId ? 'folder' : null;
  const permObjectId = selectedLibraryId ?? selectedFolderId ?? null;
  const { canDo } = usePermission(
    !isPersonal ? permObjectType : null,
    !isPersonal ? permObjectId : null,
    !isPersonal ? (teamId ?? null) : null,
  );

  const canUpload = isResourcesView && canDo('upload');

  // ── Toast ──
  const { addToast } = useToast();

  // ── Debounce search ──
  useEffect(() => {
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    searchTimerRef.current = setTimeout(() => {
      setDebouncedSearch(searchQuery);
    }, 300);
    return () => { if (searchTimerRef.current) clearTimeout(searchTimerRef.current); };
  }, [searchQuery]);

  // ── Load folders on scope change ──
  const loadFolders = useCallback(async () => {
    try {
      const allFolders = await fetchFolders(scopeId, isPersonal, selectedLibraryId);
      setFolders(allFolders);
    } catch (err) {
      console.error('Failed to load folders:', err);
      setFolders([]);
    }
  }, [isPersonal, scopeId, selectedLibraryId]);

  const loadChildFolders = useCallback(async () => {
    if (!isResourcesView) {
      setChildFolders([]);
      return;
    }
    try {
      const children = await fetchChildFolders(scopeId, isPersonal, selectedFolderId, selectedLibraryId);
      setChildFolders(children);
    } catch (err) {
      console.error('[ResourcesContext] loadChildFolders failed:', err);
      setChildFolders([]);
    }
  }, [isPersonal, scopeId, selectedFolderId, selectedLibraryId, isResourcesView]);

  /**
   * Re-fetch the current resource list honouring the active filter
   * params. Used by post-mutation handlers (move/copy/trash/rename) so
   * their explicit refresh respects server-side filtering instead of
   * silently bypassing it.
   */
  const reloadResources = useCallback(async () => {
    try {
      const items = await fetchResources({
        isPersonal,
        scopeId,
        folderId: selectedFolderId,
        libraryId: selectedLibraryId,
        ...filterParamsRef.current,
      });
      setResources(items);
      // Post-mutation refreshers (incl. the batch toolbar) call this; if the
      // user is in the Temp view its separate tempResources state must also
      // re-fetch, otherwise batch ops leave the Temp view stale. The temp
      // effect early-returns when not in the Temp view, so this is a no-op
      // elsewhere.
      setTempRefreshTick((t) => t + 1);
    } catch (err) {
      console.error('[ResourcesContext] reloadResources failed:', err);
    }
  }, [isPersonal, scopeId, selectedFolderId, selectedLibraryId]);

  // Keep a stable handle on reloadResources so the realtime subscription
  // below doesn't tear down + re-subscribe on every scope/filter change.
  const reloadResourcesRef = useRef(reloadResources);
  useEffect(() => {
    reloadResourcesRef.current = reloadResources;
  }, [reloadResources]);

  // ── Realtime: refresh the list when a new resource is created for the
  // current user, so a freshly-downloaded video shows up + becomes
  // clickable without a manual page refresh. Subscribes broadly to the
  // user's resources.creator_id and debounces reloads to avoid thrashing
  // when a download produces multiple INSERTs in quick succession (the
  // resource itself + resource_versions writes etc).
  useEffect(() => {
    if (!currentUserId) return;
    const supabase = getSupabaseClient();
    let pending: ReturnType<typeof setTimeout> | null = null;
    const scheduleReload = () => {
      if (pending) clearTimeout(pending);
      pending = setTimeout(() => {
        pending = null;
        reloadResourcesRef.current().catch(() => {});
      }, 400);
    };

    const channel = supabase
      .channel(`resources-realtime-${currentUserId}`)
      .on(
        'postgres_changes',
        {
          event: 'INSERT',
          schema: 'public',
          table: 'resources',
          filter: `creator_id=eq.${currentUserId}`,
        },
        () => scheduleReload(),
      )
      .on(
        'postgres_changes',
        {
          event: 'UPDATE',
          schema: 'public',
          table: 'resources',
          filter: `creator_id=eq.${currentUserId}`,
        },
        (payload) => {
          // Only reload on user-visible field changes (not every metadata
          // tick from background workflows). filename/notes/thumbnail
          // changes warrant a refresh; transcode_status etc. don't move
          // the card around so we ignore them here.
          const changed = (payload as Record<string, unknown>).new as Record<string, unknown> | undefined;
          if (!changed) return;
          if (
            changed.filename !== undefined ||
            changed.is_trashed !== undefined ||
            changed.folder_id !== undefined ||
            changed.thumbnail_path !== undefined
          ) {
            scheduleReload();
          }
        },
      )
      .subscribe();

    return () => {
      if (pending) clearTimeout(pending);
      supabase.removeChannel(channel);
    };
  }, [currentUserId]);

  // Initial data load on scope change
  useEffect(() => {
    setSelectedResource(null);
    setSmartFolders([]);
    loadFolders();
    fetchTags().then(setAllTags).catch(() => {});
    fetchSmartFolders(scopeId).then(setSmartFolders).catch(() => setSmartFolders([]));
    if (!isPersonal) {
      fetchLibraries(scopeId).then((libs) => {
        setLibraries(libs);
        if (libs.length > 0 && !urlLibraryId && !section) {
          navigate(resPath(`/resources/library/${libs[0].id}`), { replace: true });
        }
      }).catch(() => setLibraries([]));
    } else {
      setLibraries([]);
    }
  }, [loadFolders, isPersonal, scopeId]);

  // Sidebar counts — refetch on scope change and on explicit refresh.
  // Kept in its own effect so count queries don't block the primary list load.
  useEffect(() => {
    let cancelled = false;
    setMyResourcesCount(null);
    setDownloadsCount(null);
    Promise.all([
      fetchResourceCount(isPersonal, scopeId).catch((err) => {
        console.error('Failed to load resource count:', err);
        return 0;
      }),
      fetchDownloadedResourceCount(isPersonal, scopeId).catch((err) => {
        console.error('Failed to load download count:', err);
        return 0;
      }),
    ]).then(([resCount, dlCount]) => {
      if (cancelled) return;
      setMyResourcesCount(resCount);
      setDownloadsCount(dlCount);
    });
    return () => {
      cancelled = true;
    };
  }, [isPersonal, scopeId, countsRefreshTick]);

  // Load folder previews when child folders change
  useEffect(() => {
    if (childFolders.length === 0) {
      setFolderPreviews({});
      return;
    }
    const loadPreviews = async () => {
      const previews: Record<string, Array<{ resource_id: string | null; thumbnail_path: string | null; cover_image_path: string | null; mime_type: string | null }>> = {};
      await Promise.all(
        childFolders.map(async (f) => {
          try {
            previews[f.id] = await getFolderPreview(f.id);
          } catch (err) {
            console.debug('Folder preview fetch failed:', err);
            previews[f.id] = [];
          }
        })
      );
      setFolderPreviews(previews);
    };
    loadPreviews();
  }, [childFolders]);

  // Build folder breadcrumb chain
  useEffect(() => {
    if (!selectedFolderId) {
      setFolderChain([]);
      return;
    }

    const buildChainFromFolders = (allFolders: Folder[]): Folder[] | null => {
      const folder = allFolders.find((f) => f.id === selectedFolderId);
      if (!folder) return null;
      const chain: Folder[] = [];
      let current: Folder | undefined = folder;
      while (current) {
        chain.unshift(current);
        current = current.parent_id ? allFolders.find((f) => f.id === current!.parent_id) : undefined;
      }
      return chain;
    };

    const chain = buildChainFromFolders(folders);
    if (chain && chain.length > 0) {
      setFolderChain(chain);
      return;
    }

    let cancelled = false;
    const fetchChain = async () => {
      try {
        const { supabase } = await import('../supabaseClient');
        const result: Folder[] = [];
        let currentId: string | null = selectedFolderId;
        while (currentId) {
          const { data, error } = await supabase
            .from('folders')
            .select('*')
            .eq('id', currentId)
            .single();
          if (error || !data || cancelled) break;
          result.unshift(data);
          currentId = data.parent_id;
        }
        if (!cancelled) setFolderChain(result);
      } catch (err) {
        console.debug('Folder chain fetch failed:', err);
      }
    };
    fetchChain();
    return () => { cancelled = true; };
  }, [selectedFolderId, folders]);

  // Load resources + child folders together
  useEffect(() => {
    if (sidebarView !== 'resources') {
      setChildFolders([]);
      return;
    }
    let cancelled = false;
    setLoading(true);

    const loadAll = async () => {
      try {
        const [items, flds] = await Promise.all([
          selectedSmartFolderId
            ? fetchSmartFolderResults(selectedSmartFolderId, scopeId)
            : fetchResources({
                isPersonal,
                scopeId,
                folderId: selectedFolderId,
                libraryId: selectedLibraryId,
                ...filterParams,
              }),
          fetchChildFolders(scopeId, isPersonal, selectedFolderId, selectedLibraryId),
        ]);
        if (!cancelled) {
          setResources(items);
          setChildFolders(flds);
        }
      } catch (err) {
        console.error('[ResourcesContext] Failed to load resources/folders:', err);
        if (!cancelled) {
          setResources([]);
          setChildFolders([]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    loadAll();
    return () => { cancelled = true; };
    // filterParamsKey is a JSON fingerprint of filterParams — using it
    // directly in the dep array would trigger on every object re-create.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPersonal, scopeId, selectedFolderId, selectedSmartFolderId, selectedLibraryId, sidebarView, filterParamsKey]);

  // Bulk load tag names for search
  useEffect(() => {
    const allItems = [...resources, ...downloadedResources];
    const resourceIds = allItems
      .map((item) => item.resource?.id)
      .filter((id): id is string => !!id)
      .map(String);
    if (resourceIds.length === 0) {
      setResourceTagNamesMap({});
      setResourceTagIdsMap({});
      return;
    }
    const supabase = getSupabaseClient();
    if (!supabase) return;

    // Batch query to avoid URL length limits (Kong/nginx reject overly long GETs → 502)
    const CHUNK = 50;
    const chunks: string[][] = [];
    for (let i = 0; i < resourceIds.length; i += CHUNK) {
      chunks.push(resourceIds.slice(i, i + CHUNK));
    }
    Promise.all(
      chunks.map(ids =>
        supabase
          .from('resource_tags')
          .select('resource_id, tag_id, tag:tags(name)')
          .in('resource_id', ids)
      ),
    ).then((results) => {
      const namesMap: Record<string, string> = {};
      const idsMap: Record<string, Set<string>> = {};
      for (const { data, error } of results) {
        if (error || !data) continue;
        for (const row of data) {
          const rid = String(row.resource_id);
          const tag = row.tag as { name: string } | { name: string }[] | null;
          const tagName = Array.isArray(tag) ? tag[0]?.name : tag?.name;
          if (tagName) {
            namesMap[rid] = namesMap[rid] ? `${namesMap[rid]} ${tagName}` : tagName;
          }
          const tagId = row.tag_id ? String(row.tag_id) : null;
          if (tagId) {
            if (!idsMap[rid]) idsMap[rid] = new Set<string>();
            idsMap[rid].add(tagId);
          }
        }
      }
      setResourceTagNamesMap(namesMap);
      setResourceTagIdsMap(idsMap);
    });
  }, [resources, downloadedResources]);

  // Load trashed resources
  const loadTrashedResources = useCallback(async () => {
    try {
      const [items, flds] = await Promise.all([
        fetchTrashedResources(isPersonal, scopeId),
        fetchTrashedFolders(scopeId),
      ]);
      setTrashedResources(items);
      setTrashedFolders(flds);
    } catch (err) {
      console.error('Failed to load trashed items:', err);
      setTrashedResources([]);
      setTrashedFolders([]);
    }
  }, [isPersonal, scopeId]);

  useEffect(() => {
    if (sidebarView !== 'recycle') return;
    let cancelled = false;
    setRecycleFolderId(null);
    setLoading(true);
    Promise.all([
      fetchTrashedResources(isPersonal, scopeId),
      fetchTrashedFolders(scopeId),
    ])
      .then(([items, flds]) => {
        if (!cancelled) {
          setTrashedResources(items);
          setTrashedFolders(flds);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setTrashedResources([]);
          setTrashedFolders([]);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [sidebarView, isPersonal, scopeId]);

  // Load resource_items inside a trashed folder
  useEffect(() => {
    if (!recycleFolderId) {
      setRecycleFolderItems([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    fetchFolderContents(recycleFolderId, true)
      .then((items) => { if (!cancelled) setRecycleFolderItems(items); })
      .catch((err) => { console.error('Failed to load trashed folder contents', err); if (!cancelled) setRecycleFolderItems([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [recycleFolderId]);

  // Load downloaded resources
  const loadDownloadedResources = useCallback(async () => {
    try {
      const items = await fetchDownloadedResources(scopeId);
      setDownloadedResources(items);
    } catch (err) {
      console.error('Failed to load downloaded resources:', err);
      setDownloadedResources([]);
    }
  }, [scopeId]);

  useEffect(() => {
    if (sidebarView === 'downloads') {
      setSelectedIds(new Set());
      setLoading(true);
      loadDownloadedResources().finally(() => setLoading(false));
    }
  }, [sidebarView, loadDownloadedResources]);

  // Load temp folder resources
  useEffect(() => {
    if (!isTempView) return;
    let cancelled = false;
    setSelectedIds(new Set());
    setLoading(true);
    const loadTemp = async () => {
      try {
        // Find the folder named 'temp' in the current scope
        const allFolders = await fetchFolders(scopeId, isPersonal, selectedLibraryId);
        if (cancelled) return;
        const tempFolder = allFolders.find((f) => f.name === 'temp') ?? null;
        setTempFolderId(tempFolder ? String(tempFolder.id) : null);
        if (!tempFolder) {
          setTempResources([]);
          return;
        }
        const items = await fetchResources({
          isPersonal,
          scopeId,
          folderId: String(tempFolder.id),
        });
        if (!cancelled) setTempResources(items);
      } catch (err) {
        console.error('[ResourcesContext] Failed to load temp folder resources:', err);
        if (!cancelled) setTempResources([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    loadTemp();
    return () => { cancelled = true; };
  }, [isTempView, isPersonal, scopeId, selectedLibraryId, tempRefreshTick]);

  // Load tags when selected resource changes
  useEffect(() => {
    if (!selectedResource?.resource?.id) {
      setSelectedResourceTags([]);
      return;
    }
    setSelectedResourceTags([]);
    fetchResourceTags(selectedResource.resource.id)
      .then(setSelectedResourceTags)
      .catch(() => setSelectedResourceTags([]));
  }, [selectedResource?.resource?.id]);

  // Auto-exit multi-select when no items selected
  useEffect(() => {
    if (multiSelectMode && selectedIds.size === 0) {
      setMultiSelectMode(false);
    }
  }, [multiSelectMode, selectedIds.size]);

  // Clear search/filter on view/folder change
  useEffect(() => {
    setSearchQuery('');
    setDebouncedSearch('');
  }, [sidebarView, selectedFolderId, selectedSmartFolderId, selectedLibraryId]);

  // Clear selection on view/folder/library change
  useEffect(() => {
    setSelectedResource(null);
  }, [sidebarView, selectedFolderId, selectedSmartFolderId, selectedLibraryId]);

  // ESC to close panel / exit multi-select
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (multiSelectMode) {
          setMultiSelectMode(false);
          setSelectedIds(new Set());
        }
        setSelectedResource(null);
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [multiSelectMode]);

  // ── Actions / handlers ──

  const handleTrashResource = useCallback(async (resourceId: string) => {
    try {
      const rid = String(resourceId);
      const item = resources.find((r) => String(r.resource?.id) === rid);
      await trashResource(rid, scopeId, selectedFolderId);
      setResources((prev) => prev.filter((r) => String(r.resource?.id) !== rid));
      // The Temp sidebar view (#360) renders from a separate tempResources
      // state, so the optimistic removal above must mirror into it or the
      // trashed item lingers in the Temp view until a remount.
      setTempResources((prev) => prev.filter((r) => String(r.resource?.id) !== rid));
      if (String(selectedResource?.resource?.id) === rid) setSelectedResource(null);
      const filename = item?.resource?.filename || '';
      addToast(t('resources.trashedNotification', { name: filename }), 'success');
      refreshSidebarCounts();
    } catch (err) { console.error('Failed to trash resource:', err); }
  }, [selectedResource, scopeId, selectedFolderId, resources, addToast, t, refreshSidebarCounts]);

  const handleRestoreResource = useCallback(async (resourceId: string) => {
    try {
      const rid = String(resourceId);
      await restoreResource(rid);
      setTrashedResources((prev) => prev.filter((r) => String(r.resource?.id) !== rid));
      refreshSidebarCounts();
    } catch { /* ignore */ }
  }, [refreshSidebarCounts]);

  const handlePermanentDelete = useCallback((resourceId: string) => {
    setPendingPermanentDelete(resourceId);
  }, []);

  const confirmPermanentDelete = useCallback(async () => {
    const ids = pendingBatchPermanentDelete || (pendingPermanentDelete ? [pendingPermanentDelete] : []);
    const folderIds = pendingBatchPermanentDeleteFolders || [];
    if (ids.length === 0 && folderIds.length === 0) return;
    try {
      for (const id of ids) {
        await permanentDeleteResource(id);
      }
      for (const fid of folderIds) {
        await permanentDeleteFolder(fid);
      }
      if (folderIds.length > 0) {
        await loadTrashedResources();
      } else {
        setTrashedResources((prev) => prev.filter((r) => !ids.includes(String(r.resource?.id))));
      }
      setSelectedIds(new Set());
      addToast(t('resources.permanentDeleteSuccess'), 'success');
      refreshSidebarCounts();
    } catch (err) {
      console.error('Permanent delete failed:', err);
      addToast(t('resources.permanentDeleteFailed'), 'error');
    }
    setPendingPermanentDelete(null);
    setPendingBatchPermanentDelete(null);
    setPendingBatchPermanentDeleteFolders(null);
  }, [pendingPermanentDelete, pendingBatchPermanentDelete, pendingBatchPermanentDeleteFolders, loadTrashedResources, addToast, t, refreshSidebarCounts]);

  const handleAddTag = useCallback(async (tagId: string) => {
    if (!selectedResource?.resource?.id) return;
    try {
      await addResourceTag(selectedResource.resource.id, tagId);
      const updated = await fetchResourceTags(selectedResource.resource.id);
      setSelectedResourceTags(updated);
    } catch { /* ignore */ }
  }, [selectedResource]);

  const handleRemoveTag = useCallback(async (tagId: string) => {
    if (!selectedResource?.resource?.id) return;
    try {
      await removeResourceTag(selectedResource.resource.id, tagId);
      setSelectedResourceTags((prev) => prev.filter((t) => String(t.tag?.id) !== tagId));
    } catch { /* ignore */ }
  }, [selectedResource]);

  const handleCreateTag = useCallback(async (name: string, color: string): Promise<Tag | null> => {
    try {
      const tag = await createTag({ name, color, type: 'user' });
      setAllTags(prev => [...prev, tag]);
      return tag;
    } catch (err) {
      console.error('Failed to create tag:', err);
      return null;
    }
  }, []);

  const refreshTags = useCallback(async () => {
    try {
      setAllTags(await fetchTags());
    } catch {
      /* keep stale list on failure */
    }
  }, []);

  const handleResourceUpdate = useCallback(async (data: Partial<Resource>) => {
    if (!selectedResource?.resource?.id) return;
    const rid = String(selectedResource.resource.id);
    try {
      await updateResource(rid, data as Record<string, unknown>);
      setSelectedResource(prev => prev ? {
        ...prev,
        resource: { ...prev.resource, ...data } as Resource,
      } : null);
      setResources(prev => prev.map(item =>
        String(item.resource?.id) === rid
          ? { ...item, resource: { ...item.resource!, ...data } as Resource }
          : item
      ));
      // Mirror into the Temp view's separate state (#360) so edits/renames
      // made while in the Temp view reflect immediately there too.
      setTempResources(prev => prev.map(item =>
        String(item.resource?.id) === rid
          ? { ...item, resource: { ...item.resource!, ...data } as Resource }
          : item
      ));
    } catch (err) {
      console.error('Failed to update resource:', err);
    }
  }, [selectedResource]);

  // ── Computed data ──

  const transcodingResourceIds = useMemo(() => {
    const ids = new Set<string>();
    for (const task of allUnifiedTasks) {
      if (task.task_type === 'transcode' && (task.status === 'pending' || task.status === 'processing') && task.resource_id) {
        ids.add(task.resource_id);
      }
    }
    return ids;
  }, [allUnifiedTasks]);

  // ── Context value ──

  const value: ResourcesContextType = useMemo(() => ({
    isPersonal,
    scopeId,
    teamId,
    sidebarView,
    selectedFolderId,
    selectedSmartFolderId,
    selectedLibraryId,
    resPath,
    navigate,

    isResourcesView,
    isRecycleView,
    isSharedView,
    isDownloadsView,
    isTempView,
    canUpload,

    tempFolderId,
    tempResources,
    reloadTemp,

    resources,
    setResources,
    folders,
    childFolders,
    folderPreviews,
    trashedResources,
    trashedFolders,
    downloadedResources,
    libraries,
    setLibraries,
    smartFolders,
    setSmartFolders,
    allTags,
    setAllTags,
    refreshTags,
    myResourcesCount,
    downloadsCount,
    refreshSidebarCounts,
    resourceTagNamesMap,
    resourceTagIdsMap,
    loading,
    setLoading,
    folderChain,

    recycleFolderId,
    setRecycleFolderId,
    recycleFolderItems,
    pendingPermanentDelete,
    setPendingPermanentDelete,
    pendingBatchPermanentDelete,
    setPendingBatchPermanentDelete,
    pendingBatchPermanentDeleteFolders,
    setPendingBatchPermanentDeleteFolders,

    selectedResource,
    setSelectedResource,
    selectedFolder,
    setSelectedFolder,
    selectedResourceTags,
    setSelectedResourceTags,
    selectedIds,
    setSelectedIds,
    lastClickedId,
    setLastClickedId,
    multiSelectMode,
    setMultiSelectMode,

    viewMode,
    setViewMode,
    sortBy,
    setSortBy,
    searchQuery,
    setSearchQuery,
    debouncedSearch,
    setDebouncedSearch,
    showInfoPanel,
    setShowInfoPanel,
    infoPanelWidth,
    setInfoPanelWidth,

    filterParams,
    setFilterParams,
    reloadResources,

    loadFolders,
    loadChildFolders,
    loadTrashedResources,
    loadDownloadedResources,
    handleTrashResource,
    handleRestoreResource,
    handlePermanentDelete,
    confirmPermanentDelete,
    handleAddTag,
    handleRemoveTag,
    handleCreateTag,
    handleResourceUpdate,

    canDo,
    addToast,

    transcodingResourceIds,
  }), [
    isPersonal, scopeId, teamId, sidebarView, selectedFolderId, selectedSmartFolderId, selectedLibraryId, resPath, navigate,
    isResourcesView, isRecycleView, isSharedView, isDownloadsView, isTempView, canUpload,
    tempFolderId, tempResources, reloadTemp,
    resources, folders, childFolders, folderPreviews, trashedResources, trashedFolders, downloadedResources,
    libraries, smartFolders, allTags, refreshTags, myResourcesCount, downloadsCount, refreshSidebarCounts,
    resourceTagNamesMap, resourceTagIdsMap, loading, folderChain,
    recycleFolderId, recycleFolderItems, pendingPermanentDelete, pendingBatchPermanentDelete, pendingBatchPermanentDeleteFolders,
    selectedResource, selectedFolder, selectedResourceTags, selectedIds, lastClickedId, multiSelectMode,
    viewMode, sortBy, searchQuery, debouncedSearch, showInfoPanel, infoPanelWidth,
    filterParams, setFilterParams, reloadResources,
    loadFolders, loadChildFolders, loadTrashedResources, loadDownloadedResources,
    handleTrashResource, handleRestoreResource, handlePermanentDelete, confirmPermanentDelete,
    handleAddTag, handleRemoveTag, handleCreateTag, handleResourceUpdate,
    canDo, addToast, transcodingResourceIds,
  ]);

  return (
    <ResourcesContext.Provider value={value}>
      {children}
    </ResourcesContext.Provider>
  );
};

// ─── Hook ──────────────────────────────────────────────

export const useResourcesContext = (): ResourcesContextType => {
  const ctx = useContext(ResourcesContext);
  if (!ctx) {
    throw new Error('useResourcesContext must be used within a ResourcesProvider');
  }
  return ctx;
};
