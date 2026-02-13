import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  FolderOpen,
  FolderPlus,
  File,
  Film,
  Image,
  FileText,
  Loader2,
} from 'lucide-react';
import { Folder, ResourceItem } from '../types';
import {
  fetchFolders,
  buildFolderTree,
  fetchResources,
  createFolder,
} from '../services/resourceService';

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
}

const ResourceCard: React.FC<ResourceCardProps> = ({ item }) => {
  const resource = item.resource;
  const filename = resource?.filename ?? 'Untitled';
  const mimeType = resource?.mime_type ?? null;
  const fileSize = resource?.file_size_bytes ?? null;
  const createdAt = resource?.created_at ?? item.created_at;

  return (
    <div className="bg-zinc-900 border border-zinc-800 hover:border-zinc-700 rounded-xl overflow-hidden transition-all duration-200 group">
      {/* Thumbnail placeholder */}
      <div className="aspect-video bg-zinc-800 flex items-center justify-center">
        {fileTypeIcon(mimeType)}
      </div>
      {/* Info */}
      <div className="p-4">
        <p className="text-sm font-medium text-zinc-200 group-hover:text-zinc-100 transition-colors truncate">
          {filename}
        </p>
        <p className="text-xs text-zinc-500 mt-1">
          {formatFileSize(fileSize)} &middot; {formatDate(createdAt)}
        </p>
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
    loadFolders();
  }, [loadFolders]);

  // ─── Load resources on folder change ─────────────────

  useEffect(() => {
    let cancelled = false;

    const loadResources = async () => {
      setLoading(true);
      try {
        const items = await fetchResources(
          scopeType,
          scopeId,
          selectedFolderId
        );
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
  }, [scopeType, scopeId, selectedFolderId]);

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

  // ─── Derive selected folder name ─────────────────────

  const selectedFolderName = selectedFolderId
    ? folders.find((f) => f.id === selectedFolderId)?.name ?? 'Folder'
    : 'All Resources';

  // ─── Render ──────────────────────────────────────────

  return (
    <div className="flex h-full animate-in fade-in duration-300">
      {/* ── Left panel: Folder tree ── */}
      <div className="w-56 shrink-0 border-r border-zinc-800 flex flex-col py-3">
        {/* Root item */}
        <button
          onClick={() => setSelectedFolderId(null)}
          className={`
            w-full flex items-center gap-2 px-3 py-1.5 text-sm rounded-md transition-colors text-left mx-0
            ${selectedFolderId === null ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200'}
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
                onSelect={setSelectedFolderId}
              />
            ))
          )}
        </div>

        {/* New folder input / button */}
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
        </div>
      </div>

      {/* ── Right panel: Resource grid ── */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header bar */}
        <div className="flex items-center justify-between px-6 py-3 border-b border-zinc-800">
          <h2 className="text-lg font-semibold text-zinc-100 truncate">
            {selectedFolderName}
          </h2>
          {!loading && (
            <span className="bg-zinc-800 text-zinc-400 rounded-full px-2.5 py-0.5 text-xs font-medium">
              {resources.length}
            </span>
          )}
        </div>

        {/* Content area */}
        <div className="flex-1 overflow-y-auto p-6">
          {loading ? (
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
                <ResourceCard key={item.id} item={item} />
              ))}
            </div>
          ) : (
            /* Empty state */
            <div className="flex flex-col items-center justify-center h-full min-h-[300px] text-center">
              <FolderOpen size={48} className="text-zinc-600 mb-4" />
              <p className="text-zinc-400 text-sm">No resources yet</p>
              <p className="text-zinc-500 text-xs mt-1">
                Resources added to this{' '}
                {selectedFolderId ? 'folder' : 'scope'} will appear here
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
