import React, { createContext, useContext, useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useParams, useNavigate, useLocation, type NavigateFunction } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Folder, ResourceItem, Tag, SmartCollection, Library } from '../types';
import {
  fetchFolders,
  fetchChildFolders,
  fetchResourcesPaginated,
  trashResource,
  restoreResource,
  permanentDeleteResource,
  permanentDeleteFolder,
  fetchTrashedResourcesPaginated,
  fetchDownloadedResources,
  fetchDownloadedResourceCount,
  fetchResourceCount,
  fetchResourceTags,
  addResourceTag,
  removeResourceTag,
  fetchSmartFolders,
  fetchSmartFolderResultsPaginated,
  trashResources,
  getFolderPreview,
  updateResource,
  fetchTrashedFolders,
  restoreFolder,
  fetchFolderContents,
  type FetchResourcesParams,
} from '../services/resourceService';
import { fetchLibraries } from '../services/libraryService';
import { fetchGeneratedCounts } from '../services/generatedService';
import { fetchAssetCounts, type AssetCounts } from '../services/assetsService';
import { fetchPromptCounts } from '../services/promptsService';
import { ASSET_TYPES, type AssetType } from '../components/assets/assetSlots';
import { useKeysetPagination } from '../hooks/useKeysetPagination';
import type { KeysetCursor } from '../services/pagination';
import { fetchAllTags as fetchTags } from '../services/unifiedTagService';
import { createTag } from '../services/unifiedTagService';
import { useTaskManager } from './TaskManagerContext';
import { useAuth } from './AuthContext';
import { usePermission } from '../hooks/usePermission';
import { useToast } from '../components/Toast';
import { getSupabaseClient } from '../supabaseClient';
import { mergeAssignedTagsIntoAllTags } from '../utils/tagMerge';
import type { Resource } from '../types';
import {
  loadResourceSearchScope,
  saveResourceSearchScope,
} from '../components/resourceSearchScope';
import type { ResourceSearchField } from '../components/resourceSearchScope';

// ─── Types ─────────────────────────────────────────────

export type SidebarView = 'resources' | 'shared' | 'recycle' | 'downloads' | 'generated' | 'assets';
export type SortBy = 'newest' | 'oldest' | 'name-az' | 'name-za' | 'largest' | 'smallest';

/** Subset of fetchResources params that the filter bar contributes.
 *  Kept separate from the FetchResourcesParams type so consumers don't
 *  accidentally override scope / folder / library. */
export type ResourcesFilterParams = Omit<
  FetchResourcesParams,
  'isPersonal' | 'scopeId' | 'folderId' | 'libraryId'
>;

const EMPTY_FILTER_PARAMS: ResourcesFilterParams = {};

/** localStorage key for the resource grid's view mode preference. */
export const RESOURCES_VIEW_MODE_KEY = 'mediahub_resources_view_mode';

export interface ResourcesContextType {
  // ── Scope / URL-derived state ──
  isPersonal: boolean;
  scopeId: string;
  teamId: string | undefined;
  sidebarView: SidebarView;
  selectedFolderId: string | null;
  selectedSmartFolderId: string | null;
  selectedLibraryId: string | null;
  /** The asset type whose shelf is open (`resources/assets/:assetType`), or
   *  null on the Assets landing page. An UNKNOWN slug is null too — see
   *  `assetTypeParam` below, which is what the redirect branch reads. */
  selectedAssetType: AssetType | null;
  /** The raw `:assetType` URL segment, valid or not. The two differ only when
   *  the URL names a type that does not exist; a page that redirected on
   *  `selectedAssetType === null` alone would also bounce the landing page. */
  assetTypeParam: string | undefined;
  /** The asset whose detail page is open (`resources/assets/item/:assetId`). */
  selectedAssetId: string | null;
  resPath: (path: string) => string;
  navigate: NavigateFunction;

  // ── Derived view flags ──
  isResourcesView: boolean;
  isRecycleView: boolean;
  isSharedView: boolean;
  isDownloadsView: boolean;
  isGeneratedView: boolean;
  isAssetsView: boolean;
  canUpload: boolean;

  // ── Data state ──
  resources: ResourceItem[];
  setResources: React.Dispatch<React.SetStateAction<ResourceItem[]>>;
  /** Load the next keyset page of `resources` (no-op when drained/loading). */
  loadMoreResources: () => Promise<void>;
  hasMoreResources: boolean;
  isLoadingMoreResources: boolean;
  folders: Folder[];
  childFolders: Folder[];
  folderPreviews: Record<string, Array<{ resource_id: string | null; thumbnail_path: string | null; cover_image_path: string | null; mime_type: string | null }>>;
  trashedResources: ResourceItem[];
  /** Load the next keyset page of the recycle bin (no-op when drained/loading). */
  loadMoreTrashed: () => Promise<void>;
  hasMoreTrashed: boolean;
  isLoadingMoreTrashed: boolean;
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
  /** Unreviewed generations in the current scope — the Generated rail pill.
   *  `null` means "not known" (still loading, or the fetch failed): the pill
   *  is then not rendered at all, so a failed fetch never shows a fake 0. */
  generatedUnreviewedCount: number | null;
  /** Re-fetch `generatedUnreviewedCount` (e.g. after saving/discarding). */
  refreshGeneratedCounts: () => void;
  /** Per-type asset tallies for the six Assets sub-items. `null` means "not
   *  known" (still loading, or the fetch failed): the badges are then not
   *  rendered at all, so a failed fetch never shows six fake zeros. A type
   *  that really has none comes back as 0 from the server, which IS rendered
   *  as nothing — the distinction the null preserves is "we could not ask". */
  /** Partial: a count we do not have is ABSENT, never zero. The unified
   *  prompt count comes from a second request that can fail on its own. */
  assetCounts: Partial<AssetCounts> | null;
  /** Re-fetch `assetCounts` (e.g. after creating or deleting an asset). */
  refreshAssetCounts: () => void;
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
  /** "Show child files" toggle — flatten the current folder + all descendants
   *  into one flat file list (folders hidden). Persisted. */
  flattenFolders: boolean;
  setFlattenFolders: React.Dispatch<React.SetStateAction<boolean>>;
  /** True when folders should be hidden + files shown flat: the manual toggle
   *  OR an active search (search is always recursive + folderless). */
  flattenActive: boolean;
  sortBy: SortBy;
  setSortBy: React.Dispatch<React.SetStateAction<SortBy>>;
  searchQuery: string;
  setSearchQuery: React.Dispatch<React.SetStateAction<string>>;
  /** Which of name / notes / tags the keyword scans. Lives here rather than in
   *  the view because the server-side query needs it: `tags` can only be
   *  matched by the RPC, so the scope has to travel with the fetch, not just
   *  with the local filter. */
  searchScope: ResourceSearchField[];
  setSearchScope: (next: ResourceSearchField[]) => void;
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
  refetchSelectedResourceTags: () => Promise<void>;
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
  const {
    teamId,
    section,
    folderId: urlFolderId,
    smartFolderId: urlSmartFolderId,
    libraryId: urlLibraryId,
    assetType: urlAssetType,
    assetId: urlAssetId,
  } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const resPath = useCallback((path: string) => teamId ? `/team/${teamId}${path}` : path, [teamId]);

  // ── URL-driven state ──
  // `resources/assets/:assetType` and `resources/assets/item/:assetId` are
  // their own routes, so `section` is UNDEFINED on both — they are recognised
  // by their own params instead. Without this arm the two deep asset URLs
  // would render the file browser with the Assets rail entry inactive.
  const sidebarView: SidebarView = urlAssetType || urlAssetId
    ? 'assets'
    : urlFolderId || urlSmartFolderId || urlLibraryId
    ? 'resources'
    : (['shared', 'recycle', 'downloads', 'generated', 'assets'].includes(section || '') ? section as SidebarView : 'resources');
  const selectedFolderId = urlFolderId ?? null;
  const selectedSmartFolderId = urlSmartFolderId ?? null;
  const selectedLibraryId = urlLibraryId ?? null;
  const assetTypeParam = urlAssetType;
  // Validated against the slot table, not merely passed through: an unknown
  // slug would otherwise be sent to the backend as `?type=nonsense` and come
  // back 422, or worse be rendered as an empty shelf that looks like a real
  // (empty) one. The page redirects on `assetTypeParam && !selectedAssetType`.
  const selectedAssetType: AssetType | null =
    urlAssetType && (ASSET_TYPES as readonly string[]).includes(urlAssetType)
      ? (urlAssetType as AssetType)
      : null;
  const selectedAssetId = urlAssetId ?? null;


  // ── Data state ──
  const [folders, setFolders] = useState<Folder[]>([]);
  const [childFolders, setChildFolders] = useState<Folder[]>([]);
  const [folderPreviews, setFolderPreviews] = useState<Record<string, Array<{ resource_id: string | null; thumbnail_path: string | null; cover_image_path: string | null; mime_type: string | null }>>>({});
  // `resources` + `setResources` are provided by the keyset pagination hook
  // below (defined after filterParamsRef, its dependency). setResources keeps
  // the same Dispatch signature, so all optimistic-update call sites are unchanged.
  const [loading, setLoading] = useState(true);
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

  // ── Asset library counts (the six Assets sub-item badges) ──
  const [assetCounts, setAssetCounts] = useState<Partial<AssetCounts> | null>(null);
  const [assetCountsTick, setAssetCountsTick] = useState(0);
  const refreshAssetCounts = useCallback(() => {
    setAssetCountsTick((v) => v + 1);
  }, []);

  // ── Generated inbox count (the "Generated" rail pill) ──
  const [generatedUnreviewedCount, setGeneratedUnreviewedCount] = useState<number | null>(null);
  const [generatedRefreshTick, setGeneratedRefreshTick] = useState(0);
  const refreshGeneratedCounts = useCallback(() => {
    setGeneratedRefreshTick((v) => v + 1);
  }, []);

  // ── Selection state ──
  const [selectedResource, setSelectedResource] = useState<ResourceItem | null>(null);
  const [selectedFolder, setSelectedFolder] = useState<Folder | null>(null);
  const [selectedResourceTags, setSelectedResourceTags] = useState<Array<{ tag: Tag }>>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [lastClickedId, setLastClickedId] = useState<string | null>(null);
  const [multiSelectMode, setMultiSelectMode] = useState(false);

  // ── View / UI state ──
  // Default view is the Eagle-style adaptive ("justified") layout: thumbnails
  // keep their own proportions instead of being letterboxed onto a fixed tile.
  // Persisted like the flatten preference below so a deliberate switch to the
  // uniform grid or the list survives a reload.
  const [viewMode, setViewMode] = useState<'grid' | 'list' | 'justified'>(() => {
    try {
      const stored = localStorage.getItem(RESOURCES_VIEW_MODE_KEY);
      if (stored === 'grid' || stored === 'list' || stored === 'justified') return stored;
    } catch (err) {
      // Privacy mode / disabled storage — fall through to the default.
      console.error('Failed to read stored resources view mode:', err);
    }
    return 'justified';
  });
  useEffect(() => {
    try {
      localStorage.setItem(RESOURCES_VIEW_MODE_KEY, viewMode);
    } catch (err) {
      console.error('Failed to persist resources view mode:', err);
    }
  }, [viewMode]);
  // "Show child files" — flatten folders into a recursive flat file list.
  // Persisted so the preference survives reloads (mirrors useLibrary's pattern).
  const [flattenFolders, setFlattenFolders] = useState<boolean>(() => {
    try {
      return localStorage.getItem('mediahub_resources_flatten') === '1';
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem('mediahub_resources_flatten', flattenFolders ? '1' : '0');
    } catch {
      /* ignore quota / privacy-mode failures */
    }
  }, [flattenFolders]);
  const [sortBy, setSortBy] = useState<SortBy>('newest');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchScope, setSearchScopeState] = useState<ResourceSearchField[]>(
    () => loadResourceSearchScope(),
  );
  const setSearchScope = useCallback((next: ResourceSearchField[]) => {
    setSearchScopeState(next);
    saveResourceSearchScope(next);
  }, []);
  const searchScopeKey = searchScope.slice().sort().join(',');
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

  // Ref mirror of the loaded smart folders so fetchResourcesPage can read the
  // selected folder's rules without taking smartFolders as a callback dep.
  // The fetch is (re)triggered instead by `selectedSmartRulesKey` below, which
  // also covers the load race (smartFolders arrives after the folder is
  // selected) and live rule edits.
  const smartFoldersRef = useRef(smartFolders);
  smartFoldersRef.current = smartFolders;
  const selectedSmartRulesKey = useMemo(() => {
    if (!selectedSmartFolderId) return '';
    const f = smartFolders.find(
      (x) => String(x.id) === String(selectedSmartFolderId),
    );
    return f?.smart_rules ? JSON.stringify(f.smart_rules) : '';
  }, [selectedSmartFolderId, smartFolders]);

  // ── Keyset-paginated resource list (scale-safe). Replaces the old bulk
  //    fetchResources → setResources, which silently capped at PostgREST's
  //    1000-row ceiling once a scope exceeded 1000 items. Smart folders page
  //    the same way via the search_smart_folder RPC (mig 276). ──
  const RESOURCE_PAGE_SIZE =
    typeof window !== 'undefined' && window.innerWidth < 768 ? 20 : 40;
  // ── Flatten / recursive mode ──────────────────────────────────────────
  // Folders are hidden + files shown flat when the user enables the toggle OR
  // a search is active (search is always recursive + folderless). At root the
  // descendant set is "everything", expressed as an undefined id list (the
  // query then drops the folder constraint); inside a folder we resolve the
  // {current + all descendants} id set from the full `folders` tree by BFS.
  const flattenActive = flattenFolders || !!debouncedSearch.trim();
  const flattenFolderIds = useMemo<string[] | undefined>(() => {
    if (!flattenActive || !selectedFolderId) return undefined;
    const childrenByParent = new Map<string | null, string[]>();
    for (const f of folders) {
      const p = f.parent_id ? String(f.parent_id) : null;
      (childrenByParent.get(p) ?? childrenByParent.set(p, []).get(p)!).push(String(f.id));
    }
    const ids: string[] = [];
    const stack: string[] = [String(selectedFolderId)];
    while (stack.length) {
      const id = stack.pop()!;
      ids.push(id);
      for (const k of childrenByParent.get(id) ?? []) stack.push(k);
    }
    return ids;
  }, [flattenActive, selectedFolderId, folders]);
  // Stable fingerprint for the fetch dep arrays (array identity is unstable).
  const flattenKey = flattenActive ? (flattenFolderIds?.join(',') ?? 'root') : 'off';

  const fetchResourcesPage = useCallback(
    async (cursor: KeysetCursor | null, signal: AbortSignal) => {
      if (selectedSmartFolderId) {
        const folder = smartFoldersRef.current.find(
          (f) => String(f.id) === String(selectedSmartFolderId),
        );
        const rules = folder?.smart_rules;
        // No rules loaded yet, or a smart folder with zero conditions →
        // nothing to evaluate. selectedSmartRulesKey re-triggers this fetch
        // once the rules arrive.
        if (!rules || !rules.conditions || rules.conditions.length === 0) {
          return { data: [], hasMore: false, nextCursor: null, totalCount: 0 };
        }
        return fetchSmartFolderResultsPaginated(
          scopeId,
          rules,
          cursor,
          RESOURCE_PAGE_SIZE,
          signal,
        );
      }
      return fetchResourcesPaginated(
        {
          isPersonal,
          scopeId,
          folderId: selectedFolderId,
          libraryId: selectedLibraryId,
          flatten: flattenActive,
          flattenFolderIds,
          search: debouncedSearch.trim() || undefined,
          search_fields: searchScope,
          ...filterParamsRef.current,
        },
        cursor,
        RESOURCE_PAGE_SIZE,
        signal,
      );
    },
    // filterParamsKey is the JSON fingerprint read via filterParamsRef.current;
    // selectedSmartRulesKey re-triggers when the selected smart folder's rules
    // load or change (rules themselves are read via smartFoldersRef.current);
    // flattenKey re-triggers when the flatten toggle / search recursion changes;
    // debouncedSearch re-triggers the server-side keyword search on text change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [isPersonal, scopeId, selectedFolderId, selectedLibraryId, selectedSmartFolderId, selectedSmartRulesKey, filterParamsKey, flattenKey, debouncedSearch, searchScopeKey],
  );
  const {
    items: resources,
    setItems: setResources,
    isLoadingMore: isLoadingMoreResources,
    hasMore: hasMoreResources,
    load: loadResourcesFirstPage,
    loadMore: loadMoreResources,
  } = useKeysetPagination<ResourceItem>(fetchResourcesPage);

  // Recycle bin — keyset-paginated like the main list (was a drain-all that
  // capped at 1000). No filters/search: the recycle view has no filter bar.
  const RECYCLE_PAGE_SIZE = RESOURCE_PAGE_SIZE;
  const fetchTrashedPage = useCallback(
    (cursor: KeysetCursor | null, signal: AbortSignal) =>
      fetchTrashedResourcesPaginated(isPersonal, scopeId, cursor, RECYCLE_PAGE_SIZE, signal),
    [isPersonal, scopeId, RECYCLE_PAGE_SIZE],
  );
  const {
    items: trashedResources,
    setItems: setTrashedResources,
    isLoadingMore: isLoadingMoreTrashed,
    hasMore: hasMoreTrashed,
    load: loadTrashedFirstPage,
    loadMore: loadMoreTrashed,
  } = useKeysetPagination<ResourceItem>(fetchTrashedPage);

  // ── Derived view flags ──
  const isResourcesView = sidebarView === 'resources';
  const isRecycleView = sidebarView === 'recycle';
  const isSharedView = sidebarView === 'shared';
  const isDownloadsView = sidebarView === 'downloads';
  const isGeneratedView = sidebarView === 'generated';
  const isAssetsView = sidebarView === 'assets';

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
    // Reset to the first page. loadResourcesFirstPage owns the items state +
    // abort lifecycle and reads the current scope/filters via fetchResourcesPage.
    await loadResourcesFirstPage();
  // loadResourcesFirstPage has stable identity (keyset hook).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  // Generated inbox count — same scope id the folder/resource queries use
  // (`generated_media.scope_id` is a team id, and so is `scopeId`). A failure
  // leaves the count untouched and logs: on first load that means it stays
  // `null`, which renders NO pill — showing a 0 would claim "nothing to
  // review" on evidence we do not have. On a failed REFRESH the last known
  // number stays on screen rather than the pill vanishing; it may be stale,
  // and the console.error is the signal that it might be.
  //
  // Reset is deliberately split into its own effect keyed on `scopeId` ALONE.
  // Folding it into the fetch below would blank the pill on every
  // `refreshGeneratedCounts()` tick too, making the badge disappear and pop
  // back on each refresh. `null` means "we do not know", which is true when
  // the scope changes and false during a refresh.
  useEffect(() => {
    setGeneratedUnreviewedCount(null);
  }, [scopeId]);

  useEffect(() => {
    // No scope yet (first paint, before the workspace resolves) is not a
    // scope of zero: `?scope_id=` reaches the backend as a 422 and the only
    // trace is a console.error on every cold load. The reset effect above
    // already left the pill at `null`, which is the honest state.
    if (!scopeId) return;
    let cancelled = false;
    fetchGeneratedCounts(scopeId)
      .then((counts) => {
        if (!cancelled) setGeneratedUnreviewedCount(counts.unreviewed);
      })
      .catch((err) => {
        console.error('[ResourcesContext] generated counts failed:', err);
      });
    return () => {
      cancelled = true;
    };
  }, [scopeId, generatedRefreshTick]);

  // Asset counts — same shape of contract as the Generated pill above, for the
  // same reasons. Reset is its own effect keyed on `scopeId` ALONE so a
  // `refreshAssetCounts()` tick does not blank all six badges and pop them
  // back; on a failed REFRESH the last known numbers stay on screen (possibly
  // stale — the console.error is the signal) rather than the badges vanishing.
  useEffect(() => {
    setAssetCounts(null);
  }, [scopeId]);

  useEffect(() => {
    // Same guard, same reason as the Generated pill above.
    if (!scopeId) return;
    let cancelled = false;
    // Both counts at once: the badge needs the unified number and the other
    // five come from `/assets/counts`, so serializing them made the six badges
    // wait for two round trips.
    Promise.all([
      fetchAssetCounts(scopeId),
      fetchPromptCounts(scopeId).catch((err) => {
        console.error('[ResourcesContext] prompt counts failed:', err);
        return null;
      }),
    ])
      .then(([counts, prompts]) => {
        if (cancelled) return;
        // The Prompts tab lists the unified catalog (templates + prompted
        // pictures), so its badge must count THAT — `/assets/counts` only
        // knows the template rows. When the unified count fails we KEEP THE
        // LAST KNOWN one: substituting the template-row number would show a
        // different metric under the same label (0 while the page lists 13).
        // With nothing known yet the key stays absent, which the sidebar
        // already renders as no badge rather than as a zero.
        setAssetCounts((prev) => {
          const { prompt: _templatesOnly, ...rest } = counts;
          const unified = prompts ? prompts.mine : prev?.prompt;
          return unified === undefined ? rest : { ...rest, prompt: unified };
        });
      })
      .catch((err) => {
        console.error('[ResourcesContext] asset counts failed:', err);
      });
    return () => {
      cancelled = true;
    };
  }, [scopeId, assetCountsTick]);

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
        // Resources load through the keyset hook (loadResourcesFirstPage owns the
        // items state + its own abort lifecycle, and reads the current
        // scope/filters via fetchResourcesPage). Child folders stay bulk (small).
        const [, flds] = await Promise.all([
          loadResourcesFirstPage(),
          fetchChildFolders(scopeId, isPersonal, selectedFolderId, selectedLibraryId),
        ]);
        if (!cancelled) setChildFolders(flds);
      } catch (err) {
        console.error('[ResourcesContext] Failed to load resources/folders:', err);
        if (!cancelled) setChildFolders([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    loadAll();
    return () => { cancelled = true; };
    // filterParamsKey is a JSON fingerprint of filterParams — using it
    // directly in the dep array would trigger on every object re-create.
    // flattenKey re-loads when the flatten toggle / recursive search changes
    // the folder scope of the fetch; debouncedSearch re-loads for the
    // server-side keyword search as the query text changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPersonal, scopeId, selectedFolderId, selectedSmartFolderId, selectedLibraryId, sidebarView, filterParamsKey, flattenKey, debouncedSearch]);

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

  // Load trashed resources. Items go through the keyset hook (loadTrashedFirstPage
  // resets to page 1); folders stay a single fetch (a scope has few trashed folders).
  const loadTrashedResources = useCallback(async () => {
    try {
      const [, flds] = await Promise.all([
        loadTrashedFirstPage(),
        fetchTrashedFolders(scopeId),
      ]);
      setTrashedFolders(flds);
    } catch (err) {
      console.error('Failed to load trashed items:', err);
      setTrashedFolders([]);
    }
  }, [scopeId, loadTrashedFirstPage]);

  useEffect(() => {
    if (sidebarView !== 'recycle') return;
    let cancelled = false;
    setRecycleFolderId(null);
    setLoading(true);
    Promise.all([
      loadTrashedFirstPage(),
      fetchTrashedFolders(scopeId),
    ])
      .then(([, flds]) => {
        if (!cancelled) setTrashedFolders(flds);
      })
      .catch(() => {
        if (!cancelled) setTrashedFolders([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [sidebarView, isPersonal, scopeId, loadTrashedFirstPage]);

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

  // Entering the Generated inbox drops a multi-selection carried over from the
  // previous view — those `item:` ids point at files that are no longer on
  // screen, and the batch toolbar would still act on them. (The retired temp
  // effect did this on entry; deleting it took the behaviour with it.)
  // Keyed on the boolean, so it fires on ENTRY only: the Generated view has
  // its own multi-select, and a selection made while already inside it must
  // survive.
  useEffect(() => {
    if (!isGeneratedView) return;
    setSelectedIds(new Set());
  }, [isGeneratedView]);

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

  const refetchSelectedResourceTags = useCallback(async () => {
    if (!selectedResource?.resource?.id) return;
    try {
      const updated = await fetchResourceTags(selectedResource.resource.id);
      setSelectedResourceTags(updated);
      // The refetch may include a tag that was just created on-demand (e.g.
      // PromptSection's ensure-trigger-tag flow) and not yet be in this
      // panel's allTags snapshot — append it so the picker stays in sync
      // without a full refetch (R1, mirrors MediaCard.refetchResourceTags).
      setAllTags((prev) => mergeAssignedTagsIntoAllTags(prev, updated));
    } catch (err) {
      console.error('[ResourcesContext] refetchSelectedResourceTags failed:', err);
    }
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
    selectedAssetType,
    assetTypeParam,
    selectedAssetId,
    resPath,
    navigate,

    isResourcesView,
    isRecycleView,
    isSharedView,
    isDownloadsView,
    isGeneratedView,
    isAssetsView,
    canUpload,

    resources,
    setResources,
    loadMoreResources,
    hasMoreResources,
    isLoadingMoreResources,
    folders,
    childFolders,
    folderPreviews,
    trashedResources,
    loadMoreTrashed,
    hasMoreTrashed,
    isLoadingMoreTrashed,
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
    generatedUnreviewedCount,
    refreshGeneratedCounts,
    assetCounts,
    refreshAssetCounts,
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
    flattenFolders,
    setFlattenFolders,
    flattenActive,
    sortBy,
    setSortBy,
    searchQuery,
    setSearchQuery,
    searchScope,
    setSearchScope,
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
    refetchSelectedResourceTags,
    handleCreateTag,
    handleResourceUpdate,

    canDo,
    addToast,

    transcodingResourceIds,
  }), [
    isPersonal, scopeId, teamId, sidebarView, selectedFolderId, selectedSmartFolderId, selectedLibraryId, resPath, navigate,
    selectedAssetType, assetTypeParam, selectedAssetId,
    isResourcesView, isRecycleView, isSharedView, isDownloadsView, isGeneratedView, isAssetsView, canUpload,
    resources, folders, childFolders, folderPreviews, trashedResources, trashedFolders, downloadedResources,
    libraries, smartFolders, allTags, refreshTags, myResourcesCount, downloadsCount, refreshSidebarCounts,
    generatedUnreviewedCount, refreshGeneratedCounts, assetCounts, refreshAssetCounts,
    resourceTagNamesMap, resourceTagIdsMap, loading, folderChain,
    recycleFolderId, recycleFolderItems, pendingPermanentDelete, pendingBatchPermanentDelete, pendingBatchPermanentDeleteFolders,
    selectedResource, selectedFolder, selectedResourceTags, selectedIds, lastClickedId, multiSelectMode,
    viewMode, flattenFolders, flattenActive, sortBy, searchQuery, searchScope, setSearchScope, debouncedSearch, showInfoPanel, infoPanelWidth,
    filterParams, setFilterParams, reloadResources,
    loadMoreResources, hasMoreResources, isLoadingMoreResources,
    loadMoreTrashed, hasMoreTrashed, isLoadingMoreTrashed,
    loadFolders, loadChildFolders, loadTrashedResources, loadDownloadedResources,
    handleTrashResource, handleRestoreResource, handlePermanentDelete, confirmPermanentDelete,
    handleAddTag, handleRemoveTag, refetchSelectedResourceTags, handleCreateTag, handleResourceUpdate,
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
