import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  FolderOpen,
  FolderPlus,
  Loader2,
  Upload,
  Trash2,
  LayoutGrid,
  LayoutList,
  ChevronDown,
  Share2,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Folder, ResourceItem, Tag, SmartCollection } from '../types';
import {
  fetchFolders,
  buildFolderTree,
  fetchResources,
  createFolder,
  uploadResource,
  trashResource,
  restoreResource,
  permanentDeleteResource,
  fetchTrashedResources,
  fetchResourceTags,
  addResourceTag,
  removeResourceTag,
  fetchSmartFolders,
  fetchSmartFolderResources,
} from '../services/resourceService';
import { fetchTags } from '../services/tagsService';
import { ResourceCard } from './ResourceCard';
import { ResourceInfoPanel } from './ResourceInfoPanel';

// ─── Props ─────────────────────────────────────────────

interface ResourcesViewProps {
  scopeType: 'personal' | 'team';
  scopeId: string;
}

type ActiveTab = 'files' | 'shared' | 'recycle';
type SortBy = 'newest' | 'oldest' | 'name-az' | 'name-za' | 'largest' | 'smallest';

// ─── Folder Tree Item (recursive) ─────────────────────

interface FolderTreeItemProps {
  folder: Folder;
  selectedFolderId: string | null;
  onSelect: (id: string) => void;
  depth?: number;
}

const FolderTreeItem: React.FC<FolderTreeItemProps> = ({
  folder,
  selectedFolderId,
  onSelect,
  depth = 0,
}) => {
  const isSelected = selectedFolderId === folder.id;

  return (
    <>
      <button
        onClick={() => onSelect(folder.id)}
        className={`
          w-full flex items-center gap-2 px-3 py-1.5 text-sm rounded-md transition-colors text-left
          ${isSelected ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200'}
        `}
        style={{ paddingLeft: `${12 + depth * 16}px` }}
      >
        <FolderOpen size={14} className="shrink-0" />
        <span className="truncate">{folder.name}</span>
      </button>
      {folder.children?.map((child) => (
        <FolderTreeItem
          key={child.id}
          folder={child}
          selectedFolderId={selectedFolderId}
          onSelect={onSelect}
          depth={depth + 1}
        />
      ))}
    </>
  );
};

// ─── Skeleton ─────────────────────────────────────────

const SkeletonGrid: React.FC = () => (
  <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
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

// ─── Main Component ────────────────────────────────────

export const ResourcesView: React.FC<ResourcesViewProps> = ({
  scopeType,
  scopeId,
}) => {
  const { t } = useTranslation();

  // Data
  const [folders, setFolders] = useState<Folder[]>([]);
  const [folderTree, setFolderTree] = useState<Folder[]>([]);
  const [resources, setResources] = useState<ResourceItem[]>([]);
  const [selectedFolderId, setSelectedFolderId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [foldersLoading, setFoldersLoading] = useState(true);

  // New folder inline input
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [savingFolder, setSavingFolder] = useState(false);
  const newFolderInputRef = useRef<HTMLInputElement>(null);

  // Upload state
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Recycle bin
  const [trashedResources, setTrashedResources] = useState<ResourceItem[]>([]);

  // Tags
  const [allTags, setAllTags] = useState<Tag[]>([]);

  // Smart folders
  const [smartFolders, setSmartFolders] = useState<SmartCollection[]>([]);
  const [selectedSmartFolderId, setSelectedSmartFolderId] = useState<string | null>(null);

  // UI state
  const [activeTab, setActiveTab] = useState<ActiveTab>('files');
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  const [sortBy, setSortBy] = useState<SortBy>('newest');
  const [showSortMenu, setShowSortMenu] = useState(false);

  // Detail panel
  const [selectedResource, setSelectedResource] = useState<ResourceItem | null>(null);
  const [selectedResourceTags, setSelectedResourceTags] = useState<Array<{ tag: Tag }>>([]);

  // ─── Load folders on scope change ────────────────────

  const loadFolders = useCallback(async () => {
    setFoldersLoading(true);
    try {
      const allFolders = await fetchFolders(scopeType, scopeId);
      setFolders(allFolders);
      setFolderTree(buildFolderTree(allFolders));
    } catch {
      setFolders([]);
      setFolderTree([]);
    } finally {
      setFoldersLoading(false);
    }
  }, [scopeType, scopeId]);

  useEffect(() => {
    setSelectedFolderId(null);
    setSelectedSmartFolderId(null);
    setActiveTab('files');
    setSelectedResource(null);
    loadFolders();
    fetchTags().then(setAllTags).catch(() => {});
    fetchSmartFolders(scopeType, scopeId).then(setSmartFolders).catch(() => {});
  }, [loadFolders, scopeType, scopeId]);

  // ─── Load resources on folder change ─────────────────

  useEffect(() => {
    if (activeTab !== 'files') return;
    let cancelled = false;

    const loadResources = async () => {
      setLoading(true);
      try {
        let items: ResourceItem[];
        if (selectedSmartFolderId) {
          const sf = smartFolders.find((s) => String(s.id) === selectedSmartFolderId);
          items = sf ? await fetchSmartFolderResources(scopeType, scopeId, sf.rules as any) : [];
        } else {
          items = await fetchResources(scopeType, scopeId, selectedFolderId);
        }
        if (!cancelled) setResources(items);
      } catch {
        if (!cancelled) setResources([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    loadResources();
    return () => { cancelled = true; };
  }, [scopeType, scopeId, selectedFolderId, selectedSmartFolderId, smartFolders, activeTab]);

  // ─── Load trashed resources ──────────────────────────

  const loadTrashedResources = useCallback(async () => {
    try {
      const items = await fetchTrashedResources(scopeType, scopeId);
      setTrashedResources(items);
    } catch {
      setTrashedResources([]);
    }
  }, [scopeType, scopeId]);

  useEffect(() => {
    if (activeTab === 'recycle') {
      setLoading(true);
      loadTrashedResources().finally(() => setLoading(false));
    }
  }, [activeTab, loadTrashedResources]);

  // ─── Focus new folder input ──────────────────────────

  useEffect(() => {
    if (creatingFolder && newFolderInputRef.current) {
      newFolderInputRef.current.focus();
    }
  }, [creatingFolder]);

  // ─── Create folder handler ───────────────────────────

  const handleCreateFolder = async () => {
    const trimmed = newFolderName.trim();
    if (!trimmed || savingFolder) return;
    setSavingFolder(true);
    try {
      await createFolder({
        name: trimmed,
        parent_id: null,
        scope_type: scopeType,
        scope_id: scopeId,
      });
      setNewFolderName('');
      setCreatingFolder(false);
      await loadFolders();
    } catch {
      // Keep input open on error
    } finally {
      setSavingFolder(false);
    }
  };

  // ─── Upload handler ──────────────────────────────────

  const handleUpload = useCallback(async (files: FileList | File[]) => {
    if (!files.length || uploading) return;
    setUploading(true);
    setUploadProgress(0);
    try {
      for (let i = 0; i < files.length; i++) {
        await uploadResource(
          files[i],
          scopeType,
          scopeId,
          selectedFolderId,
          (progress) => {
            const overall = Math.round(((i + progress / 100) / files.length) * 100);
            setUploadProgress(overall);
          },
        );
      }
      const items = await fetchResources(scopeType, scopeId, selectedFolderId);
      setResources(items);
    } catch {
      // Upload failed
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  }, [scopeType, scopeId, selectedFolderId, uploading]);

  // ─── Drag & drop ─────────────────────────────────────

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files.length) handleUpload(e.dataTransfer.files);
  }, [handleUpload]);

  // ─── Trash handlers ──────────────────────────────────

  const handleTrash = useCallback(async (resourceId: string) => {
    try {
      await trashResource(resourceId);
      setResources((prev) => prev.filter((r) => r.resource?.id !== resourceId));
      if (selectedResource?.resource?.id === resourceId) setSelectedResource(null);
    } catch { /* ignore */ }
  }, [selectedResource]);

  const handleRestore = useCallback(async (resourceId: string) => {
    try {
      await restoreResource(resourceId);
      setTrashedResources((prev) => prev.filter((r) => r.resource?.id !== resourceId));
    } catch { /* ignore */ }
  }, []);

  const handlePermanentDelete = useCallback(async (resourceId: string) => {
    try {
      await permanentDeleteResource(resourceId);
      setTrashedResources((prev) => prev.filter((r) => r.resource?.id !== resourceId));
    } catch { /* ignore */ }
  }, []);

  // ─── Resource selection & detail panel ───────────────

  const handleResourceClick = useCallback((item: ResourceItem) => {
    if (selectedResource?.id === item.id) {
      setSelectedResource(null);
    } else {
      setSelectedResource(item);
    }
  }, [selectedResource]);

  // Load tags when selected resource changes
  useEffect(() => {
    if (!selectedResource?.resource?.id) {
      setSelectedResourceTags([]);
      return;
    }
    fetchResourceTags(selectedResource.resource.id)
      .then(setSelectedResourceTags)
      .catch(() => setSelectedResourceTags([]));
  }, [selectedResource?.resource?.id]);

  // ESC to close panel
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSelectedResource(null);
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, []);

  // Clear selection on tab/folder change
  useEffect(() => {
    setSelectedResource(null);
  }, [activeTab, selectedFolderId, selectedSmartFolderId]);

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
      setSelectedResourceTags((prev) => prev.filter((t) => t.tag?.id !== tagId));
    } catch { /* ignore */ }
  }, [selectedResource]);

  // ─── Sort ────────────────────────────────────────────

  const currentItems = activeTab === 'recycle' ? trashedResources : resources;

  const sortedItems = useMemo(() => {
    const items = [...currentItems];
    switch (sortBy) {
      case 'newest':
        return items.sort((a, b) => new Date(b.resource?.created_at ?? b.created_at).getTime() - new Date(a.resource?.created_at ?? a.created_at).getTime());
      case 'oldest':
        return items.sort((a, b) => new Date(a.resource?.created_at ?? a.created_at).getTime() - new Date(b.resource?.created_at ?? b.created_at).getTime());
      case 'name-az':
        return items.sort((a, b) => (a.resource?.filename ?? '').localeCompare(b.resource?.filename ?? ''));
      case 'name-za':
        return items.sort((a, b) => (b.resource?.filename ?? '').localeCompare(a.resource?.filename ?? ''));
      case 'largest':
        return items.sort((a, b) => (b.resource?.file_size_bytes ?? 0) - (a.resource?.file_size_bytes ?? 0));
      case 'smallest':
        return items.sort((a, b) => (a.resource?.file_size_bytes ?? 0) - (b.resource?.file_size_bytes ?? 0));
      default:
        return items;
    }
  }, [currentItems, sortBy]);

  // Sort options
  const sortOptions: { value: SortBy; label: string }[] = [
    { value: 'newest', label: t('resources.sortNewest') },
    { value: 'oldest', label: t('resources.sortOldest') },
    { value: 'name-az', label: t('resources.sortNameAZ') },
    { value: 'name-za', label: t('resources.sortNameZA') },
    { value: 'largest', label: t('resources.sortLargest') },
    { value: 'smallest', label: t('resources.sortSmallest') },
  ];

  const currentSortLabel = sortOptions.find((o) => o.value === sortBy)?.label ?? '';

  // ─── Render ──────────────────────────────────────────

  const isFilesTab = activeTab === 'files';
  const isRecycleTab = activeTab === 'recycle';
  const isSharedTab = activeTab === 'shared';

  const tabs: { key: ActiveTab; label: string; icon: React.ReactNode }[] = [
    { key: 'files', label: t('resources.files'), icon: <FolderOpen size={14} /> },
    { key: 'shared', label: t('resources.shared'), icon: <Share2 size={14} /> },
    { key: 'recycle', label: t('resources.recycleBin'), icon: <Trash2 size={14} /> },
  ];

  return (
    <div className="flex h-full animate-in fade-in duration-300">
      {/* ── Left panel: Folder tree (only on files tab) ── */}
      {isFilesTab && (
        <div className="w-56 shrink-0 border-r border-zinc-800 flex flex-col py-3">
          {/* Root item */}
          <button
            onClick={() => {
              setSelectedFolderId(null);
              setSelectedSmartFolderId(null);
            }}
            className={`
              w-full flex items-center gap-2 px-3 py-1.5 text-sm rounded-md transition-colors text-left
              ${selectedFolderId === null && !selectedSmartFolderId ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200'}
            `}
          >
            <FolderOpen size={14} className="shrink-0" />
            <span>{t('resources.allResources')}</span>
          </button>

          {/* Folder list */}
          <div className="flex-1 overflow-y-auto mt-1 space-y-0.5">
            {foldersLoading ? (
              <div className="flex items-center justify-center py-6">
                <Loader2 size={16} className="animate-spin text-zinc-500" />
              </div>
            ) : (
              folderTree.map((folder) => (
                <FolderTreeItem
                  key={folder.id}
                  folder={folder}
                  selectedFolderId={selectedFolderId}
                  onSelect={(id) => {
                    setSelectedFolderId(id);
                    setSelectedSmartFolderId(null);
                  }}
                />
              ))
            )}

            {/* Smart folders */}
            {smartFolders.length > 0 && (
              <>
                <div className="px-3 py-1 mt-2">
                  <span className="text-[10px] uppercase tracking-wider text-zinc-600 font-medium">
                    {t('resources.smartFolders')}
                  </span>
                </div>
                {smartFolders.map((sf) => (
                  <button
                    key={sf.id}
                    onClick={() => {
                      setSelectedSmartFolderId(String(sf.id));
                      setSelectedFolderId(null);
                    }}
                    className={`
                      w-full flex items-center gap-2 px-3 py-1.5 text-sm rounded-md transition-colors text-left
                      ${selectedSmartFolderId === String(sf.id) ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200'}
                    `}
                  >
                    <span className="shrink-0 text-xs">{sf.icon || '⚡'}</span>
                    <span className="truncate">{sf.name}</span>
                  </button>
                ))}
              </>
            )}
          </div>

          {/* New folder */}
          <div className="mt-2 px-2">
            {creatingFolder ? (
              <div className="flex items-center gap-1.5">
                <input
                  ref={newFolderInputRef}
                  type="text"
                  value={newFolderName}
                  onChange={(e) => setNewFolderName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleCreateFolder();
                    if (e.key === 'Escape') {
                      setCreatingFolder(false);
                      setNewFolderName('');
                    }
                  }}
                  placeholder={t('resources.folderName')}
                  disabled={savingFolder}
                  className="flex-1 min-w-0 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 disabled:opacity-50"
                />
                {savingFolder && (
                  <Loader2 size={12} className="animate-spin text-zinc-400" />
                )}
              </div>
            ) : (
              <button
                onClick={() => setCreatingFolder(true)}
                className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50 rounded-md transition-colors"
              >
                <FolderPlus size={14} />
                <span>{t('resources.newFolder')}</span>
              </button>
            )}
          </div>
        </div>
      )}

      {/* ── Center panel: Main content ── */}
      <div className="flex-1 min-w-0 flex flex-col">
        {/* Toolbar */}
        <div className="flex items-center justify-between px-6 py-3 border-b border-zinc-800">
          {/* Left: Tabs */}
          <div className="flex items-center gap-1">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg transition-colors ${
                  activeTab === tab.key
                    ? 'bg-zinc-800 text-zinc-100 font-medium'
                    : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50'
                }`}
              >
                {tab.icon}
                <span>{tab.label}</span>
              </button>
            ))}
          </div>

          {/* Right: Sort + View Toggle + Upload + Count */}
          <div className="flex items-center gap-2">
            {/* Sort dropdown */}
            <div className="relative">
              <button
                onClick={() => setShowSortMenu(!showSortMenu)}
                className="flex items-center gap-1 px-2.5 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 rounded-lg transition-colors"
              >
                <span>{currentSortLabel}</span>
                <ChevronDown size={12} />
              </button>
              {showSortMenu && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setShowSortMenu(false)} />
                  <div className="absolute right-0 top-full mt-1 z-20 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl py-1 w-40">
                    {sortOptions.map((opt) => (
                      <button
                        key={opt.value}
                        onClick={() => { setSortBy(opt.value); setShowSortMenu(false); }}
                        className={`w-full text-left px-3 py-1.5 text-xs transition-colors ${
                          sortBy === opt.value
                            ? 'bg-zinc-800 text-indigo-400'
                            : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
                        }`}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>

            {/* View toggle */}
            <div className="flex bg-zinc-800 rounded-lg p-0.5">
              <button
                onClick={() => setViewMode('grid')}
                className={`p-1.5 rounded-md transition-colors ${
                  viewMode === 'grid' ? 'bg-zinc-700 text-white' : 'text-zinc-400 hover:text-zinc-200'
                }`}
              >
                <LayoutGrid size={14} />
              </button>
              <button
                onClick={() => setViewMode('list')}
                className={`p-1.5 rounded-md transition-colors ${
                  viewMode === 'list' ? 'bg-zinc-700 text-white' : 'text-zinc-400 hover:text-zinc-200'
                }`}
              >
                <LayoutList size={14} />
              </button>
            </div>

            {/* Upload button (only on files tab) */}
            {isFilesTab && (
              <>
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  className="hidden"
                  onChange={(e) => {
                    if (e.target.files?.length) {
                      handleUpload(e.target.files);
                      e.target.value = '';
                    }
                  }}
                />
                <button
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploading}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-800 text-white rounded-lg transition-colors"
                >
                  {uploading ? (
                    <>
                      <Loader2 size={14} className="animate-spin" />
                      <span>{uploadProgress}%</span>
                    </>
                  ) : (
                    <>
                      <Upload size={14} />
                      <span>{t('resources.upload')}</span>
                    </>
                  )}
                </button>
              </>
            )}

            {/* Count badge */}
            {!loading && (
              <span className="bg-zinc-800 text-zinc-400 rounded-full px-2.5 py-0.5 text-xs font-medium">
                {sortedItems.length}
              </span>
            )}
          </div>
        </div>

        {/* Content area */}
        <div
          className={`flex-1 overflow-y-auto p-6 transition-colors ${
            dragOver ? 'bg-indigo-900/10 ring-2 ring-inset ring-indigo-500/30' : ''
          }`}
          onDragOver={isFilesTab ? handleDragOver : undefined}
          onDragLeave={isFilesTab ? handleDragLeave : undefined}
          onDrop={isFilesTab ? handleDrop : undefined}
        >
          {/* Upload progress */}
          {uploading && (
            <div className="mb-4">
              <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-indigo-500 transition-all duration-300"
                  style={{ width: `${uploadProgress}%` }}
                />
              </div>
            </div>
          )}

          {/* Shared tab placeholder */}
          {isSharedTab && (
            <div className="flex flex-col items-center justify-center h-full min-h-[300px] text-center">
              <Share2 size={48} className="text-zinc-600 mb-4" />
              <p className="text-zinc-400 text-sm">{t('resources.sharedComingSoon')}</p>
            </div>
          )}

          {/* Files / Recycle content */}
          {!isSharedTab && (
            loading ? (
              viewMode === 'grid' ? <SkeletonGrid /> : <SkeletonList />
            ) : sortedItems.length > 0 ? (
              viewMode === 'grid' ? (
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
                  {sortedItems.map((item) => (
                    <ResourceCard
                      key={item.id}
                      item={item}
                      onClick={() => handleResourceClick(item)}
                      viewMode="grid"
                      isSelected={selectedResource?.id === item.id}
                      showRestoreAction={isRecycleTab}
                      onTrash={isRecycleTab ? undefined : handleTrash}
                      onRestore={isRecycleTab ? handleRestore : undefined}
                      onPermanentDelete={isRecycleTab ? handlePermanentDelete : undefined}
                    />
                  ))}
                </div>
              ) : (
                <div className="space-y-2">
                  {sortedItems.map((item) => (
                    <ResourceCard
                      key={item.id}
                      item={item}
                      onClick={() => handleResourceClick(item)}
                      viewMode="list"
                      isSelected={selectedResource?.id === item.id}
                      showRestoreAction={isRecycleTab}
                      onTrash={isRecycleTab ? undefined : handleTrash}
                      onRestore={isRecycleTab ? handleRestore : undefined}
                      onPermanentDelete={isRecycleTab ? handlePermanentDelete : undefined}
                    />
                  ))}
                </div>
              )
            ) : (
              /* Empty states */
              <div className="flex flex-col items-center justify-center h-full min-h-[300px] text-center">
                {isRecycleTab ? (
                  <>
                    <Trash2 size={48} className="text-zinc-600 mb-4" />
                    <p className="text-zinc-400 text-sm">{t('resources.recycleBinEmpty')}</p>
                  </>
                ) : dragOver ? (
                  <>
                    <Upload size={48} className="text-indigo-400 mb-4" />
                    <p className="text-indigo-300 text-sm">{t('resources.dropToUpload')}</p>
                  </>
                ) : (
                  <>
                    <FolderOpen size={48} className="text-zinc-600 mb-4" />
                    <p className="text-zinc-400 text-sm">{t('resources.noResources')}</p>
                    <p className="text-zinc-500 text-xs mt-1">{t('resources.noResourcesHint')}</p>
                  </>
                )}
              </div>
            )
          )}
        </div>
      </div>

      {/* ── Right panel: Resource Info ── */}
      {selectedResource?.resource && (
        <ResourceInfoPanel
          resource={selectedResource.resource}
          allTags={allTags}
          assignedTags={selectedResourceTags}
          onClose={() => setSelectedResource(null)}
          onAddTag={handleAddTag}
          onRemoveTag={handleRemoveTag}
        />
      )}
    </div>
  );
};
