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
import type { UseFilterBarConfigReturn } from '../hooks/useFilterBarConfig';
import { useFilterBarVisibility } from '../hooks/useFilterBarVisibility';

// ─── Skeleton components ────────────────────────────────

const SkeletonGrid: React.FC = () => (
  <div className="grid grid-cols-2 gap-4 downloads-grid">
    {Array.from({ length: 8 }).map((_, i) => (
      <div key={i} className="bg-zinc-800/80 border border-zinc-700/50 rounded-xl overflow-hidden animate-pulse">
        <div className="h-32 bg-zinc-800" />
        <div className="p-3 space-y-2">
          <div className="h-4 bg-zinc-700 rounded w-3/4" />
          <div className="h-3 bg-zinc-700 rounded w-1/2" />
        </div>
      </div>
    ))}
  </div>
);

const SkeletonList: React.FC = () => (
  <div className="space-y-2">
    {Array.from({ length: 6 }).map((_, i) => (
      <div key={i} className="flex items-center gap-4 px-4 py-3 bg-zinc-800/60 border border-zinc-700/30 rounded-xl animate-pulse">
        <div className="w-10 h-10 bg-zinc-700 rounded-lg" />
        <div className="flex-1 h-4 bg-zinc-700 rounded w-1/3" />
        <div className="w-16 h-3 bg-zinc-700 rounded" />
        <div className="w-20 h-3 bg-zinc-700 rounded" />
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

  // Sort state & callbacks
  sortOptions: Array<{ value: SortBy; label: string }>;
  currentSortLabel: string;

  // Search callbacks
  onQueryChange: (q: string) => void;
  onAISearch: (q: string, mode: 'hybrid' | 'semantic') => Promise<void>;
  onSearchClear: () => void;
  isAISearching: boolean;

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
  sortOptions,
  currentSortLabel,
  onQueryChange,
  onAISearch,
  onSearchClear,
  isAISearching,
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
}) => {
  const { t } = useTranslation();
  const ctx = useResourcesContext();
  const {
    scopeType, selectedFolderId, selectedLibraryId, selectedSmartFolderId,
    isResourcesView, isRecycleView, isSharedView,
    loading, viewMode, setViewMode, sortBy, setSortBy,
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
  } = ctx;

  // Mobile detection (matches Tailwind md: breakpoint at 768px)
  const isMobileDevice = typeof window !== 'undefined' && window.innerWidth < 768;

  // Filter bar visibility — search-row toggle remembers the choice in
  // localStorage. Hidden in shared / recycle views where filters don't
  // apply anyway.
  const { visible: isFilterBarVisible, toggle: toggleFilterBar } = useFilterBarVisibility();
  const filterBarEligible = !isRecycleView && !isSharedView;
  const shouldRenderFilterBar = filterBarEligible && isFilterBarVisible;

  // Mobile search state
  const [isMobileSearchOpen, setIsMobileSearchOpen] = useState(false);

  // Upload dropdown
  const [showUploadDropdown, setShowUploadDropdown] = useState(false);
  const uploadDropdownRef = useRef<HTMLDivElement>(null);

  // New dropdown
  const [showNewDropdown, setShowNewDropdown] = useState(false);
  const newDropdownRef = useRef<HTMLDivElement>(null);

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
    <div className="flex-1 min-w-0 flex flex-col">
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
            className="p-2.5 -ml-2 text-zinc-400 hover:text-zinc-200 active:bg-zinc-700/50 rounded-lg"
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
        <div className="md:hidden fixed top-2.5 right-3 z-40 flex justify-end items-start pointer-events-none">
          <div className="pointer-events-auto flex items-center justify-end">
            {isMobileSearchOpen ? (
              <div className="flex items-center bg-black/50 backdrop-blur-md rounded-full px-4 py-2.5 w-[calc(100vw-80px)] max-w-sm animate-in slide-in-from-right-10 duration-200 border border-white/10 shadow-lg">
                <Search size={16} className="text-zinc-300 mr-2 flex-shrink-0" />
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
                  className="bg-transparent border-none outline-none text-white text-sm w-full placeholder-zinc-400"
                />
                <button
                  onClick={() => {
                    setIsMobileSearchOpen(false);
                    onSearchClear();
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

      {/* Toolbar -- desktop only */}
      <div
        className="hidden md:block px-3 md:px-6 pt-3 pb-2 border-b border-zinc-800/80 space-y-2"
        style={{ paddingRight: (selectedResource?.resource || selectedFolder) && showInfoPanel ? `${infoPanelWidth + 24}px` : undefined }}
      >
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            {breadcrumbSegments.length <= 1 ? (
              <div>
                <h2 className="text-lg font-semibold text-zinc-100">{breadcrumbSegments[0]?.label}</h2>
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
                    : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'
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
                    : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'
                }`}
                title={currentSortLabel}
              >
                <ArrowUpDown size={14} />
              </button>
              {showSortMenu && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setShowSortMenu(false)} />
                  <div className="absolute right-0 top-full mt-1.5 z-20 bg-zinc-900/95 backdrop-blur-sm border border-zinc-700/80 rounded-xl shadow-2xl py-1.5 w-44 animate-dropdown">
                    {sortOptions.map((opt) => (
                      <button
                        key={opt.value}
                        onClick={() => { setSortBy(opt.value); setShowSortMenu(false); }}
                        className={`w-full text-left px-3 py-2 text-xs transition-colors flex items-center justify-between ${
                          sortBy === opt.value
                            ? 'bg-indigo-500/10 text-indigo-400'
                            : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
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

            {/* View toggle */}
            <button
              onClick={() => setViewMode(viewMode === 'grid' ? 'list' : 'grid')}
              className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
              title={viewMode === 'grid' ? t('resources.listView') : t('resources.gridView')}
            >
              {viewMode === 'grid' ? <LayoutList size={14} /> : <LayoutGrid size={14} />}
            </button>

            {/* Upload button */}
            {canUpload && (
              <>
                {uploading ? (
                  <button
                    disabled
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-indigo-800 text-white rounded-lg"
                  >
                    <Loader2 size={14} className="animate-spin" />
                    <span>{overallProgress}%</span>
                  </button>
                ) : (
                  <div className="relative" ref={uploadDropdownRef}>
                    <div className="flex">
                      <button
                        onClick={() => fileInputRef.current?.click()}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-indigo-600 hover:bg-indigo-500 text-white rounded-l-lg transition-colors"
                      >
                        <Upload size={14} />
                        <span>{t('resources.upload')}</span>
                      </button>
                      <button
                        onClick={() => setShowUploadDropdown(prev => !prev)}
                        className="px-1.5 py-1.5 text-xs font-medium bg-indigo-600 hover:bg-indigo-500 text-white rounded-r-lg border-l border-indigo-500 transition-colors"
                      >
                        <ChevronDown size={12} />
                      </button>
                    </div>
                    {showUploadDropdown && (
                      <div className="absolute right-0 top-full mt-1 z-20 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl py-1 w-40">
                        <button
                          onClick={() => { fileInputRef.current?.click(); setShowUploadDropdown(false); }}
                          className="w-full text-left px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors flex items-center gap-2"
                        >
                          <Upload size={12} />
                          {t('resources.uploadFile')}
                        </button>
                        <button
                          onClick={() => { folderInputRef.current?.click(); setShowUploadDropdown(false); }}
                          className="w-full text-left px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors flex items-center gap-2"
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
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-amber-600 hover:bg-amber-500 text-white rounded-lg transition-colors"
                  >
                    <Sparkles size={14} />
                    <span>{t('resources.new')}</span>
                    <ChevronDown size={12} />
                  </button>
                  {showNewDropdown && (
                    <div className="absolute right-0 top-full mt-1 z-20 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl py-1 w-48">
                      {/* Group 1 -- Containers */}
                      <button
                        onClick={() => { onStartCreateFolder(); setShowNewDropdown(false); }}
                        className="w-full text-left px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors flex items-center gap-2"
                      >
                        <FolderPlus size={14} className="text-amber-400" />
                        {t('resources.newFolder')}
                      </button>
                      <button
                        onClick={() => { addToast(t('resources.comingSoon'), 'info'); setShowNewDropdown(false); }}
                        className="w-full text-left px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors flex items-center gap-2"
                      >
                        <LayoutGrid size={14} className="text-blue-400" />
                        {t('resources.newProject')}
                      </button>
                      <button
                        onClick={() => { onShowSmartFolderEditor(); setShowNewDropdown(false); }}
                        className="w-full text-left px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors flex items-center gap-2"
                      >
                        <FolderSearch size={14} className="text-purple-400" />
                        {t('resources.newSmartFolder')}
                      </button>
                      {/* Divider */}
                      <div className="border-t border-zinc-800 my-1" />
                      {/* Group 2 -- Documents */}
                      <button
                        onClick={() => { addToast(t('resources.comingSoon'), 'info'); setShowNewDropdown(false); }}
                        className="w-full text-left px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors flex items-center gap-2"
                      >
                        <FileText size={14} className="text-emerald-400" />
                        {t('resources.newDocument')}
                      </button>
                      <button
                        onClick={() => { addToast(t('resources.comingSoon'), 'info'); setShowNewDropdown(false); }}
                        className="w-full text-left px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors flex items-center gap-2"
                      >
                        <Table2 size={14} className="text-cyan-400" />
                        {t('resources.newSpreadsheet')}
                      </button>
                      <button
                        onClick={() => { addToast(t('resources.comingSoon'), 'info'); setShowNewDropdown(false); }}
                        className="w-full text-left px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors flex items-center gap-2"
                      >
                        <Presentation size={14} className="text-orange-400" />
                        {t('resources.newPresentation')}
                      </button>
                      {/* Divider */}
                      <div className="border-t border-zinc-800 my-1" />
                      {/* Group 3 -- Other */}
                      <button
                        onClick={() => { addToast(t('resources.comingSoon'), 'info'); setShowNewDropdown(false); }}
                        className="w-full text-left px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors flex items-center gap-2"
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
          />
        )}
      </div>

      {/* Multi-select mode toolbar */}
      {multiSelectMode && (
        <div className="px-6 py-2 border-b border-zinc-800/80 bg-zinc-900/80 flex items-center gap-3">
          <button
            onClick={() => setSelectedIds(new Set(allSelectableIds))}
            className="px-3 py-1 text-xs font-medium text-zinc-300 bg-zinc-800 hover:bg-zinc-700 rounded-lg transition-colors"
          >
            {t('resources.selectAll')}
          </button>
          <button
            onClick={() => { setSelectedIds(new Set()); setMultiSelectMode(false); setSelectedResource(null); }}
            className="px-3 py-1 text-xs font-medium text-zinc-400 bg-zinc-800 hover:bg-zinc-700 rounded-lg transition-colors"
          >
            {t('common.cancel')}
          </button>
          <span className="text-xs text-zinc-500">
            {t('resources.multiSelectCount', {
              total: filteredFolders.length + sortedItems.length,
              selected: selectedIds.size,
            })}
          </span>
        </div>
      )}

      {/* Content area */}
      <div
        className="flex-1 overflow-y-auto p-3 md:p-6 relative"
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
          <div className="absolute inset-0 z-30 flex flex-col items-center justify-center bg-zinc-950/80 backdrop-blur-sm border-2 border-dashed border-indigo-500 rounded-xl m-2 pointer-events-none">
            <UploadCloud size={56} className="text-indigo-400 mb-4 animate-bounce" />
            <p className="text-lg font-medium text-indigo-300">{t('resources.dropToUpload')}</p>
            <p className="text-sm text-zinc-400 mt-1">{t('resources.dropToUploadHint')}</p>
          </div>
        )}

        {/* Recycle bin auto-cleanup notice */}
        {isRecycleView && (
          <div className="mb-4 flex items-center gap-2 px-3 py-2.5 bg-zinc-800/50 border border-zinc-700/50 rounded-lg text-xs text-zinc-400">
            <Clock size={14} className="shrink-0 text-zinc-500" />
            <span>{t('resources.recycleBinAutoCleanup')}</span>
          </div>
        )}

        {/* Shared view placeholder */}
        {isSharedView && (
          <div className="flex flex-col items-center justify-center h-full min-h-[300px] text-center">
            <Share2 size={48} className="text-zinc-600 mb-4" />
            <p className="text-zinc-400 text-sm">{t('resources.sharedComingSoon')}</p>
          </div>
        )}

        {/* Recycle Bin shortcut on mobile */}
        {!selectedFolderId && !selectedSmartFolderId && !selectedLibraryId && isResourcesView && !isRecycleView && (
          <div className="md:hidden mb-3">
            <button
              onClick={() => navigate(resPath('/resources/recycle'))}
              data-context-item
              className="w-full flex items-center gap-3 px-4 py-3 bg-zinc-800/30 hover:bg-zinc-800/60 rounded-xl border border-zinc-700/30 transition-colors"
            >
              <div className="w-10 h-10 rounded-lg bg-zinc-700/40 flex items-center justify-center">
                <Trash2 size={18} className="text-zinc-400" />
              </div>
              <div className="flex-1 text-left">
                <span className="text-sm text-zinc-300 font-medium">Recycle Bin</span>
              </div>
              <ChevronRight size={16} className="text-zinc-600" />
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
              className="flex-1 bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-1.5 text-sm text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 disabled:opacity-50"
            />
            {savingFolder && (
              <Loader2 size={14} className="animate-spin text-zinc-400" />
            )}
          </div>
        )}

        {/* Resources / Recycle content */}
        {!isSharedView && (
          loading ? (
            viewMode === 'grid' ? <SkeletonGrid /> : <SkeletonList />
          ) : (filteredFolders.length > 0 || sortedItems.length > 0 || (isRecycleView && recycleSubFolders.length > 0)) ? (
            <div className="space-y-5">
              {/* Trashed folders in recycle bin */}
              {isRecycleView && recycleSubFolders.length > 0 && (
                <div>
                  {sortedItems.length > 0 && (
                    <h3 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-3">{t('resources.folders')}</h3>
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
              {filteredFolders.length > 0 && (
                <div>
                  {sortedItems.length > 0 && (
                    <h3 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-3">{t('resources.folders')}</h3>
                  )}
                  {viewMode === 'grid' ? (
                    <div className="grid grid-cols-2 gap-3 downloads-grid">
                      {filteredFolders.map((folder) => {
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
                      {filteredFolders.map((folder) => {
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
                  {(filteredFolders.length > 0 || (isRecycleView && recycleSubFolders.length > 0)) && (
                    <h3 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-3">{t('resources.files')}</h3>
                  )}
                  {viewMode === 'list' && (
                    <div className="flex items-center gap-4 px-4 py-2 text-[11px] font-semibold text-zinc-500 uppercase tracking-wider border-b border-zinc-800/60 mb-1">
                      <div className="w-10" />
                      <button onClick={() => setSortBy(sortBy === 'name-az' ? 'name-za' : 'name-az')} className="flex-1 text-left hover:text-zinc-300 transition-colors cursor-pointer">
                        {t('resources.listHeaderName')} {sortBy === 'name-az' ? '\u2191' : sortBy === 'name-za' ? '\u2193' : ''}
                      </button>
                      <span className="w-24 text-left">{t('resources.listHeaderType')}</span>
                      <button onClick={() => setSortBy(sortBy === 'largest' ? 'smallest' : 'largest')} className="w-20 text-right hover:text-zinc-300 transition-colors cursor-pointer">
                        {t('resources.listHeaderSize')} {sortBy === 'largest' ? '\u2193' : sortBy === 'smallest' ? '\u2191' : ''}
                      </button>
                      <button onClick={() => setSortBy(sortBy === 'newest' ? 'oldest' : 'newest')} className="w-28 text-right hover:text-zinc-300 transition-colors cursor-pointer">
                        {t('resources.modifiedAt')} {sortBy === 'newest' ? '\u2193' : sortBy === 'oldest' ? '\u2191' : ''}
                      </button>
                    </div>
                  )}
                  {viewMode === 'grid' ? (
                    <div className="grid grid-cols-2 gap-3 downloads-grid">
                      {sortedItems.map((item) => (
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
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="space-y-1.5">
                      {sortedItems.map((item) => (
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
                        </div>
                      ))}
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
                  <Trash2 size={48} className="text-zinc-700 mb-4" />
                  <p className="text-zinc-500 text-sm">{t('resources.recycleBinEmpty')}</p>
                </>
              ) : (
                <>
                  <FolderOpen size={48} className="text-zinc-700 mb-4" />
                  <p className="text-zinc-500 text-sm">{t('resources.noResources')}</p>
                  <p className="text-zinc-600 text-xs mt-1">{t('resources.noResourcesHint')}</p>
                </>
              )}
            </div>
          )
        )}
      </div>
    </div>
  );
};
