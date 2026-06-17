import React, { useRef, useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import {
  Clock,
  Filter,
  FolderOpen,
  Loader2,
  Upload,
  Trash2,
  LayoutGrid,
  LayoutList,
  LayoutTemplate,
  FolderTree,
  ArrowUpDown,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Share2,
  FolderPlus,
  FolderSearch,
  Search,
  Check,
  X,
  UploadCloud,
  FileText,
  Table2,
  Presentation,
  Globe,
  Sparkles,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ToolbarSearch } from './ToolbarSearch';
import { ResourceCard } from './ResourceCard';
import { FolderCard } from './FolderCard';
import { Breadcrumb, BreadcrumbSegment } from './Breadcrumb';
import { Folder, ResourceItem, Tag } from '../types';
import { useResourcesContext } from '../contexts/ResourcesContext';
import type { SortBy } from '../contexts/ResourcesContext';
import { FilterBar } from './resources/filter/FilterBar';
import { FilterChipBar } from './filters/FilterChipBar';
import { FacetPickerSheet } from './filters/FacetPickerSheet';
import type { ChipId } from './resources/filter/types';
import type { UseFilterBarConfigReturn } from '../hooks/useFilterBarConfig';
import { useFilterBarVisibility } from '../hooks/useFilterBarVisibility';
import { ResourceFetchUrlModal } from './ResourceFetchUrlModal';
import { TempResourceActions } from './TempResourceActions';
import { ttlBadgeText } from '../utils/tempTtl';
import { tempTtlService } from '../services/tempTtlService';

// ─── Justified layout helper ────────────────────────────
// Derive a display aspect ratio (w/h) for a resource, clamped to a sane range.
// Used by the Eagle-style "justified" view to size thumbnails by their real
// proportions while keeping rows roughly equal-height.
function aspectRatioOf(resource?: { resolution?: string | null; mime_type?: string | null }): number {
  const res = resource?.resolution;
  if (res) {
    const m = res.match(/(\d+)\s*[x:×]\s*(\d+)/i);
    if (m) { const w = +m[1], h = +m[2]; if (w > 0 && h > 0) return Math.min(3, Math.max(0.4, w / h)); }
  }
  const mt = resource?.mime_type || '';
  if (mt.startsWith('video/')) return 16 / 9;
  return 1; // square fallback for images/audio/other without resolution
}

// ─── Skeleton components ────────────────────────────────

const SkeletonGrid: React.FC = () => (
  <div className="grid grid-cols-2 gap-4 downloads-grid">
    {Array.from({ length: 8 }).map((_, i) => (
      <div key={i} className="bg-ink-800/80 border border-ink-700/50 rounded-xl overflow-hidden animate-pulse">
        <div className="h-32 bg-ink-800" />
        <div className="p-3 space-y-2">
          <div className="h-4 bg-ink-700 rounded w-3/4" />
          <div className="h-3 bg-ink-700 rounded w-1/2" />
        </div>
      </div>
    ))}
  </div>
);

const SkeletonList: React.FC = () => (
  <div className="space-y-2">
    {Array.from({ length: 6 }).map((_, i) => (
      <div key={i} className="flex items-center gap-4 px-4 py-3 bg-ink-800/60 border border-ink-700/30 rounded-xl animate-pulse">
        <div className="w-10 h-10 bg-ink-700 rounded-lg" />
        <div className="flex-1 h-4 bg-ink-700 rounded w-1/3" />
        <div className="w-16 h-3 bg-ink-700 rounded" />
        <div className="w-20 h-3 bg-ink-700 rounded" />
      </div>
    ))}
  </div>
);

// ─── Props ──────────────────────────────────────────────

export interface ResourceGridProps {
  // Breadcrumb
  breadcrumbSegments: BreadcrumbSegment[];

  // Computed data
  filteredFolders: Folder[];
  sortedItems: ResourceItem[];
  recycleSubFolders: Folder[];
  trashedFolderPreviews: Record<string, Array<{ resource_id?: string | null; thumbnail_path?: string | null; cover_image_path?: string | null; mime_type?: string | null }>>;
  allSelectableIds: string[];

  // Filter bar state (Eagle-style chip toolbar)
  filterBarConfig: UseFilterBarConfigReturn;
  allTags: Tag[];
  /** Distinct platforms observed in the currently-loaded resource set,
   *  used to enrich the Source filter chip's options. Optional. */
  availablePlatforms?: string[];
  /** Optional per-scope chip allowlist forwarded to FilterBar. See
   *  FilterBarProps for semantics. Defaults to showing every chip. */
  allowedChips?: ReadonlyArray<ChipId>;

  // Sort state & callbacks
  sortOptions: Array<{ value: SortBy; label: string }>;
  currentSortLabel: string;

  // Search callbacks
  onQueryChange: (q: string) => void;
  onAISearch: (q: string, mode: 'hybrid' | 'semantic') => Promise<void>;
  onSearchClear: () => void;
  isAISearching: boolean;

  // Search-scope picker — when these are passed, the magnifier dropdown
  // shows the Eagle-style scope checkboxes (Name / Notes / Tags etc).
  searchScope?: string[];
  onSearchScopeChange?: (next: string[]) => void;
  scopeOptions?: import('./ToolbarSearch').ScopeOption[];

  // Upload
  uploading: boolean;
  overallProgress: number;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  folderInputRef: React.RefObject<HTMLInputElement | null>;

  // Drag-drop (file upload)
  canUploadDrop: boolean;
  dragOver: boolean;
  onDragEnter: (e: React.DragEvent) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;

  // Resource/folder interaction
  onResourceClick: (item: ResourceItem) => void;
  onResourceDoubleClick: (item: ResourceItem) => void;
  onFileContextMenu: (e: React.MouseEvent, item: ResourceItem) => void;
  onFolderContextMenu: (e: React.MouseEvent, folder: Folder) => void;
  onEmptyAreaContextMenu: (e: React.MouseEvent) => void;
  onCardClick: (compositeId: string, e?: React.MouseEvent) => void;
  onToggleSelect: (compositeId: string, e: React.MouseEvent) => void;
  onDropOnFolder: (targetFolderId: string | null, droppedIds: string[]) => void;

  // Rename
  renamingResourceId: string | null;
  renameValue: string;
  onRenameChange: (val: string) => void;
  onRenameResourceConfirm: () => void;
  onRenameResourceCancel: () => void;
  onStartRenameResource: (itemId: string, currentName: string) => void;
  renamingFolderId: string | null;
  renameFolderValue: string;
  onRenameFolderChange: (val: string) => void;
  onRenameFolderConfirm: () => void;
  onRenameFolderCancel: () => void;
  onStartRenameFolder: (folderId: string, currentName: string) => void;

  // New folder
  creatingFolder: boolean;
  newFolderName: string;
  savingFolder: boolean;
  newFolderInputRef: React.RefObject<HTMLInputElement | null>;
  onNewFolderNameChange: (val: string) => void;
  onCreateFolder: () => void;
  onCancelCreateFolder: () => void;
  onStartCreateFolder: () => void;

  // New / Smart folder
  onShowSmartFolderEditor: () => void;

  // Touch handlers
  getItemTouchHandlers: (type: 'file' | 'folder', item: any) => {
    onTouchStart: (e: React.TouchEvent) => void;
    onTouchMove: (e: React.TouchEvent) => void;
    onTouchEnd: () => void;
    onTouchCancel: () => void;
  };
  isTouchDropTarget: (folderId: string) => boolean;
  touchDragState: { isDragging: boolean };
  onEmptyAreaTouchStart: (e: React.TouchEvent) => void;
  onEmptyAreaTouchMove: (e: React.TouchEvent) => void;
  onEmptyAreaTouchEnd: () => void;
  onTouchDragMove: (e: React.TouchEvent) => void;
  onTouchDragEnd: (e: React.TouchEvent) => void;

  // Infinite-scroll pagination for the CURRENT view. Lifted to props (was read
  // from context) so each view (library / downloads / recycle / folder) can
  // drive its OWN keyset loadMore instead of sharing the main resources list's.
  loadMore: () => void;
  hasMore: boolean;
  isLoadingMore: boolean;
}

// ─── Component ──────────────────────────────────────────

export const ResourceGrid: React.FC<ResourceGridProps> = ({
  breadcrumbSegments,
  filteredFolders,
  sortedItems,
  recycleSubFolders,
  trashedFolderPreviews,
  allSelectableIds,
  filterBarConfig,
  allTags,
  availablePlatforms,
  allowedChips,
  sortOptions,
  currentSortLabel,
  onQueryChange,
  onAISearch,
  onSearchClear,
  isAISearching,
  searchScope,
  onSearchScopeChange,
  scopeOptions,
  uploading,
  overallProgress,
  fileInputRef,
  folderInputRef,
  canUploadDrop,
  dragOver,
  onDragEnter,
  onDragOver,
  onDragLeave,
  onDrop,
  onResourceClick,
  onResourceDoubleClick,
  onFileContextMenu,
  onFolderContextMenu,
  onEmptyAreaContextMenu,
  onCardClick,
  onToggleSelect,
  onDropOnFolder,
  renamingResourceId,
  renameValue,
  onRenameChange,
  onRenameResourceConfirm,
  onRenameResourceCancel,
  onStartRenameResource,
  renamingFolderId,
  renameFolderValue,
  onRenameFolderChange,
  onRenameFolderConfirm,
  onRenameFolderCancel,
  onStartRenameFolder,
  creatingFolder,
  newFolderName,
  savingFolder,
  newFolderInputRef,
  onNewFolderNameChange,
  onCreateFolder,
  onCancelCreateFolder,
  onStartCreateFolder,
  onShowSmartFolderEditor,
  getItemTouchHandlers,
  isTouchDropTarget,
  touchDragState,
  onEmptyAreaTouchStart,
  onEmptyAreaTouchMove,
  onEmptyAreaTouchEnd,
  onTouchDragMove,
  onTouchDragEnd,
  loadMore,
  hasMore,
  isLoadingMore,
}) => {
  const { t } = useTranslation();
  const ctx = useResourcesContext();
  const {
    isPersonal, scopeId, selectedFolderId, selectedLibraryId, selectedSmartFolderId,
    isResourcesView, isRecycleView, isSharedView, isTempView,
    loading, viewMode, setViewMode, flattenFolders, setFlattenFolders, sortBy, setSortBy,
    searchQuery, folderChain, folderPreviews,
    selectedResource, setSelectedResource,
    selectedFolder, setSelectedFolder,
    selectedIds, setSelectedIds,
    multiSelectMode, setMultiSelectMode,
    showInfoPanel, infoPanelWidth, setShowInfoPanel,
    recycleFolderId, setRecycleFolderId,
    navigate, resPath, canUpload, addToast,
    transcodingResourceIds,
    handleTrashResource: handleTrash,
    handleRestoreResource: handleRestore,
    handlePermanentDelete,
    reloadResources,
    reloadTemp,
  } = ctx;

  // PR-E: temp_ttl service + child panels still speak the legacy
  // 'personal' | 'team' scope_type (UUID-keyed user_settings exception).
  // Derive it locally from the isPersonal discriminator.
  const scopeType: 'personal' | 'team' = isPersonal ? 'personal' : 'team';

  // Mobile detection (matches Tailwind md: breakpoint at 768px)
  const isMobileDevice = typeof window !== 'undefined' && window.innerWidth < 768;

  // ─── Hide 'temp' from the top-level My Uploads FOLDERS grid ───────
  // Only filter at the root level (no folder selected) — navigating INTO
  // the temp folder explicitly must still work via the sidebar "Temp" item.
  const visibleFolders = isResourcesView && !selectedFolderId && !selectedSmartFolderId && !selectedLibraryId
    ? filteredFolders.filter((f) => f.name !== 'temp')
    : filteredFolders;

  // ─── Temp: TTL badge + Save actions ────────────
  // Two ways to be "in temp": navigating into the temp folder (legacy), or the
  // dedicated Temp sidebar view (isTempView, no selectedFolder). Both need the
  // TTL badge + promote-to-permanent action — gating on inTempFolder alone
  // regressed the Temp sidebar view (#360).
  const inTempFolder = selectedFolder?.name === 'temp';
  const isTempContext = inTempFolder || isTempView;
  const [scopeTtl, setScopeTtl] = useState<number | null>(null);
  useEffect(() => {
    if (!isTempContext || !scopeType || !scopeId) return;
    let cancelled = false;
    tempTtlService.getChatTempTtl(scopeType, scopeId).then((r) => {
      if (cancelled) return;
      setScopeTtl(r.ttl_days === -1 ? null : r.ttl_days);
    }).catch((err) => {
      console.warn('[ChatTtl] fetch failed:', err);
      /* badge falls back to '' on error */
    });
    return () => { cancelled = true; };
  }, [isTempContext, scopeType, scopeId]);

  // Filter bar visibility — search-row toggle remembers the choice in
  // localStorage. Hidden in shared / recycle views where filters don't
  // apply anyway.
  const { visible: isFilterBarVisible, toggle: toggleFilterBar } = useFilterBarVisibility();
  const filterBarEligible = !isRecycleView && !isSharedView;
  const shouldRenderFilterBar = filterBarEligible && isFilterBarVisible;
  const [openFacet, setOpenFacet] = useState<ChipId | null>(null);

  // Mobile search state
  const [isMobileSearchOpen, setIsMobileSearchOpen] = useState(false);

  // Upload dropdown
  const [showUploadDropdown, setShowUploadDropdown] = useState(false);
  const uploadDropdownRef = useRef<HTMLDivElement>(null);

  // New dropdown
  const [showNewDropdown, setShowNewDropdown] = useState(false);
  // Bug F (issue #194) — replaces the placeholder "即将推出" toast
  // on the 网页地址 menu item with a real URL submission modal that
  // hits POST /api/v1/media/fetch.
  const [showFetchUrlModal, setShowFetchUrlModal] = useState(false);
  const newDropdownRef = useRef<HTMLDivElement>(null);

  // ── Infinite scroll for the keyset-paginated Resources library ──
  // The useKeysetPagination hook (unlike useLibrary) has no built-in
  // IntersectionObserver, so this view drives loadMore. An observer (not bare
  // scroll events) handles BOTH the initial-fill case — page 1 is short and the
  // sentinel is already on-screen — and ongoing scroll. Re-running on
  // isLoadingMoreResources flipping false keeps auto-filling until the viewport
  // is covered or the scope is drained. The hook's own re-entrancy guard +
  // isLoadingMoreResources gate prevent double-fire.
  const loadMoreRef = useRef<HTMLDivElement>(null);
  const contentScrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const sentinel = loadMoreRef.current;
    if (!sentinel) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (
          entries[0]?.isIntersecting &&
          hasMore &&
          !isLoadingMore &&
          searchQuery.trim().length === 0
        ) {
          loadMore();
        }
      },
      { root: contentScrollRef.current ?? null, rootMargin: '600px' },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMore, isLoadingMore, searchQuery, loadMore]);

  // Sort panel
  const [showSortMenu, setShowSortMenu] = useState(false);

  // Click outside to close upload dropdown
  useEffect(() => {
    if (!showUploadDropdown) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (uploadDropdownRef.current && !uploadDropdownRef.current.contains(e.target as Node)) {
        setShowUploadDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showUploadDropdown]);

  // Click outside to close new dropdown
  useEffect(() => {
    if (!showNewDropdown) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (newDropdownRef.current && !newDropdownRef.current.contains(e.target as Node)) {
        setShowNewDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showNewDropdown]);

  return (
    <div className="flex-1 min-w-0 flex flex-col md:h-full md:min-h-0">
      {/* Mobile filter chip bar (Pixcall-style) — desktop keeps the FilterBar */}
      {filterBarEligible && (
        <FilterChipBar
          config={filterBarConfig}
          allTags={allTags}
          availablePlatforms={availablePlatforms}
          onOpenFacet={setOpenFacet}
        />
      )}
      <FacetPickerSheet
        open={openFacet !== null}
        facetId={openFacet}
        onClose={() => setOpenFacet(null)}
        config={filterBarConfig}
        allTags={allTags}
        availablePlatforms={availablePlatforms}
      />

      {/* Mobile breadcrumb navigation — hidden at root, shown inside folders */}
      {(selectedFolderId || isRecycleView) && (
        <div className="md:hidden px-3 py-2.5 min-h-[40px] flex items-center gap-2">
          <button
            onClick={() => {
              if (isRecycleView) {
                if (recycleFolderId) {
                  setRecycleFolderId(null);
                } else {
                  navigate(resPath('/resources'));
                }
              } else if (folderChain.length > 1) {
                const parentId = folderChain[folderChain.length - 2]?.id;
                navigate(resPath(parentId ? `/resources/folder/${parentId}` : '/resources'));
              } else {
                navigate(resPath('/resources'));
              }
            }}
            className="p-2.5 -ml-2 text-ink-400 hover:text-ink-200 active:bg-ink-700/50 rounded-lg"
          >
            <ChevronLeft size={22} />
          </button>
          <div className="flex-1 min-w-0">
            <Breadcrumb segments={breadcrumbSegments} />
          </div>
        </div>
      )}

      {/* Mobile search overlay — floating pill top-right (matches DownloadsView) */}
      {!isRecycleView && !isSharedView && createPortal(
        <div className="md:hidden fixed top-[calc(env(safe-area-inset-top,0px)+10px)] right-3 z-40 flex justify-end items-start pointer-events-none">
          <div className="pointer-events-auto flex items-center justify-end">
            {isMobileSearchOpen ? (
              <div className="flex items-center bg-black/50 backdrop-blur-md rounded-full px-4 py-2.5 w-[calc(100vw-80px)] max-w-sm animate-in slide-in-from-right-10 duration-200 border border-white/10 shadow-lg">
                <Search size={16} className="text-ink-300 mr-2 flex-shrink-0" />
                <input
                  autoFocus
                  type="text"
                  value={searchQuery}
                  onChange={(e) => onQueryChange(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && searchQuery.trim()) {
                      onAISearch(searchQuery, 'hybrid');
                    }
                    if (e.key === 'Escape') {
                      setIsMobileSearchOpen(false);
                      onSearchClear();
                    }
                  }}
                  placeholder={t('resources.searchFiles')}
                  className="bg-transparent border-none outline-none text-white text-sm w-full placeholder-ink-400"
                />
                <button
                  onClick={() => {
                    setIsMobileSearchOpen(false);
                    onSearchClear();
                  }}
                  className="ml-2 text-ink-400 hover:text-white"
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

      {/* Toolbar -- desktop only */}
      <div
        className="hidden md:block px-3 md:px-6 pt-3 pb-2 border-b border-ink-800/80 space-y-2"
        style={{ paddingRight: (selectedResource?.resource || selectedFolder) && showInfoPanel ? `${infoPanelWidth + 24}px` : undefined }}
      >
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            {breadcrumbSegments.length <= 1 ? (
              <div>
                <h2 className="text-lg font-semibold text-ink-100">{breadcrumbSegments[0]?.label}</h2>
              </div>
            ) : (
              <Breadcrumb segments={breadcrumbSegments} />
            )}
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {/* Search box */}
            <ToolbarSearch
              onQueryChange={onQueryChange}
              onAISearch={onAISearch}
              onClear={onSearchClear}
              isSearching={isAISearching}
              placeholder={t('resources.searchFiles')}
              className="w-48"
              searchScope={searchScope}
              onSearchScopeChange={onSearchScopeChange}
              scopeOptions={scopeOptions}
            />

            {/* Filter bar visibility toggle — plain funnel. Shows / hides
                the chip row below. Only visible when the bar itself is
                eligible (hidden in shared / recycle). */}
            {filterBarEligible && (
              <button
                type="button"
                onClick={toggleFilterBar}
                className={`p-1.5 rounded-lg transition-colors ${
                  isFilterBarVisible
                    ? 'text-indigo-400 bg-indigo-500/10 hover:bg-indigo-500/20'
                    : 'text-ink-400 hover:text-ink-200 hover:bg-ink-800'
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
            )}

            {/* Sort dropdown */}
            <div className="relative">
              <button
                onClick={() => setShowSortMenu(!showSortMenu)}
                className={`p-1.5 rounded-lg transition-colors ${
                  sortBy !== 'newest'
                    ? 'text-indigo-400 bg-indigo-500/10 hover:bg-indigo-500/20'
                    : 'text-ink-400 hover:text-ink-200 hover:bg-ink-800'
                }`}
                title={currentSortLabel}
              >
                <ArrowUpDown size={14} />
              </button>
              {showSortMenu && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setShowSortMenu(false)} />
                  <div className="absolute right-0 top-full mt-1.5 z-20 bg-ink-900/95 backdrop-blur-sm border border-ink-700/80 rounded-xl shadow-2xl py-1.5 w-44 animate-dropdown">
                    {sortOptions.map((opt) => (
                      <button
                        key={opt.value}
                        onClick={() => { setSortBy(opt.value); setShowSortMenu(false); }}
                        className={`w-full text-left px-3 py-2 text-xs transition-colors flex items-center justify-between ${
                          sortBy === opt.value
                            ? 'bg-indigo-500/10 text-indigo-400'
                            : 'text-ink-400 hover:bg-ink-800 hover:text-ink-200'
                        }`}
                      >
                        <span>{opt.label}</span>
                        {sortBy === opt.value && (
                          <Check size={12} className="text-indigo-400" />
                        )}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>

            {/* View toggle — cycles grid → justified → list → grid */}
            <button
              onClick={() =>
                setViewMode(
                  viewMode === 'grid' ? 'justified' : viewMode === 'justified' ? 'list' : 'grid',
                )
              }
              className="p-1.5 rounded-lg text-ink-400 hover:text-ink-200 hover:bg-ink-800 transition-colors"
              title={
                viewMode === 'grid'
                  ? t('resources.gridView')
                  : viewMode === 'justified'
                  ? t('resources.justifiedView')
                  : t('resources.listView')
              }
            >
              {viewMode === 'grid' ? (
                <LayoutGrid size={14} />
              ) : viewMode === 'justified' ? (
                <LayoutTemplate size={14} />
              ) : (
                <LayoutList size={14} />
              )}
            </button>

            {/* Flatten toggle — "show child files": list every file from the
                current folder + all descendants, hiding folder cards. */}
            {isResourcesView && (
              <button
                onClick={() => setFlattenFolders((v) => !v)}
                className={`p-1.5 rounded-lg transition-colors ${
                  flattenFolders
                    ? 'text-indigo-400 bg-indigo-500/10'
                    : 'text-ink-400 hover:text-ink-200 hover:bg-ink-800'
                }`}
                title={flattenFolders ? t('resources.showFolders') : t('resources.flattenFolders')}
                aria-pressed={flattenFolders}
              >
                <FolderTree size={14} />
              </button>
            )}

            {/* Upload button */}
            {canUpload && (
              <>
                {uploading ? (
                  <button
                    disabled
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg btn-tint-indigo btn-cta opacity-70"
                  >
                    <Loader2 size={14} className="animate-spin" />
                    <span>{overallProgress}%</span>
                  </button>
                ) : (
                  <div className="relative" ref={uploadDropdownRef}>
                    <div className="flex">
                      <button
                        onClick={() => fileInputRef.current?.click()}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-l-lg btn-tint-indigo btn-cta border-r-0 transition-colors"
                      >
                        <Upload size={14} />
                        <span>{t('resources.upload')}</span>
                      </button>
                      <button
                        onClick={() => setShowUploadDropdown(prev => !prev)}
                        className="px-1.5 py-1.5 text-xs font-medium rounded-r-lg btn-tint-indigo btn-cta transition-colors"
                      >
                        <ChevronDown size={12} />
                      </button>
                    </div>
                    {showUploadDropdown && (
                      <div className="absolute right-0 top-full mt-1 z-20 bg-ink-900 border border-ink-700 rounded-lg shadow-xl py-1 w-40">
                        <button
                          onClick={() => { fileInputRef.current?.click(); setShowUploadDropdown(false); }}
                          className="w-full text-left px-3 py-1.5 text-xs text-ink-400 hover:bg-ink-800 hover:text-ink-200 transition-colors flex items-center gap-2"
                        >
                          <Upload size={12} />
                          {t('resources.uploadFile')}
                        </button>
                        <button
                          onClick={() => { folderInputRef.current?.click(); setShowUploadDropdown(false); }}
                          className="w-full text-left px-3 py-1.5 text-xs text-ink-400 hover:bg-ink-800 hover:text-ink-200 transition-colors flex items-center gap-2"
                        >
                          <FolderOpen size={12} />
                          {t('resources.uploadFolder')}
                        </button>
                      </div>
                    )}
                  </div>
                )}

                {/* New dropdown button */}
                <div className="relative" ref={newDropdownRef}>
                  <button
                    onClick={() => setShowNewDropdown(prev => !prev)}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg btn-tint-amber transition-colors"
                  >
                    <Sparkles size={14} />
                    <span>{t('resources.new')}</span>
                    <ChevronDown size={12} />
                  </button>
                  {showNewDropdown && (
                    <div className="absolute right-0 top-full mt-1 z-20 bg-ink-900 border border-ink-700 rounded-lg shadow-xl py-1 w-48">
                      {/* Group 1 -- Containers */}
                      <button
                        onClick={() => { onStartCreateFolder(); setShowNewDropdown(false); }}
                        className="w-full text-left px-3 py-1.5 text-xs text-ink-300 hover:bg-ink-800 hover:text-ink-50 transition-colors flex items-center gap-2"
                      >
                        <FolderPlus size={14} className="text-amber-400" />
                        {t('resources.newFolder')}
                      </button>
                      <button
                        onClick={() => { addToast(t('resources.comingSoon'), 'info'); setShowNewDropdown(false); }}
                        className="w-full text-left px-3 py-1.5 text-xs text-ink-300 hover:bg-ink-800 hover:text-ink-50 transition-colors flex items-center gap-2"
                      >
                        <LayoutGrid size={14} className="text-blue-400" />
                        {t('resources.newProject')}
                      </button>
                      <button
                        onClick={() => { onShowSmartFolderEditor(); setShowNewDropdown(false); }}
                        className="w-full text-left px-3 py-1.5 text-xs text-ink-300 hover:bg-ink-800 hover:text-ink-50 transition-colors flex items-center gap-2"
                      >
                        <FolderSearch size={14} className="text-purple-400" />
                        {t('resources.newSmartFolder')}
                      </button>
                      {/* Divider */}
                      <div className="border-t border-ink-800 my-1" />
                      {/* Group 2 -- Documents */}
                      <button
                        onClick={() => { addToast(t('resources.comingSoon'), 'info'); setShowNewDropdown(false); }}
                        className="w-full text-left px-3 py-1.5 text-xs text-ink-300 hover:bg-ink-800 hover:text-ink-50 transition-colors flex items-center gap-2"
                      >
                        <FileText size={14} className="text-emerald-400" />
                        {t('resources.newDocument')}
                      </button>
                      <button
                        onClick={() => { addToast(t('resources.comingSoon'), 'info'); setShowNewDropdown(false); }}
                        className="w-full text-left px-3 py-1.5 text-xs text-ink-300 hover:bg-ink-800 hover:text-ink-50 transition-colors flex items-center gap-2"
                      >
                        <Table2 size={14} className="text-cyan-400" />
                        {t('resources.newSpreadsheet')}
                      </button>
                      <button
                        onClick={() => { addToast(t('resources.comingSoon'), 'info'); setShowNewDropdown(false); }}
                        className="w-full text-left px-3 py-1.5 text-xs text-ink-300 hover:bg-ink-800 hover:text-ink-50 transition-colors flex items-center gap-2"
                      >
                        <Presentation size={14} className="text-orange-400" />
                        {t('resources.newPresentation')}
                      </button>
                      {/* Divider */}
                      <div className="border-t border-ink-800 my-1" />
                      {/* Group 3 -- Other */}
                      <button
                        onClick={() => {
                          setShowFetchUrlModal(true);
                          setShowNewDropdown(false);
                        }}
                        className="w-full text-left px-3 py-1.5 text-xs text-ink-300 hover:bg-ink-800 hover:text-ink-50 transition-colors flex items-center gap-2"
                      >
                        <Globe size={14} className="text-indigo-400" />
                        {t('resources.newWebUrl')}
                      </button>
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </div>

        {/* Pinnable filter bar — hidden in shared/recycle views where
            filters don't apply, and togglable via the funnel icon in
            the search row. */}
        {shouldRenderFilterBar && (
          <FilterBar
            config={filterBarConfig}
            allTags={allTags}
            availablePlatforms={availablePlatforms}
            allowedChips={allowedChips}
          />
        )}
      </div>

      {/* Multi-select mode toolbar */}
      {multiSelectMode && (
        <div className="px-6 py-2 border-b border-ink-800/80 bg-ink-900/80 flex items-center gap-3">
          <button
            onClick={() => setSelectedIds(new Set(allSelectableIds))}
            className="px-3 py-1 text-xs font-medium text-ink-300 bg-ink-800 hover:bg-ink-700 rounded-lg transition-colors"
          >
            {t('resources.selectAll')}
          </button>
          <button
            onClick={() => { setSelectedIds(new Set()); setMultiSelectMode(false); setSelectedResource(null); }}
            className="px-3 py-1 text-xs font-medium text-ink-400 bg-ink-800 hover:bg-ink-700 rounded-lg transition-colors"
          >
            {t('common.cancel')}
          </button>
          <span className="text-xs text-ink-500">
            {t('resources.multiSelectCount', {
              total: visibleFolders.length + sortedItems.length,
              selected: selectedIds.size,
            })}
          </span>
        </div>
      )}

      {/* Content area */}
      <div
        ref={contentScrollRef}
        className="flex-1 md:min-h-0 overflow-y-auto p-3 md:p-6 relative lib-scroll"
        style={{ paddingRight: (selectedResource?.resource || selectedFolder) && showInfoPanel ? `${infoPanelWidth + 24}px` : undefined }}
        onDragEnter={canUploadDrop ? onDragEnter : undefined}
        onDragOver={canUploadDrop ? onDragOver : undefined}
        onDragLeave={canUploadDrop ? onDragLeave : undefined}
        onDrop={canUploadDrop ? onDrop : undefined}
        onContextMenu={isResourcesView ? onEmptyAreaContextMenu : undefined}
        onTouchStart={onEmptyAreaTouchStart}
        onTouchMove={touchDragState.isDragging ? onTouchDragMove : onEmptyAreaTouchMove}
        onTouchEnd={touchDragState.isDragging ? onTouchDragEnd : onEmptyAreaTouchEnd}
        onClick={(e) => {
          const target = e.target as HTMLElement;
          if (!target.closest('[data-context-item]')) {
            if (selectedIds.size > 0 || multiSelectMode) {
              setSelectedIds(new Set());
              setMultiSelectMode(false);
            }
            setSelectedResource(null);
            setSelectedFolder(null);
          }
        }}
      >
        {/* Drag-and-drop overlay */}
        {dragOver && canUploadDrop && (
          <div className="absolute inset-0 z-30 flex flex-col items-center justify-center bg-ink-950/80 backdrop-blur-sm border-2 border-dashed border-indigo-500 rounded-xl m-2 pointer-events-none">
            <UploadCloud size={56} className="text-indigo-400 mb-4 animate-bounce" />
            <p className="text-lg font-medium text-indigo-300">{t('resources.dropToUpload')}</p>
            <p className="text-sm text-ink-400 mt-1">{t('resources.dropToUploadHint')}</p>
          </div>
        )}

        {/* Recycle bin auto-cleanup notice */}
        {isRecycleView && (
          <div className="mb-4 flex items-center gap-2 px-3 py-2.5 bg-ink-800/50 border border-ink-700/50 rounded-lg text-xs text-ink-400">
            <Clock size={14} className="shrink-0 text-ink-500" />
            <span>{t('resources.recycleBinAutoCleanup')}</span>
          </div>
        )}

        {/* Shared view placeholder */}
        {isSharedView && (
          <div className="flex flex-col items-center justify-center h-full min-h-[300px] text-center">
            <Share2 size={48} className="text-ink-600 mb-4" />
            <p className="text-ink-400 text-sm">{t('resources.sharedComingSoon')}</p>
          </div>
        )}

        {/* Recycle Bin shortcut on mobile */}
        {!selectedFolderId && !selectedSmartFolderId && !selectedLibraryId && isResourcesView && !isRecycleView && (
          <div className="md:hidden mb-3">
            <button
              onClick={() => navigate(resPath('/resources/recycle'))}
              data-context-item
              className="w-full flex items-center gap-3 px-4 py-3 bg-ink-800/30 hover:bg-ink-800/60 rounded-xl border border-ink-700/30 transition-colors"
            >
              <div className="w-10 h-10 rounded-lg bg-ink-700/40 flex items-center justify-center">
                <Trash2 size={18} className="text-ink-400" />
              </div>
              <div className="flex-1 text-left">
                <span className="text-sm text-ink-300 font-medium">Recycle Bin</span>
              </div>
              <ChevronRight size={16} className="text-ink-600" />
            </button>
          </div>
        )}

        {/* Inline new folder input */}
        {creatingFolder && !isSharedView && !isRecycleView && (
          <div className="mb-4 flex items-center gap-2 max-w-sm">
            <FolderPlus size={16} className="text-amber-400 shrink-0" />
            <input
              ref={newFolderInputRef}
              type="text"
              value={newFolderName}
              onChange={(e) => onNewFolderNameChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') onCreateFolder();
                if (e.key === 'Escape') onCancelCreateFolder();
              }}
              onBlur={() => {
                if (!newFolderName.trim()) onCancelCreateFolder();
              }}
              placeholder={t('resources.folderName')}
              disabled={savingFolder}
              className="flex-1 bg-ink-900 border border-ink-700 rounded-lg px-3 py-1.5 text-sm text-ink-200 placeholder-ink-500 focus:outline-none focus:border-indigo-500 disabled:opacity-50"
            />
            {savingFolder && (
              <Loader2 size={14} className="animate-spin text-ink-400" />
            )}
          </div>
        )}

        {/* Resources / Recycle content */}
        {!isSharedView && (
          loading ? (
            viewMode === 'list' ? <SkeletonList /> : <SkeletonGrid />
          ) : (visibleFolders.length > 0 || sortedItems.length > 0 || (isRecycleView && recycleSubFolders.length > 0)) ? (
            <div className="space-y-5">
              {/* Trashed folders in recycle bin */}
              {isRecycleView && recycleSubFolders.length > 0 && (
                <div>
                  {sortedItems.length > 0 && (
                    <h3 className="text-[11px] font-semibold text-ink-500 uppercase tracking-widest mb-3">{t('resources.folders')}</h3>
                  )}
                  <div className="grid grid-cols-2 gap-3 downloads-grid">
                    {recycleSubFolders.map((folder) => (
                      <FolderCard
                        key={`trashed-folder-${folder.id}`}
                        folder={folder}
                        viewMode="grid"
                        onClick={(e?: any) => {
                          onCardClick(`folder:${folder.id}`, e);
                          if (!(e?.metaKey || e?.ctrlKey || e?.shiftKey)) {
                            setSelectedResource(null);
                            if (selectedFolder?.id === folder.id) {
                              setSelectedFolder(null);
                            } else {
                              setSelectedFolder(folder);
                              setShowInfoPanel(true);
                            }
                          }
                        }}
                        onDoubleClick={() => setRecycleFolderId(String(folder.id))}
                        previewItems={trashedFolderPreviews[String(folder.id)]}
                        selectable
                        isChecked={selectedIds.has(`folder:${folder.id}`)}
                        onToggleSelect={(e) => onToggleSelect(`folder:${folder.id}`, e)}
                        forceShowCheckbox={multiSelectMode}
                      />
                    ))}
                  </div>
                </div>
              )}

              {/* Folders section */}
              {visibleFolders.length > 0 && (
                <div>
                  {sortedItems.length > 0 && (
                    <h3 className="text-[11px] font-semibold text-ink-500 uppercase tracking-widest mb-3">{t('resources.folders')}</h3>
                  )}
                  {viewMode === 'grid' || viewMode === 'justified' ? (
                    <div className="grid grid-cols-2 gap-3 downloads-grid">
                      {visibleFolders.map((folder) => {
                        const folderNavigate = () => {
                          if (selectedLibraryId) {
                            navigate(resPath(`/resources/library/${selectedLibraryId}/folder/${folder.id}`));
                          } else {
                            navigate(resPath(`/resources/folder/${folder.id}`));
                          }
                        };
                        return (
                        <div
                          key={`folder-${folder.id}`}
                          data-folder-id={String(folder.id)}
                          className={isTouchDropTarget(String(folder.id)) ? 'ring-2 ring-indigo-500 rounded-xl transition-shadow' : ''}
                          {...getItemTouchHandlers('folder', folder)}
                        >
                        <FolderCard
                          folder={folder}
                          onClick={(e?: any) => {
                            if (isMobileDevice) {
                              folderNavigate();
                              return;
                            }
                            onCardClick(`folder:${folder.id}`, e);
                            if (!(e?.metaKey || e?.ctrlKey || e?.shiftKey)) {
                              setSelectedResource(null);
                              if (selectedFolder?.id === folder.id) {
                                setSelectedFolder(null);
                              } else {
                                setSelectedFolder(folder);
                                setShowInfoPanel(true);
                              }
                            }
                          }}
                          onDoubleClick={folderNavigate}
                          viewMode="grid"
                          onContextMenu={(e) => onFolderContextMenu(e, folder)}
                          renaming={renamingFolderId === folder.id}
                          renameValue={renamingFolderId === folder.id ? renameFolderValue : undefined}
                          onRenameChange={onRenameFolderChange}
                          onRenameConfirm={onRenameFolderConfirm}
                          onRenameCancel={onRenameFolderCancel}
                          onStartRename={() => onStartRenameFolder(folder.id, folder.name)}
                          selectable
                          isChecked={selectedIds.has(`folder:${folder.id}`)}
                          onToggleSelect={(e) => onToggleSelect(`folder:${folder.id}`, e)}
                          forceShowCheckbox={multiSelectMode}
                          onDropItems={(ids) => onDropOnFolder(folder.id, ids)}
                          previewItems={folderPreviews[folder.id]}
                        />
                        </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="space-y-1.5">
                      {visibleFolders.map((folder) => {
                        const folderNavigate = () => {
                          if (selectedLibraryId) {
                            navigate(resPath(`/resources/library/${selectedLibraryId}/folder/${folder.id}`));
                          } else {
                            navigate(resPath(`/resources/folder/${folder.id}`));
                          }
                        };
                        return (
                        <div
                          key={`folder-${folder.id}`}
                          data-folder-id={String(folder.id)}
                          className={isTouchDropTarget(String(folder.id)) ? 'ring-2 ring-indigo-500 rounded-xl transition-shadow' : ''}
                          {...getItemTouchHandlers('folder', folder)}
                        >
                        <FolderCard
                          folder={folder}
                          onClick={(e?: any) => {
                            if (isMobileDevice) {
                              folderNavigate();
                              return;
                            }
                            onCardClick(`folder:${folder.id}`, e);
                            if (!(e?.metaKey || e?.ctrlKey || e?.shiftKey)) {
                              setSelectedResource(null);
                              if (selectedFolder?.id === folder.id) {
                                setSelectedFolder(null);
                              } else {
                                setSelectedFolder(folder);
                                setShowInfoPanel(true);
                              }
                            }
                          }}
                          onDoubleClick={folderNavigate}
                          viewMode="list"
                          onContextMenu={(e) => onFolderContextMenu(e, folder)}
                          renaming={renamingFolderId === folder.id}
                          renameValue={renamingFolderId === folder.id ? renameFolderValue : undefined}
                          onRenameChange={onRenameFolderChange}
                          onRenameConfirm={onRenameFolderConfirm}
                          onRenameCancel={onRenameFolderCancel}
                          onStartRename={() => onStartRenameFolder(folder.id, folder.name)}
                          selectable
                          isChecked={selectedIds.has(`folder:${folder.id}`)}
                          onToggleSelect={(e) => onToggleSelect(`folder:${folder.id}`, e)}
                          forceShowCheckbox={multiSelectMode}
                          onDropItems={(ids) => onDropOnFolder(folder.id, ids)}
                          previewItems={folderPreviews[folder.id]}
                        />
                        </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}

              {/* Files section */}
              {sortedItems.length > 0 && (
                <div>
                  {(visibleFolders.length > 0 || (isRecycleView && recycleSubFolders.length > 0)) && (
                    <h3 className="text-[11px] font-semibold text-ink-500 uppercase tracking-widest mb-3">{t('resources.files')}</h3>
                  )}
                  {viewMode === 'list' && (
                    <div className="flex items-center gap-4 px-4 py-2 text-[11px] font-semibold text-ink-500 uppercase tracking-wider border-b border-ink-800/60 mb-1">
                      <div className="w-10" />
                      <button onClick={() => setSortBy(sortBy === 'name-az' ? 'name-za' : 'name-az')} className="flex-1 text-left hover:text-ink-300 transition-colors cursor-pointer">
                        {t('resources.listHeaderName')} {sortBy === 'name-az' ? '\u2191' : sortBy === 'name-za' ? '\u2193' : ''}
                      </button>
                      <span className="w-24 text-left">{t('resources.listHeaderType')}</span>
                      <button onClick={() => setSortBy(sortBy === 'largest' ? 'smallest' : 'largest')} className="w-20 text-right hover:text-ink-300 transition-colors cursor-pointer">
                        {t('resources.listHeaderSize')} {sortBy === 'largest' ? '\u2193' : sortBy === 'smallest' ? '\u2191' : ''}
                      </button>
                      <button onClick={() => setSortBy(sortBy === 'newest' ? 'oldest' : 'newest')} className="w-28 text-right hover:text-ink-300 transition-colors cursor-pointer">
                        {t('resources.modifiedAt')} {sortBy === 'newest' ? '\u2193' : sortBy === 'oldest' ? '\u2191' : ''}
                      </button>
                    </div>
                  )}
                  {viewMode === 'justified' ? (
                    <div className="flex flex-wrap gap-2 justified-grid">
                      {sortedItems.map((item) => {
                        const badge = isTempContext ? ttlBadgeText(item.created_at, scopeTtl) : '';
                        const ar = aspectRatioOf(item.resource);
                        return (
                        <div
                          key={item.id}
                          style={{ flexGrow: ar, flexBasis: `${ar * 170}px` }}
                          className="min-w-[140px] max-w-full"
                          {...getItemTouchHandlers('file', item)}
                        >
                        <ResourceCard
                          item={item}
                          onClick={(e?: any) => {
                            if (isMobileDevice) {
                              onResourceDoubleClick(item);
                              return;
                            }
                            onCardClick(`item:${item.id}`, e);
                            if (!(e?.metaKey || e?.ctrlKey || e?.shiftKey)) onResourceClick(item);
                          }}
                          onDoubleClick={() => onResourceDoubleClick(item)}
                          viewMode="grid"
                          aspectRatio={ar}
                          isSelected={selectedResource?.id === item.id}
                          showRestoreAction={isRecycleView}
                          onTrash={isRecycleView ? undefined : handleTrash}
                          onRestore={isRecycleView ? handleRestore : undefined}
                          onPermanentDelete={isRecycleView ? handlePermanentDelete : undefined}
                          onContextMenu={!isRecycleView ? (e) => onFileContextMenu(e, item) : undefined}
                          renaming={renamingResourceId === item.id}
                          renameValue={renamingResourceId === item.id ? renameValue : undefined}
                          onRenameChange={onRenameChange}
                          onRenameConfirm={onRenameResourceConfirm}
                          onRenameCancel={onRenameResourceCancel}
                          onStartRename={() => onStartRenameResource(item.id, item.resource?.filename ?? '')}
                          selectable
                          isChecked={selectedIds.has(`item:${item.id}`)}
                          onToggleSelect={(e) => onToggleSelect(`item:${item.id}`, e)}
                          forceShowCheckbox={multiSelectMode}
                          selectedIds={selectedIds}
                          compositeId={`item:${item.id}`}
                          isTranscoding={!!item.resource?.id && transcodingResourceIds.has(String(item.resource.id))}
                        />
                        {isTempContext && (
                          <div className="flex items-center gap-2 px-2 py-1.5 bg-ink-900/60 rounded-b-xl border-t border-ink-800/50">
                            {badge && (
                              <span className="text-xs text-amber-700 dark:text-amber-300 flex-1 truncate">
                                {badge}
                              </span>
                            )}
                            <TempResourceActions
                              resourceId={item.resource_id}
                              scopeType={scopeType}
                              scopeId={scopeId}
                              onDone={isTempView ? reloadTemp : reloadResources}
                            />
                          </div>
                        )}
                        </div>
                        );
                      })}
                    </div>
                  ) : viewMode === 'grid' ? (
                    <div className="grid grid-cols-2 gap-3 downloads-grid">
                      {sortedItems.map((item) => {
                        const badge = isTempContext ? ttlBadgeText(item.created_at, scopeTtl) : '';
                        return (
                        <div key={item.id} {...getItemTouchHandlers('file', item)}>
                        <ResourceCard
                          item={item}
                          onClick={(e?: any) => {
                            if (isMobileDevice) {
                              onResourceDoubleClick(item);
                              return;
                            }
                            onCardClick(`item:${item.id}`, e);
                            if (!(e?.metaKey || e?.ctrlKey || e?.shiftKey)) onResourceClick(item);
                          }}
                          onDoubleClick={() => onResourceDoubleClick(item)}
                          viewMode="grid"
                          isSelected={selectedResource?.id === item.id}
                          showRestoreAction={isRecycleView}
                          onTrash={isRecycleView ? undefined : handleTrash}
                          onRestore={isRecycleView ? handleRestore : undefined}
                          onPermanentDelete={isRecycleView ? handlePermanentDelete : undefined}
                          onContextMenu={!isRecycleView ? (e) => onFileContextMenu(e, item) : undefined}
                          renaming={renamingResourceId === item.id}
                          renameValue={renamingResourceId === item.id ? renameValue : undefined}
                          onRenameChange={onRenameChange}
                          onRenameConfirm={onRenameResourceConfirm}
                          onRenameCancel={onRenameResourceCancel}
                          onStartRename={() => onStartRenameResource(item.id, item.resource?.filename ?? '')}
                          selectable
                          isChecked={selectedIds.has(`item:${item.id}`)}
                          onToggleSelect={(e) => onToggleSelect(`item:${item.id}`, e)}
                          forceShowCheckbox={multiSelectMode}
                          selectedIds={selectedIds}
                          compositeId={`item:${item.id}`}
                          isTranscoding={!!item.resource?.id && transcodingResourceIds.has(String(item.resource.id))}
                        />
                        {isTempContext && (
                          <div className="flex items-center gap-2 px-2 py-1.5 bg-ink-900/60 rounded-b-xl border-t border-ink-800/50">
                            {badge && (
                              <span className="text-xs text-amber-700 dark:text-amber-300 flex-1 truncate">
                                {badge}
                              </span>
                            )}
                            <TempResourceActions
                              resourceId={item.resource_id}
                              scopeType={scopeType}
                              scopeId={scopeId}
                              onDone={isTempView ? reloadTemp : reloadResources}
                            />
                          </div>
                        )}
                        </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="space-y-1.5">
                      {sortedItems.map((item) => {
                        const badge = isTempContext ? ttlBadgeText(item.created_at, scopeTtl) : '';
                        return (
                        <div key={item.id} {...getItemTouchHandlers('file', item)}>
                        <ResourceCard
                          item={item}
                          onClick={(e?: any) => {
                            if (isMobileDevice) {
                              onResourceDoubleClick(item);
                              return;
                            }
                            onCardClick(`item:${item.id}`, e);
                            if (!(e?.metaKey || e?.ctrlKey || e?.shiftKey)) onResourceClick(item);
                          }}
                          onDoubleClick={() => onResourceDoubleClick(item)}
                          viewMode="list"
                          isSelected={selectedResource?.id === item.id}
                          showRestoreAction={isRecycleView}
                          onTrash={isRecycleView ? undefined : handleTrash}
                          onRestore={isRecycleView ? handleRestore : undefined}
                          onPermanentDelete={isRecycleView ? handlePermanentDelete : undefined}
                          onContextMenu={!isRecycleView ? (e) => onFileContextMenu(e, item) : undefined}
                          renaming={renamingResourceId === item.id}
                          renameValue={renamingResourceId === item.id ? renameValue : undefined}
                          onRenameChange={onRenameChange}
                          onRenameConfirm={onRenameResourceConfirm}
                          onRenameCancel={onRenameResourceCancel}
                          onStartRename={() => onStartRenameResource(item.id, item.resource?.filename ?? '')}
                          selectable
                          isChecked={selectedIds.has(`item:${item.id}`)}
                          onToggleSelect={(e) => onToggleSelect(`item:${item.id}`, e)}
                          forceShowCheckbox={multiSelectMode}
                          selectedIds={selectedIds}
                          compositeId={`item:${item.id}`}
                          isTranscoding={!!item.resource?.id && transcodingResourceIds.has(String(item.resource.id))}
                        />
                        {isTempContext && (
                          <div className="flex items-center gap-2 px-4 py-1.5 border-t border-ink-800/50">
                            {badge && (
                              <span className="text-xs text-amber-700 dark:text-amber-300 flex-1 truncate">
                                {badge}
                              </span>
                            )}
                            <TempResourceActions
                              resourceId={item.resource_id}
                              scopeType={scopeType}
                              scopeId={scopeId}
                              onDone={isTempView ? reloadTemp : reloadResources}
                            />
                          </div>
                        )}
                        </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : (
            /* Empty states */
            <div className="flex flex-col items-center justify-center h-full min-h-[300px] text-center">
              {isRecycleView ? (
                <>
                  <Trash2 size={48} className="text-ink-700 mb-4" />
                  <p className="text-ink-500 text-sm">{t('resources.recycleBinEmpty')}</p>
                </>
              ) : isTempView ? (
                <>
                  <Clock size={48} className="text-ink-700 mb-4" />
                  <p className="text-ink-500 text-sm">{t('resources.tempEmpty')}</p>
                </>
              ) : (
                <>
                  <FolderOpen size={48} className="text-ink-700 mb-4" />
                  <p className="text-ink-500 text-sm">{t('resources.noResources')}</p>
                  <p className="text-ink-600 text-xs mt-1">{t('resources.noResourcesHint')}</p>
                </>
              )}
            </div>
          )
        )}

        {/* Keyset pagination sentinel — observed by the infinite-scroll effect
            above. Only the Resources library paginates; recycle/temp load
            eagerly. Unmounts when the scope is drained (hasMore=false). */}
        {(isResourcesView || isRecycleView) && (hasMore || isLoadingMore) && (
          <div ref={loadMoreRef} className="w-full flex justify-center py-6">
            {isLoadingMore && (
              <span className="text-ink-500 text-sm">{t('common.loading')}</span>
            )}
          </div>
        )}
      </div>

      {/* Bug F (issue #194) — Web URL fetch modal, mounted as a sibling
          so the dropdown closing doesn't unmount it mid-submit. */}
      <ResourceFetchUrlModal
        isOpen={showFetchUrlModal}
        onClose={() => setShowFetchUrlModal(false)}
      />
    </div>
  );
};
