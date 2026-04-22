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
} from '../services/resourceService';
import { fetchLibraries } from '../services/libraryService';
import { fetchAllTags as fetchTags } from '../services/unifiedTagService';
import { createTag } from '../services/unifiedTagService';
import { useTaskManager } from './TaskManagerContext';
import { usePermission } from '../hooks/usePermission';
import { useToast } from '../components/Toast';
import { getSupabaseClient } from '../supabaseClient';
import type { Resource } from '../types';

// ─── Types ─────────────────────────────────────────────

export type SidebarView = 'resources' | 'shared' | 'recycle' | 'downloads';
export type SortBy = 'newest' | 'oldest' | 'name-az' | 'name-za' | 'largest' | 'smallest';

export interface ResourcesContextType {
  // ── Scope / URL-derived state ──
  scopeType: 'personal' | 'team';
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
  canUpload: boolean;

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
  /** Total count of "My Resources" (non-web resources, across all folders) for the current scope.
   *  Used by the sidebar to render a count badge on the "My Resources" entry.
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
  viewMode: 'grid' | 'list';
  setViewMode: React.Dispatch<React.SetStateAction<'grid' | 'list'>>;
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
  scopeType: 'personal' | 'team';
  scopeId: string;
  children: React.ReactNode;
}

export const ResourcesProvider: React.FC<ResourcesProviderProps> = ({
  scopeType,
  scopeId,
  children,
}) => {
  const { t } = useTranslation();
  const { tasks: allUnifiedTasks } = useTaskManager();
  const { teamId, section, folderId: urlFolderId, smartFolderId: urlSmartFolderId, libraryId: urlLibraryId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const resPath = useCallback((path: string) => teamId ? `/team/${teamId}${path}` : path, [teamId]);

  // ── URL-driven state ──
  const sidebarView: SidebarView = urlFolderId || urlSmartFolderId || urlLibraryId
    ? 'resources'
    : (['shared', 'recycle', 'downloads'].includes(section || '') ? section as SidebarView : 'resources');
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

  // ── Sidebar counts (for "My Downloads" / "My Resources" menu badges) ──
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
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  const [sortBy, setSortBy] = useState<SortBy>('newest');
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [showInfoPanel, setShowInfoPanel] = useState(true);
  const [infoPanelWidth, setInfoPanelWidth] = useState(320);

  // ── Derived view flags ──
  const isResourcesView = sidebarView === 'resources';
  const isRecycleView = sidebarView === 'recycle';
  const isSharedView = sidebarView === 'shared';
  const isDownloadsView = sidebarView === 'downloads';

  // ── Permission check ──
  const permObjectType = selectedLibraryId ? 'library' : selectedFolderId ? 'folder' : null;
  const permObjectId = selectedLibraryId ?? selectedFolderId ?? null;
  const { canDo } = usePermission(
    scopeType === 'team' ? permObjectType : null,
    scopeType === 'team' ? permObjectId : null,
    scopeType === 'team' ? (teamId ?? null) : null,
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
      const allFolders = await fetchFolders(scopeType, scopeId, selectedLibraryId);
      setFolders(allFolders);
    } catch (err) {
      console.error('Failed to load folders:', err);
      setFolders([]);
    }
  }, [scopeType, scopeId, selectedLibraryId]);

  const loadChildFolders = useCallback(async () => {
    if (!isResourcesView) {
      setChildFolders([]);
      return;
    }
    try {
      const children = await fetchChildFolders(scopeType, scopeId, selectedFolderId, selectedLibraryId);
      setChildFolders(children);
    } catch (err) {
      console.error('[ResourcesContext] loadChildFolders failed:', err);
      setChildFolders([]);
    }
  }, [scopeType, scopeId, selectedFolderId, selectedLibraryId, isResourcesView]);

  // Initial data load on scope change
  useEffect(() => {
    setSelectedResource(null);
    setSmartFolders([]);
    loadFolders();
    fetchTags().then(setAllTags).catch(() => {});
    fetchSmartFolders(scopeType, scopeId).then(setSmartFolders).catch(() => setSmartFolders([]));
    if (scopeType === 'team') {
      fetchLibraries(scopeId).then((libs) => {
        setLibraries(libs);
        if (libs.length > 0 && !urlLibraryId && !section) {
          navigate(resPath(`/resources/library/${libs[0].id}`), { replace: true });
        }
      }).catch(() => setLibraries([]));
    } else {
      setLibraries([]);
    }
  }, [loadFolders, scopeType, scopeId]);

  // Sidebar counts — refetch on scope change and on explicit refresh.
  // Kept in its own effect so count queries don't block the primary list load.
  useEffect(() => {
    let cancelled = false;
    setMyResourcesCount(null);
    setDownloadsCount(null);
    Promise.all([
      fetchResourceCount(scopeType, scopeId).catch((err) => {
        console.error('Failed to load resource count:', err);
        return 0;
      }),
      fetchDownloadedResourceCount(scopeType, scopeId).catch((err) => {
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
  }, [scopeType, scopeId, countsRefreshTick]);

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
            ? fetchSmartFolderResults(selectedSmartFolderId, scopeType, scopeId)
            : fetchResources(scopeType, scopeId, selectedFolderId, selectedLibraryId),
          fetchChildFolders(scopeType, scopeId, selectedFolderId, selectedLibraryId),
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
  }, [scopeType, scopeId, selectedFolderId, selectedSmartFolderId, selectedLibraryId, sidebarView]);

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
        fetchTrashedResources(scopeType, scopeId),
        fetchTrashedFolders(scopeType, scopeId),
      ]);
      setTrashedResources(items);
      setTrashedFolders(flds);
    } catch (err) {
      console.error('Failed to load trashed items:', err);
      setTrashedResources([]);
      setTrashedFolders([]);
    }
  }, [scopeType, scopeId]);

  useEffect(() => {
    if (sidebarView !== 'recycle') return;
    let cancelled = false;
    setRecycleFolderId(null);
    setLoading(true);
    Promise.all([
      fetchTrashedResources(scopeType, scopeId),
      fetchTrashedFolders(scopeType, scopeId),
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
  }, [sidebarView, scopeType, scopeId]);

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
      const items = await fetchDownloadedResources(scopeType, scopeId);
      setDownloadedResources(items);
    } catch (err) {
      console.error('Failed to load downloaded resources:', err);
      setDownloadedResources([]);
    }
  }, [scopeType, scopeId]);

  useEffect(() => {
    if (sidebarView === 'downloads') {
      setSelectedIds(new Set());
      setLoading(true);
      loadDownloadedResources().finally(() => setLoading(false));
    }
  }, [sidebarView, loadDownloadedResources]);

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
      await trashResource(rid, scopeType, scopeId, selectedFolderId);
      setResources((prev) => prev.filter((r) => String(r.resource?.id) !== rid));
      if (String(selectedResource?.resource?.id) === rid) setSelectedResource(null);
      const filename = item?.resource?.filename || '';
      addToast(t('resources.trashedNotification', { name: filename }), 'success');
      refreshSidebarCounts();
    } catch (err) { console.error('Failed to trash resource:', err); }
  }, [selectedResource, scopeType, scopeId, selectedFolderId, resources, addToast, t, refreshSidebarCounts]);

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
    scopeType,
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
    canUpload,

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
    scopeType, scopeId, teamId, sidebarView, selectedFolderId, selectedSmartFolderId, selectedLibraryId, resPath, navigate,
    isResourcesView, isRecycleView, isSharedView, isDownloadsView, canUpload,
    resources, folders, childFolders, folderPreviews, trashedResources, trashedFolders, downloadedResources,
    libraries, smartFolders, allTags, myResourcesCount, downloadsCount, refreshSidebarCounts,
    resourceTagNamesMap, resourceTagIdsMap, loading, folderChain,
    recycleFolderId, recycleFolderItems, pendingPermanentDelete, pendingBatchPermanentDelete, pendingBatchPermanentDeleteFolders,
    selectedResource, selectedFolder, selectedResourceTags, selectedIds, lastClickedId, multiSelectMode,
    viewMode, sortBy, searchQuery, debouncedSearch, showInfoPanel, infoPanelWidth,
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
