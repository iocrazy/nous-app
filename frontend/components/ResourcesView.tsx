import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  FolderOpen,
  FolderPlus,
  File,
  Film,
  Image,
  FileText,
  Loader2,
  Upload,
  Trash2,
  RotateCcw,
  X,
  Tag as TagIcon,
} from 'lucide-react';
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

// ─── Props ─────────────────────────────────────────────

interface ResourcesViewProps {
  scopeType: 'personal' | 'team';
  scopeId: string;
}

// ─── Helpers ───────────────────────────────────────────

function formatFileSize(bytes: number | null | undefined): string {
  if (!bytes) return '--';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '--';
  const d = new Date(dateStr);
  return d.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

function fileTypeIcon(mimeType: string | null | undefined) {
  if (!mimeType) return <File size={24} className="text-zinc-500" />;
  if (mimeType.startsWith('video/'))
    return <Film size={24} className="text-indigo-400" />;
  if (mimeType.startsWith('image/'))
    return <Image size={24} className="text-emerald-400" />;
  if (
    mimeType.startsWith('text/') ||
    mimeType.includes('pdf') ||
    mimeType.includes('document')
  )
    return <FileText size={24} className="text-amber-400" />;
  return <File size={24} className="text-zinc-500" />;
}

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

// ─── Skeleton Card ─────────────────────────────────────

const SkeletonCard: React.FC = () => (
  <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden animate-pulse">
    <div className="aspect-video bg-zinc-800" />
    <div className="p-4 space-y-2">
      <div className="h-4 bg-zinc-800 rounded w-3/4" />
      <div className="h-3 bg-zinc-800 rounded w-1/2" />
    </div>
  </div>
);

// ─── Resource Card ─────────────────────────────────────

interface ResourceCardProps {
  item: ResourceItem;
  showTrashAction?: boolean;
  showRestoreAction?: boolean;
  allTags?: Tag[];
  onTrash?: (resourceId: string) => void;
  onRestore?: (resourceId: string) => void;
  onPermanentDelete?: (resourceId: string) => void;
  onTagsChanged?: () => void;
}

const ResourceCard: React.FC<ResourceCardProps> = ({
  item,
  showTrashAction = true,
  showRestoreAction = false,
  allTags = [],
  onTrash,
  onRestore,
  onPermanentDelete,
  onTagsChanged,
}) => {
  const resource = item.resource;
  const filename = resource?.filename ?? 'Untitled';
  const mimeType = resource?.mime_type ?? null;
  const fileSize = resource?.file_size_bytes ?? null;
  const createdAt = resource?.created_at ?? item.created_at;

  const [tags, setTags] = useState<Array<{ tag: Tag }>>([]);
  const [showTagPicker, setShowTagPicker] = useState(false);

  // Load tags for this resource
  useEffect(() => {
    if (!resource?.id) return;
    fetchResourceTags(resource.id).then(setTags).catch(() => {});
  }, [resource?.id]);

  const handleAddTag = async (tagId: string) => {
    if (!resource?.id) return;
    try {
      await addResourceTag(resource.id, tagId);
      const updated = await fetchResourceTags(resource.id);
      setTags(updated);
      onTagsChanged?.();
    } catch { /* ignore */ }
  };

  const handleRemoveTag = async (tagId: string) => {
    if (!resource?.id) return;
    try {
      await removeResourceTag(resource.id, tagId);
      setTags((prev) => prev.filter((t) => t.tag?.id !== tagId));
      onTagsChanged?.();
    } catch { /* ignore */ }
  };

  const assignedTagIds = new Set(tags.map((t) => t.tag?.id).filter(Boolean));

  return (
    <div className="bg-zinc-900 border border-zinc-800 hover:border-zinc-700 rounded-xl overflow-hidden transition-all duration-200 group relative">
      {/* Thumbnail placeholder */}
      <div className="aspect-video bg-zinc-800 flex items-center justify-center">
        {fileTypeIcon(mimeType)}
      </div>
      {/* Actions overlay */}
      <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        {showTrashAction && !showRestoreAction && (
          <button
            onClick={(e) => { e.stopPropagation(); setShowTagPicker(!showTagPicker); }}
            className="p-1.5 bg-zinc-900/80 hover:bg-indigo-900/80 rounded-lg text-zinc-400 hover:text-indigo-400 transition-colors"
            title="Manage tags"
          >
            <TagIcon size={14} />
          </button>
        )}
        {showTrashAction && onTrash && resource && (
          <button
            onClick={(e) => { e.stopPropagation(); onTrash(resource.id); }}
            className="p-1.5 bg-zinc-900/80 hover:bg-red-900/80 rounded-lg text-zinc-400 hover:text-red-400 transition-colors"
            title="Move to trash"
          >
            <Trash2 size={14} />
          </button>
        )}
        {showRestoreAction && onRestore && resource && (
          <button
            onClick={(e) => { e.stopPropagation(); onRestore(resource.id); }}
            className="p-1.5 bg-zinc-900/80 hover:bg-emerald-900/80 rounded-lg text-zinc-400 hover:text-emerald-400 transition-colors"
            title="Restore"
          >
            <RotateCcw size={14} />
          </button>
        )}
        {showRestoreAction && onPermanentDelete && resource && (
          <button
            onClick={(e) => { e.stopPropagation(); onPermanentDelete(resource.id); }}
            className="p-1.5 bg-zinc-900/80 hover:bg-red-900/80 rounded-lg text-zinc-400 hover:text-red-400 transition-colors"
            title="Delete permanently"
          >
            <X size={14} />
          </button>
        )}
      </div>
      {/* Tag picker popup */}
      {showTagPicker && (
        <div className="absolute top-12 right-2 z-10 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl p-2 w-48 max-h-48 overflow-y-auto">
          {allTags.length === 0 ? (
            <p className="text-xs text-zinc-500 p-1">No tags available</p>
          ) : (
            allTags.map((tag) => (
              <button
                key={tag.id}
                onClick={(e) => {
                  e.stopPropagation();
                  if (assignedTagIds.has(tag.id)) {
                    handleRemoveTag(tag.id);
                  } else {
                    handleAddTag(tag.id);
                  }
                }}
                className={`w-full flex items-center gap-2 px-2 py-1 text-xs rounded transition-colors ${
                  assignedTagIds.has(tag.id)
                    ? 'bg-indigo-900/30 text-indigo-300'
                    : 'text-zinc-400 hover:bg-zinc-800'
                }`}
              >
                <span
                  className="w-2 h-2 rounded-full shrink-0"
                  style={{ backgroundColor: tag.color || '#6366f1' }}
                />
                <span className="truncate">{tag.name}</span>
              </button>
            ))
          )}
        </div>
      )}
      {/* Info */}
      <div className="p-4">
        <p className="text-sm font-medium text-zinc-200 group-hover:text-zinc-100 transition-colors truncate">
          {filename}
        </p>
        <p className="text-xs text-zinc-500 mt-1">
          {formatFileSize(fileSize)} &middot; {formatDate(createdAt)}
        </p>
        {/* Tag chips */}
        {tags.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-2">
            {tags.map((t) => t.tag && (
              <span
                key={t.tag.id}
                className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] rounded-full"
                style={{
                  backgroundColor: (t.tag.color || '#6366f1') + '20',
                  color: t.tag.color || '#6366f1',
                }}
              >
                {t.tag.name}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

// ─── Main Component ────────────────────────────────────

export const ResourcesView: React.FC<ResourcesViewProps> = ({
  scopeType,
  scopeId,
}) => {
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
  const [showTrash, setShowTrash] = useState(false);
  const [trashedResources, setTrashedResources] = useState<ResourceItem[]>([]);

  // Tags
  const [allTags, setAllTags] = useState<Tag[]>([]);

  // Smart folders
  const [smartFolders, setSmartFolders] = useState<SmartCollection[]>([]);
  const [selectedSmartFolderId, setSelectedSmartFolderId] = useState<string | null>(null);

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
    loadFolders();
    fetchTags().then(setAllTags).catch(() => {});
    fetchSmartFolders(scopeType, scopeId).then(setSmartFolders).catch(() => {});
  }, [loadFolders, scopeType, scopeId]);

  // ─── Load resources on folder change ─────────────────

  useEffect(() => {
    let cancelled = false;

    const loadResources = async () => {
      if (showTrash) return; // Trash has its own loading

      setLoading(true);
      try {
        let items: ResourceItem[];

        if (selectedSmartFolderId) {
          // Smart folder: query by rules
          const sf = smartFolders.find((s) => String(s.id) === selectedSmartFolderId);
          if (sf) {
            items = await fetchSmartFolderResources(scopeType, scopeId, sf.rules as any);
          } else {
            items = [];
          }
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
    return () => {
      cancelled = true;
    };
  }, [scopeType, scopeId, selectedFolderId, selectedSmartFolderId, smartFolders, showTrash]);

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
      // Keep input open on error so user can retry
    } finally {
      setSavingFolder(false);
    }
  };

  // ─── Upload handler ─────────────────────────────────

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
      // Reload resources after upload
      const items = await fetchResources(scopeType, scopeId, selectedFolderId);
      setResources(items);
    } catch {
      // Upload failed — resources stay unchanged
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  }, [scopeType, scopeId, selectedFolderId, uploading]);

  // ─── Drag & drop handlers ──────────────────────────

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
    if (e.dataTransfer.files.length) {
      handleUpload(e.dataTransfer.files);
    }
  }, [handleUpload]);

  // ─── Trash handlers ────────────────────────────────

  const handleTrash = useCallback(async (resourceId: string) => {
    try {
      await trashResource(resourceId);
      setResources((prev) => prev.filter((r) => r.resource?.id !== resourceId));
    } catch { /* ignore */ }
  }, []);

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

  const loadTrashedResources = useCallback(async () => {
    try {
      const items = await fetchTrashedResources(scopeType, scopeId);
      setTrashedResources(items);
    } catch {
      setTrashedResources([]);
    }
  }, [scopeType, scopeId]);

  useEffect(() => {
    if (showTrash) {
      loadTrashedResources();
    }
  }, [showTrash, loadTrashedResources]);

  // ─── Derive selected folder name ─────────────────────

  const selectedFolderName = showTrash
    ? 'Recycle Bin'
    : selectedSmartFolderId
      ? smartFolders.find((s) => String(s.id) === selectedSmartFolderId)?.name ?? 'Smart Folder'
      : selectedFolderId
        ? folders.find((f) => f.id === selectedFolderId)?.name ?? 'Folder'
        : 'All Resources';

  // ─── Render ──────────────────────────────────────────

  return (
    <div className="flex h-full animate-in fade-in duration-300">
      {/* ── Left panel: Folder tree ── */}
      <div className="w-56 shrink-0 border-r border-zinc-800 flex flex-col py-3">
        {/* Root item */}
        <button
          onClick={() => {
            setSelectedFolderId(null);
            setSelectedSmartFolderId(null);
            setShowTrash(false);
          }}
          className={`
            w-full flex items-center gap-2 px-3 py-1.5 text-sm rounded-md transition-colors text-left mx-0
            ${selectedFolderId === null && !selectedSmartFolderId && !showTrash ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200'}
          `}
        >
          <FolderOpen size={14} className="shrink-0" />
          <span>All Resources</span>
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
                  setShowTrash(false);
                }}
              />
            ))
          )}

          {/* Smart folders */}
          {smartFolders.length > 0 && (
            <>
              <div className="px-3 py-1 mt-2">
                <span className="text-[10px] uppercase tracking-wider text-zinc-600 font-medium">Smart Folders</span>
              </div>
              {smartFolders.map((sf) => (
                <button
                  key={sf.id}
                  onClick={() => {
                    setSelectedSmartFolderId(String(sf.id));
                    setSelectedFolderId(null);
                    setShowTrash(false);
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

        {/* New folder input / button */}
        <div className="mt-2 px-2 space-y-0.5">
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
                placeholder="Folder name"
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
              <span>New Folder</span>
            </button>
          )}

          {/* Recycle bin */}
          <button
            onClick={() => {
              setShowTrash(!showTrash);
              if (!showTrash) {
                setSelectedFolderId(null);
                setSelectedSmartFolderId(null);
              }
            }}
            className={`
              w-full flex items-center gap-2 px-3 py-1.5 text-sm rounded-md transition-colors text-left
              ${showTrash ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50'}
            `}
          >
            <Trash2 size={14} />
            <span>Recycle Bin</span>
          </button>
        </div>
      </div>

      {/* ── Right panel: Resource grid ── */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header bar */}
        <div className="flex items-center justify-between px-6 py-3 border-b border-zinc-800">
          <h2 className="text-lg font-semibold text-zinc-100 truncate">
            {selectedFolderName}
          </h2>
          <div className="flex items-center gap-2">
            {!showTrash && (
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
                      <span>Upload</span>
                    </>
                  )}
                </button>
              </>
            )}
            {!loading && (
              <span className="bg-zinc-800 text-zinc-400 rounded-full px-2.5 py-0.5 text-xs font-medium">
                {showTrash ? trashedResources.length : resources.length}
              </span>
            )}
          </div>
        </div>

        {/* Content area */}
        <div
          className={`flex-1 overflow-y-auto p-6 transition-colors ${dragOver ? 'bg-indigo-900/10 ring-2 ring-inset ring-indigo-500/30' : ''}`}
          onDragOver={!showTrash ? handleDragOver : undefined}
          onDragLeave={!showTrash ? handleDragLeave : undefined}
          onDrop={!showTrash ? handleDrop : undefined}
        >
          {/* Upload progress bar */}
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

          {showTrash ? (
            /* Recycle bin content */
            trashedResources.length > 0 ? (
              <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
                {trashedResources.map((item) => (
                  <ResourceCard
                    key={item.id}
                    item={item}
                    showTrashAction={false}
                    showRestoreAction={true}
                    onRestore={handleRestore}
                    onPermanentDelete={handlePermanentDelete}
                  />
                ))}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-full min-h-[300px] text-center">
                <Trash2 size={48} className="text-zinc-600 mb-4" />
                <p className="text-zinc-400 text-sm">Recycle bin is empty</p>
              </div>
            )
          ) : loading ? (
            /* Loading skeleton */
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
              {Array.from({ length: 6 }).map((_, i) => (
                <SkeletonCard key={i} />
              ))}
            </div>
          ) : resources.length > 0 ? (
            /* Resource grid */
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
              {resources.map((item) => (
                <ResourceCard
                  key={item.id}
                  item={item}
                  allTags={allTags}
                  onTrash={handleTrash}
                />
              ))}
            </div>
          ) : (
            /* Empty state */
            <div className="flex flex-col items-center justify-center h-full min-h-[300px] text-center">
              {dragOver ? (
                <>
                  <Upload size={48} className="text-indigo-400 mb-4" />
                  <p className="text-indigo-300 text-sm">Drop files to upload</p>
                </>
              ) : (
                <>
                  <FolderOpen size={48} className="text-zinc-600 mb-4" />
                  <p className="text-zinc-400 text-sm">No resources yet</p>
                  <p className="text-zinc-500 text-xs mt-1">
                    Drag & drop files or click Upload to add resources
                  </p>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
