import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  FolderOpen,
  Loader2,
  Upload,
  Trash2,
  LayoutGrid,
  LayoutList,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Share2,
  Download,
  Plus,
  Zap,
  FolderPlus,
  FolderSearch,
  Check,
  X,
  UploadCloud,
  BookOpen,
  ExternalLink,
  Pencil,
  Copy,
  Move,
  RefreshCw,
  Filter,
  Search,
  FileText,
  Table2,
  Presentation,
  Globe,
  Sparkles,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { RipVaultView } from './RipVaultView';
import { usePermission } from '../hooks/usePermission';
import { Folder, ResourceItem, Tag, SmartCollection, Library } from '../types';
import {
  fetchFolders,
  fetchChildFolders,
  fetchResources,
  createFolder,
  uploadResource,
  trashResource,
  restoreResource,
  permanentDeleteResource,
  fetchTrashedResources,
  fetchDownloadedResources,
  fetchResourceTags,
  addResourceTag,
  removeResourceTag,
  fetchSmartFolders,
  fetchSmartFolderResults,
  createSmartFolder,
  updateSmartFolder,
  deleteSmartFolder,
  renameFolder,
  moveResourceItem,
  moveResourceItems,
  copyResourceItem,
  moveFolder,
  trashResources,
  renameResource,
  getFolderPreview,
} from '../services/resourceService';
import type { SmartFolderRules } from '../services/resourceService';
import { fetchLibraries, createLibrary } from '../services/libraryService';
import { fetchTags } from '../services/tagsService';
import { ResourceCard } from './ResourceCard';
import { FolderCard } from './FolderCard';
import { ResourceInfoPanel } from './ResourceInfoPanel';
import { ContextMenu, ContextMenuItem } from './ContextMenu';
import { Breadcrumb, BreadcrumbSegment } from './Breadcrumb';
import { SmartFolderEditor } from './SmartFolderEditor';
import { ShareModal } from './ShareModal';
import { FolderPickerModal } from './FolderPickerModal';
import { SidebarFolderTree } from './SidebarFolderTree';
import { useToast } from './Toast';
import { useFileKeyboard } from '../hooks/useFileKeyboard';
import { useUpload, type UploadFileProgress } from '../contexts/UploadContext';

// ─── Upload constants ────────────────────────────────────

const BLOCKED_EXTENSIONS = new Set([
  '.exe', '.bat', '.cmd', '.msi', '.scr', '.pif', '.com',
  '.sh', '.bash', '.ps1', '.vbs', '.wsf', '.jar',
]);

const MAX_FILE_SIZE = 500 * 1024 * 1024; // 500 MB

function validateFile(file: File): string | null {
  const ext = '.' + file.name.split('.').pop()?.toLowerCase();
  if (BLOCKED_EXTENSIONS.has(ext)) {
    return 'invalidFileType';
  }
  if (file.size > MAX_FILE_SIZE) {
    return 'fileTooLarge';
  }
  return null;
}

// ─── Props ─────────────────────────────────────────────

interface ResourcesViewProps {
  scopeType: 'personal' | 'team';
  scopeId: string;
}

type SidebarView = 'resources' | 'shared' | 'recycle' | 'downloads';
type SortBy = 'newest' | 'oldest' | 'name-az' | 'name-za' | 'largest' | 'smallest';


// ─── Skeleton ─────────────────────────────────────────

const SkeletonGrid: React.FC = () => (
  <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))' }}>
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
  const { teamId, section, folderId: urlFolderId, smartFolderId: urlSmartFolderId, libraryId: urlLibraryId } = useParams();
  const navigate = useNavigate();
  const resPath = (path: string) => teamId ? `/t/${teamId}${path}` : path;

  // URL-driven state
  const sidebarView: SidebarView = urlFolderId || urlSmartFolderId || urlLibraryId
    ? 'resources'
    : (['shared', 'recycle', 'downloads'].includes(section || '') ? section as SidebarView : 'resources');
  const selectedFolderId = urlFolderId ?? null;
  const selectedSmartFolderId = urlSmartFolderId ?? null;
  const selectedLibraryId = urlLibraryId ?? null;

  // Data
  const [folders, setFolders] = useState<Folder[]>([]);
  const [childFolders, setChildFolders] = useState<Folder[]>([]);
  const [folderPreviews, setFolderPreviews] = useState<Record<string, Array<{ thumbnail_path: string | null; mime_type: string | null }>>>({});
  const [resources, setResources] = useState<ResourceItem[]>([]);
  const [loading, setLoading] = useState(true);

  // New folder inline input
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [savingFolder, setSavingFolder] = useState(false);
  const newFolderInputRef = useRef<HTMLInputElement>(null);

  // Upload state (shared via context so TopBar TaskCenter can display transfers)
  const upload = useUpload();
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dragCounterRef = useRef(0);

  // Recycle bin
  const [trashedResources, setTrashedResources] = useState<ResourceItem[]>([]);
  const [pendingPermanentDelete, setPendingPermanentDelete] = useState<string | null>(null);
  const [pendingBatchPermanentDelete, setPendingBatchPermanentDelete] = useState<string[] | null>(null);

  // Downloads (parser-created resources)
  const [downloadedResources, setDownloadedResources] = useState<ResourceItem[]>([]);

  // Tags
  const [allTags, setAllTags] = useState<Tag[]>([]);

  // Libraries (team mode)
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [creatingLibrary, setCreatingLibrary] = useState(false);
  const [newLibraryName, setNewLibraryName] = useState('');
  const [savingLibrary, setSavingLibrary] = useState(false);
  const newLibraryInputRef = useRef<HTMLInputElement>(null);

  // Smart folders
  const [smartFolders, setSmartFolders] = useState<SmartCollection[]>([]);
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  const [sortBy, setSortBy] = useState<SortBy>('newest');
  const [showSortMenu, setShowSortMenu] = useState(false);

  // Filter state
  type FilterType = 'video' | 'image' | 'audio' | 'document' | 'other';
  const [activeFilters, setActiveFilters] = useState<Set<FilterType>>(new Set());
  const [showFilterPanel, setShowFilterPanel] = useState(false);

  // Search state
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Detail panel
  const [selectedResource, setSelectedResource] = useState<ResourceItem | null>(null);
  const [selectedResourceTags, setSelectedResourceTags] = useState<Array<{ tag: Tag }>>([]);
  const [showInfoPanel, setShowInfoPanel] = useState(true);
  const [infoPanelWidth, setInfoPanelWidth] = useState(320);
  const isResizingPanelRef = useRef(false);
  const resizeStartRef = useRef({ x: 0, width: 0 });

  // Multi-select mode (triggered by checkbox click)
  const [multiSelectMode, setMultiSelectMode] = useState(false);

  // Upload dropdown
  const [showUploadDropdown, setShowUploadDropdown] = useState(false);
  const uploadDropdownRef = useRef<HTMLDivElement>(null);

  // New dropdown
  const [showNewDropdown, setShowNewDropdown] = useState(false);
  const newDropdownRef = useRef<HTMLDivElement>(null);

  // Aliases for readability
  const uploading = upload.isUploading;

  // Context menu
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    type: 'file' | 'folder' | 'empty';
    target?: ResourceItem | Folder;
  } | null>(null);

  // Rename state
  const [renamingResourceId, setRenamingResourceId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renamingFolderId, setRenamingFolderId] = useState<string | null>(null);
  const [renameFolderValue, setRenameFolderValue] = useState('');

  // Smart folder editor
  const [showSmartFolderEditor, setShowSmartFolderEditor] = useState(false);
  const [editingSmartFolder, setEditingSmartFolder] = useState<SmartCollection | null>(null);

  // Share target (for ShareModal)
  const [shareTarget, setShareTarget] = useState<{
    resourceId?: string;
    folderId?: string;
    libraryId?: string;
  } | null>(null);

  // Batch selection
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [lastClickedId, setLastClickedId] = useState<string | null>(null);

  // Copy/Move operations
  const [folderPickerMode, setFolderPickerMode] = useState<'copy' | 'move' | null>(null);
  const [operationTargetItems, setOperationTargetItems] = useState<ResourceItem[]>([]);
  const [operationTargetFolders, setOperationTargetFolders] = useState<Folder[]>([]);

  // ─── Permission check ────────────────────────────────

  const permObjectType = selectedLibraryId ? 'library' : selectedFolderId ? 'folder' : null;
  const permObjectId = selectedLibraryId ?? selectedFolderId ?? null;
  const { canDo } = usePermission(
    scopeType === 'team' ? permObjectType : null,
    scopeType === 'team' ? permObjectId : null,
    scopeType === 'team' ? (teamId ?? null) : null,
  );

  // ─── Toast ────────────────────────────────────────────
  const { addToast } = useToast();

  // ─── Clipboard state for keyboard shortcuts ──────────
  const [clipboardItems, setClipboardItems] = useState<ResourceItem[]>([]);
  const [clipboardMode, setClipboardMode] = useState<'copy' | 'cut' | null>(null);

  // ─── Folder upload ref ───────────────────────────────
  const folderInputRef = useRef<HTMLInputElement>(null);

  // ─── Folder breadcrumb chain (independent of full folders array) ──
  const [folderChain, setFolderChain] = useState<Folder[]>([]);

  // ─── Derived view flags ──────────────────────────────

  const isResourcesView = sidebarView === 'resources';
  const isRecycleView = sidebarView === 'recycle';
  const isSharedView = sidebarView === 'shared';
  const isDownloadsView = sidebarView === 'downloads';
  const canUpload = isResourcesView && canDo('upload');

  // ─── Load folders on scope change ────────────────────

  const loadFolders = useCallback(async () => {
    try {
      const allFolders = await fetchFolders(scopeType, scopeId, selectedLibraryId);
      setFolders(allFolders);
    } catch {
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
    } catch {
      setChildFolders([]);
    }
  }, [scopeType, scopeId, selectedFolderId, selectedLibraryId, isResourcesView]);

  useEffect(() => {
    // State resets for folder/view/smart folder handled by URL navigation
    setSelectedResource(null);
    loadFolders();
    fetchTags().then(setAllTags).catch(() => {});
    fetchSmartFolders(scopeType, scopeId).then(setSmartFolders).catch(() => {});
    // Load libraries in team mode
    if (scopeType === 'team') {
      fetchLibraries(scopeId).then(setLibraries).catch(() => setLibraries([]));
    }
  }, [loadFolders, scopeType, scopeId]);

  // Load child folders when folder/library/view changes
  useEffect(() => {
    loadChildFolders();
  }, [loadChildFolders]);

  // Load folder previews when child folders change
  useEffect(() => {
    if (childFolders.length === 0) {
      setFolderPreviews({});
      return;
    }
    const loadPreviews = async () => {
      const previews: Record<string, Array<{ thumbnail_path: string | null; mime_type: string | null }>> = {};
      await Promise.all(
        childFolders.map(async (f) => {
          try {
            previews[f.id] = await getFolderPreview(f.id);
          } catch {
            previews[f.id] = [];
          }
        })
      );
      setFolderPreviews(previews);
    };
    loadPreviews();
  }, [childFolders]);

  // ─── Build folder breadcrumb chain ────────────────────

  useEffect(() => {
    if (!selectedFolderId) {
      setFolderChain([]);
      return;
    }

    // Try to build chain from the already-loaded folders array
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

    // Fallback: fetch folder chain directly from Supabase
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
      } catch {
        // ignore
      }
    };
    fetchChain();
    return () => { cancelled = true; };
  }, [selectedFolderId, folders]);

  // ─── Load resources on folder change ─────────────────

  useEffect(() => {
    if (sidebarView !== 'resources') return;
    let cancelled = false;

    const loadResources = async () => {
      setLoading(true);
      try {
        let items: ResourceItem[];
        if (selectedSmartFolderId) {
          items = await fetchSmartFolderResults(selectedSmartFolderId, scopeType, scopeId);
        } else {
          items = await fetchResources(scopeType, scopeId, selectedFolderId, selectedLibraryId);
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
  }, [scopeType, scopeId, selectedFolderId, selectedSmartFolderId, selectedLibraryId, sidebarView]);

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
    if (sidebarView === 'recycle') {
      setLoading(true);
      loadTrashedResources().finally(() => setLoading(false));
    }
  }, [sidebarView, loadTrashedResources]);

  // ─── Load downloaded resources ─────────────────────

  const loadDownloadedResources = useCallback(async () => {
    try {
      const items = await fetchDownloadedResources(scopeType, scopeId);
      setDownloadedResources(items);
    } catch {
      setDownloadedResources([]);
    }
  }, [scopeType, scopeId]);

  useEffect(() => {
    if (sidebarView === 'downloads') {
      setLoading(true);
      loadDownloadedResources().finally(() => setLoading(false));
    }
  }, [sidebarView, loadDownloadedResources]);

  // ─── Focus new folder/library input ─────────────────

  useEffect(() => {
    if (creatingFolder && newFolderInputRef.current) {
      newFolderInputRef.current.focus();
    }
  }, [creatingFolder]);

  useEffect(() => {
    if (creatingLibrary && newLibraryInputRef.current) {
      newLibraryInputRef.current.focus();
    }
  }, [creatingLibrary]);

  // ─── Create folder handler ───────────────────────────

  const handleCreateFolder = async () => {
    const trimmed = newFolderName.trim();
    if (!trimmed || savingFolder) return;
    setSavingFolder(true);
    try {
      await createFolder({
        name: trimmed,
        parent_id: selectedFolderId || null,
        scope_type: scopeType,
        scope_id: scopeId,
        ...(selectedLibraryId ? { library_id: selectedLibraryId } : {}),
      });
      setNewFolderName('');
      setCreatingFolder(false);
      await Promise.all([loadFolders(), loadChildFolders()]);
    } catch {
      // Keep input open on error
    } finally {
      setSavingFolder(false);
    }
  };

  // ─── Create library handler ─────────────────────────

  const handleCreateLibrary = async () => {
    const trimmed = newLibraryName.trim();
    if (!trimmed || savingLibrary) return;
    setSavingLibrary(true);
    try {
      const lib = await createLibrary({ name: trimmed, scope_id: scopeId });
      setNewLibraryName('');
      setCreatingLibrary(false);
      setLibraries((prev) => [...prev, lib]);
      navigate(resPath(`/resources/library/${lib.id}`));
    } catch {
      // Keep input open on error
    } finally {
      setSavingLibrary(false);
    }
  };

  // ─── Upload handler ──────────────────────────────────

  const handleUpload = useCallback(async (files: FileList | File[]) => {
    if (!files.length || uploading) return;

    // Validate files and build initial progress entries
    const validFiles: File[] = [];
    const initialProgress: UploadFileProgress[] = [];

    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      const validationError = validateFile(file);
      const id = `${Date.now()}-${i}`;

      if (validationError) {
        initialProgress.push({
          id,
          filename: file.name,
          percent: 0,
          status: 'error',
          error: t(`resources.${validationError}`),
          fileSize: file.size,
          bytesUploaded: 0,
          speed: 0,
        });
      } else {
        validFiles.push(file);
        initialProgress.push({
          id,
          filename: file.name,
          percent: 0,
          status: 'uploading',
          fileSize: file.size,
          bytesUploaded: 0,
          speed: 0,
        });
      }
    }

    if (validFiles.length === 0 && initialProgress.length > 0) {
      // All files failed validation — show errors briefly
      upload.setItems(initialProgress);
      return;
    }

    // Append new items to existing history (don't replace)
    upload.setItems((prev) => [...prev, ...initialProgress]);
    upload.setIsUploading(true);
    upload.setOverallProgress(0);

    const batchStartTime = Date.now();
    upload.setUploadStartTime(batchStartTime);

    let completedCount = 0;
    // Map valid files to their progress entry IDs
    const validFileEntryIds = initialProgress
      .filter((p) => p.status === 'uploading')
      .map((p) => p.id);

    for (let i = 0; i < validFiles.length; i++) {
      const file = validFiles[i];
      const entryId = validFileEntryIds[i];
      const fileStartTime = Date.now();

      try {
        await uploadResource(
          file,
          scopeType,
          scopeId,
          selectedFolderId,
          (progress) => {
            const fileEntry = initialProgress.find((p) => p.id === entryId);
            const fileSz = fileEntry?.fileSize || 0;
            const bytesUploaded = Math.round(fileSz * progress / 100);
            const elapsedSec = Math.max((Date.now() - fileStartTime) / 1000, 0.5);
            const speed = bytesUploaded > 0 ? Math.round(bytesUploaded / elapsedSec) : 0;
            upload.setItems((prev) =>
              prev.map((p) => p.id === entryId ? { ...p, percent: progress, bytesUploaded, speed } : p)
            );
            const overall = Math.round(((completedCount + progress / 100) / validFiles.length) * 100);
            upload.setOverallProgress(overall);
          },
        );

        completedCount++;
        const fileEntry = initialProgress.find((p) => p.id === entryId);
        const fileSz = fileEntry?.fileSize || 0;
        upload.setItems((prev) =>
          prev.map((p) => p.id === entryId ? { ...p, percent: 100, status: 'complete', bytesUploaded: fileSz, speed: 0 } : p)
        );
      } catch {
        upload.setItems((prev) =>
          prev.map((p) => p.id === entryId
            ? { ...p, status: 'error', error: t('resources.uploadFailed') }
            : p
          )
        );
      }
    }

    // Refresh resource list
    try {
      const items = await fetchResources(scopeType, scopeId, selectedFolderId);
      setResources(items);
    } catch { /* ignore */ }

    upload.setIsUploading(false);
    upload.setOverallProgress(0);
  }, [scopeType, scopeId, selectedFolderId, uploading, t, upload]);

  // ─── Drag & drop (robust nested-element handling) ────

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current++;
    if (e.dataTransfer.types.includes('Files')) {
      setDragOver(true);
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current--;
    if (dragCounterRef.current === 0) {
      setDragOver(false);
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = 0;
    setDragOver(false);
    if (e.dataTransfer.files.length) handleUpload(e.dataTransfer.files);
  }, [handleUpload]);

  // ─── Trash handlers ──────────────────────────────────

  const handleTrash = useCallback(async (resourceId: string) => {
    try {
      const item = resources.find((r) => r.resource?.id === resourceId);
      await trashResource(resourceId, scopeType, scopeId);
      setResources((prev) => prev.filter((r) => r.resource?.id !== resourceId));
      if (selectedResource?.resource?.id === resourceId) setSelectedResource(null);
      const filename = item?.resource?.filename || '';
      addToast(t('resources.trashedNotification', { name: filename }), 'success');
    } catch { /* ignore */ }
  }, [selectedResource, scopeType, scopeId, resources, addToast, t]);

  const handleRestore = useCallback(async (resourceId: string) => {
    try {
      await restoreResource(resourceId);
      setTrashedResources((prev) => prev.filter((r) => r.resource?.id !== resourceId));
    } catch { /* ignore */ }
  }, []);

  const handlePermanentDelete = useCallback((resourceId: string) => {
    setPendingPermanentDelete(resourceId);
  }, []);

  const confirmPermanentDelete = useCallback(async () => {
    const ids = pendingBatchPermanentDelete || (pendingPermanentDelete ? [pendingPermanentDelete] : []);
    if (ids.length === 0) return;
    try {
      for (const id of ids) {
        await permanentDeleteResource(id);
      }
      setTrashedResources((prev) => prev.filter((r) => !ids.includes(String(r.resource?.id))));
      setSelectedIds(new Set());
      addToast(t('resources.permanentDeleteSuccess'), 'success');
    } catch {
      addToast(t('resources.permanentDeleteFailed'), 'error');
    }
    setPendingPermanentDelete(null);
    setPendingBatchPermanentDelete(null);
  }, [pendingPermanentDelete, pendingBatchPermanentDelete, addToast, t]);

  // ─── Resource selection & detail panel ───────────────

  const handleResourceClick = useCallback((item: ResourceItem) => {
    // Single click: toggle — click same item to deselect
    if (selectedResource?.id === item.id) {
      setSelectedResource(null);
      setSelectedIds(new Set());
    } else {
      setSelectedResource(item);
    }
  }, [selectedResource]);

  // Double click: navigate to detail page
  const handleResourceDoubleClick = useCallback((item: ResourceItem) => {
    if (item.resource?.id) {
      navigate(resPath(`/resources/file/${item.resource.id}`));
    }
  }, [navigate, resPath]);

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

  // Auto-exit multi-select when no items selected
  useEffect(() => {
    if (multiSelectMode && selectedIds.size === 0) {
      setMultiSelectMode(false);
    }
  }, [multiSelectMode, selectedIds.size]);

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

  // Resize info panel handler
  const handlePanelResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isResizingPanelRef.current = true;
    resizeStartRef.current = { x: e.clientX, width: infoPanelWidth };

    const onMouseMove = (ev: MouseEvent) => {
      if (!isResizingPanelRef.current) return;
      const delta = resizeStartRef.current.x - ev.clientX;
      const newWidth = Math.max(240, Math.min(600, resizeStartRef.current.width + delta));
      setInfoPanelWidth(newWidth);
    };

    const onMouseUp = () => {
      isResizingPanelRef.current = false;
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, [infoPanelWidth]);

  // Debounce search
  useEffect(() => {
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    searchTimerRef.current = setTimeout(() => {
      setDebouncedSearch(searchQuery);
    }, 300);
    return () => { if (searchTimerRef.current) clearTimeout(searchTimerRef.current); };
  }, [searchQuery]);

  // Clear search/filter on view/folder change
  useEffect(() => {
    setSearchQuery('');
    setDebouncedSearch('');
  }, [sidebarView, selectedFolderId, selectedSmartFolderId, selectedLibraryId]);

  // Clear selection on view/folder/library change
  useEffect(() => {
    setSelectedResource(null);
  }, [sidebarView, selectedFolderId, selectedSmartFolderId, selectedLibraryId]);

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

  // ─── Context menu handlers ─────────────────────────

  const handleFileContextMenu = useCallback((e: React.MouseEvent, item: ResourceItem) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ x: e.clientX, y: e.clientY, type: 'file', target: item });
  }, []);

  const handleFolderContextMenu = useCallback((e: React.MouseEvent, folder: Folder) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ x: e.clientX, y: e.clientY, type: 'folder', target: folder });
  }, []);

  const handleEmptyAreaContextMenu = useCallback((e: React.MouseEvent) => {
    // Only trigger when not right-clicking on a card (cards have their own context menus)
    const target = e.target as HTMLElement;
    if (!target.closest('[data-context-item]')) {
      e.preventDefault();
      setContextMenu({ x: e.clientX, y: e.clientY, type: 'empty' });
    }
  }, []);

  const closeContextMenu = useCallback(() => {
    setContextMenu(null);
  }, []);

  const handleFolderPickerConfirm = useCallback(async (targetFolderId: string | null, targetLibraryId?: string | null) => {
    try {
      if (folderPickerMode === 'move') {
        // Move folders
        for (const folder of operationTargetFolders) {
          await moveFolder(folder.id, targetFolderId, targetLibraryId);
        }
        // Move items
        if (operationTargetItems.length === 1) {
          await moveResourceItem(operationTargetItems[0].id, targetFolderId, targetLibraryId);
        } else if (operationTargetItems.length > 1) {
          await moveResourceItems(operationTargetItems.map((i) => i.id), targetFolderId, targetLibraryId);
        }
        // Reload
        await Promise.all([loadFolders(), loadChildFolders()]);
        const items = await fetchResources(scopeType, scopeId, selectedFolderId, selectedLibraryId);
        setResources(items);
        const totalMoved = operationTargetItems.length + operationTargetFolders.length;
        addToast(t('resources.moveSuccess', { count: totalMoved }), 'success');
      } else if (folderPickerMode === 'copy') {
        for (const item of operationTargetItems) {
          if (item.resource?.id) {
            await copyResourceItem(String(item.resource.id), scopeType, scopeId, targetFolderId, targetLibraryId);
          }
        }
        addToast(t('resources.copySuccess', { count: operationTargetItems.length }), 'success');
      }
    } catch {
      /* ignore */
    }
    setFolderPickerMode(null);
    setOperationTargetItems([]);
    setOperationTargetFolders([]);
  }, [folderPickerMode, operationTargetItems, operationTargetFolders, scopeType, scopeId, selectedFolderId, selectedLibraryId, loadFolders, loadChildFolders, addToast, t]);

  // ─── Handle drag-drop onto folder ──────────────────

  const handleDropOnFolder = useCallback(async (targetFolderId: string | null, droppedIds: string[]) => {
    try {
      for (const compositeId of droppedIds) {
        if (compositeId.startsWith('folder:')) {
          const fId = compositeId.replace('folder:', '');
          if (fId !== targetFolderId) {
            await moveFolder(fId, targetFolderId, selectedLibraryId);
          }
        } else if (compositeId.startsWith('item:')) {
          const itemId = compositeId.replace('item:', '');
          await moveResourceItem(itemId, targetFolderId, selectedLibraryId);
        }
      }
      // Refresh
      await Promise.all([loadFolders(), loadChildFolders()]);
      const items = await fetchResources(scopeType, scopeId, selectedFolderId, selectedLibraryId);
      setResources(items);
      setSelectedIds(new Set());
    } catch { /* ignore */ }
  }, [selectedLibraryId, scopeType, scopeId, selectedFolderId, loadFolders, loadChildFolders]);

  // ─── Sidebar drop handlers ────────────────────────

  const handleSidebarDragOver = useCallback((e: React.DragEvent) => {
    if (e.dataTransfer.types.includes('application/mediahub-items')) {
      e.preventDefault();
      e.stopPropagation();
    }
  }, []);

  const handleSidebarDrop = useCallback((e: React.DragEvent, targetFolderId: string | null) => {
    e.preventDefault();
    e.stopPropagation();
    const raw = e.dataTransfer.getData('application/mediahub-items');
    if (raw) {
      try {
        const data = JSON.parse(raw);
        if (data.ids && Array.isArray(data.ids)) {
          handleDropOnFolder(targetFolderId, data.ids);
        }
      } catch { /* ignore */ }
    }
  }, [handleDropOnFolder]);

  const handleDeleteSmartFolder = useCallback(async (sf: SmartCollection) => {
    if (!confirm(t('smartFolder.confirmDelete'))) return;
    await deleteSmartFolder(String(sf.id));
    const updated = await fetchSmartFolders(scopeType, scopeId);
    setSmartFolders(updated);
    // If we're viewing the deleted folder, go back to resources root
    if (selectedSmartFolderId === String(sf.id)) {
      navigate(resPath('/resources'));
    }
  }, [scopeType, scopeId, selectedSmartFolderId, navigate, resPath, t]);

  // Build context menu items based on type
  const contextMenuItems = useMemo((): ContextMenuItem[] => {
    if (!contextMenu) return [];

    if (contextMenu.type === 'file') {
      const item = contextMenu.target as ResourceItem;
      const resourceId = item.resource?.id;
      const filePath = item.resource?.file_path;
      const items: ContextMenuItem[] = [
        {
          label: t('resources.openInNewTab'),
          icon: <ExternalLink size={14} />,
          onClick: () => {
            if (filePath) window.open(filePath, '_blank');
          },
          disabled: !filePath,
        },
      ];
      if (canDo('download')) {
        items.push({
          label: t('resources.downloadOriginal'),
          icon: <Download size={14} />,
          onClick: () => {
            if (filePath) {
              const a = document.createElement('a');
              a.href = filePath;
              a.download = item.resource?.filename ?? 'download';
              document.body.appendChild(a);
              a.click();
              document.body.removeChild(a);
            }
          },
          disabled: !filePath,
        });
      }
      if (canDo('update')) {
        items.push({
          label: t('resources.rename'),
          icon: <Pencil size={14} />,
          onClick: () => {
            if (resourceId) {
              setRenamingResourceId(item.id);
              setRenameValue(item.resource?.filename ?? '');
            }
          },
          divider: true,
        });
      }
      if (canDo('copy')) {
        items.push({
          label: t('resources.copyTo'),
          icon: <Copy size={14} />,
          onClick: () => {
            setOperationTargetItems([item]);
            setOperationTargetFolders([]);
            setFolderPickerMode('copy');
          },
        });
      }
      if (canDo('move')) {
        items.push({
          label: t('resources.moveTo'),
          icon: <Move size={14} />,
          onClick: () => {
            setOperationTargetItems([item]);
            setOperationTargetFolders([]);
            setFolderPickerMode('move');
          },
        });
      }
      if (canDo('share')) {
        items.push({
          label: t('resources.share'),
          icon: <Share2 size={14} />,
          onClick: () => {
            setShareTarget({ resourceId: String(item.resource?.id) });
          },
        });
      }
      if (canDo('delete')) {
        items.push({
          label: t('resources.moveToTrash'),
          icon: <Trash2 size={14} />,
          onClick: () => {
            if (resourceId) handleTrash(resourceId);
          },
          danger: true,
          divider: true,
        });
      }
      return items;
    }

    if (contextMenu.type === 'folder') {
      const folder = contextMenu.target as Folder;
      const folderUrl = selectedLibraryId
        ? resPath(`/resources/library/${selectedLibraryId}/folder/${folder.id}`)
        : resPath(`/resources/folder/${folder.id}`);
      const items: ContextMenuItem[] = [
        {
          label: t('resources.openInNewTab'),
          icon: <ExternalLink size={14} />,
          onClick: () => {
            window.open(folderUrl, '_blank');
          },
        },
        {
          label: t('resources.open'),
          icon: <FolderOpen size={14} />,
          onClick: () => navigate(folderUrl),
        },
      ];
      if (canDo('update')) {
        items.push({
          label: t('resources.rename'),
          icon: <Pencil size={14} />,
          onClick: () => {
            setRenamingFolderId(folder.id);
            setRenameFolderValue(folder.name);
          },
          divider: true,
        });
      }
      if (canDo('copy')) {
        items.push({
          label: t('resources.copyTo'),
          icon: <Copy size={14} />,
          onClick: () => {
            // For folders, copy means copy contents - use move picker in copy mode
            setOperationTargetItems([]);
            setOperationTargetFolders([folder]);
            setFolderPickerMode('copy');
          },
        });
      }
      if (canDo('move')) {
        items.push({
          label: t('resources.moveTo'),
          icon: <Move size={14} />,
          onClick: () => {
            setOperationTargetItems([]);
            setOperationTargetFolders([folder]);
            setFolderPickerMode('move');
          },
        });
      }
      if (canDo('share')) {
        items.push({
          label: t('resources.share'),
          icon: <Share2 size={14} />,
          onClick: () => {
            setShareTarget({ folderId: String(folder.id) });
          },
        });
      }
      if (canDo('delete')) {
        items.push({
          label: t('resources.moveToTrash'),
          icon: <Trash2 size={14} />,
          onClick: async () => {
            try {
              const { trashFolder } = await import('../services/resourceService');
              await trashFolder(folder.id);
              await Promise.all([loadFolders(), loadChildFolders()]);
            } catch { /* ignore */ }
          },
          danger: true,
          divider: true,
        });
      }
      return items;
    }

    if ((contextMenu.type as string) === 'smartFolder') {
      const sf = contextMenu.target as unknown as SmartCollection;
      return [
        {
          label: t('smartFolder.editSmartFolder'),
          icon: <Pencil size={14} />,
          onClick: () => setEditingSmartFolder(sf),
        },
        {
          label: t('smartFolder.deleteSmartFolder'),
          icon: <Trash2 size={14} />,
          onClick: () => handleDeleteSmartFolder(sf),
          danger: true,
          divider: true,
        },
      ];
    }

    // Empty area
    const emptyItems: ContextMenuItem[] = [];
    if (canDo('upload')) {
      emptyItems.push(
        {
          label: t('resources.uploadFile'),
          icon: <Upload size={14} />,
          onClick: () => fileInputRef.current?.click(),
        },
        {
          label: t('resources.newFolder'),
          icon: <FolderPlus size={14} />,
          onClick: () => setCreatingFolder(true),
        },
      );
    }
    emptyItems.push({
      label: t('resources.refresh'),
      icon: <RefreshCw size={14} />,
      onClick: async () => {
        setLoading(true);
        try {
          await Promise.all([loadChildFolders()]);
          const items = await fetchResources(scopeType, scopeId, selectedFolderId, selectedLibraryId);
          setResources(items);
        } catch { /* ignore */ }
        setLoading(false);
      },
      divider: true,
    });
    // Paste option when there are items in clipboard (operationTargetItems from copy)
    if (operationTargetItems.length > 0 && folderPickerMode === null) {
      emptyItems.push({
        label: t('resources.paste'),
        icon: <Copy size={14} />,
        onClick: async () => {
          try {
            for (const item of operationTargetItems) {
              if (item.resource?.id) {
                await copyResourceItem(String(item.resource.id), scopeType, scopeId, selectedFolderId, selectedLibraryId);
              }
            }
            const items = await fetchResources(scopeType, scopeId, selectedFolderId, selectedLibraryId);
            setResources(items);
            setOperationTargetItems([]);
          } catch { /* ignore */ }
        },
      });
    }
    return emptyItems;
  }, [contextMenu, t, selectedLibraryId, navigate, resPath, handleTrash, handleDeleteSmartFolder, scopeType, scopeId, selectedFolderId, loadFolders, loadChildFolders, canDo]);

  // ─── Rename handlers ─────────────────────────────────

  const handleRenameResourceConfirm = useCallback(async () => {
    if (!renamingResourceId || !renameValue.trim()) {
      setRenamingResourceId(null);
      return;
    }
    const item = resources.find((r) => r.id === renamingResourceId);
    if (!item?.resource?.id) {
      setRenamingResourceId(null);
      return;
    }
    try {
      await renameResource(item.resource.id, renameValue.trim());
      // Update local state
      setResources((prev) =>
        prev.map((r) =>
          r.id === renamingResourceId && r.resource
            ? { ...r, resource: { ...r.resource, filename: renameValue.trim() } }
            : r
        )
      );
      addToast(t('resources.renamedNotification', { name: renameValue.trim() }), 'success');
    } catch { /* ignore */ }
    setRenamingResourceId(null);
  }, [renamingResourceId, renameValue, resources, addToast, t]);

  const handleRenameFolderConfirm = useCallback(async () => {
    if (!renamingFolderId || !renameFolderValue.trim()) {
      setRenamingFolderId(null);
      return;
    }
    try {
      await renameFolder(renamingFolderId, renameFolderValue.trim());
      await Promise.all([loadFolders(), loadChildFolders()]);
      addToast(t('resources.renamedNotification', { name: renameFolderValue.trim() }), 'success');
    } catch { /* ignore */ }
    setRenamingFolderId(null);
  }, [renamingFolderId, renameFolderValue, loadFolders, loadChildFolders, addToast, t]);

  // ─── Smart Folder CRUD ──────────────────────────────

  const handleCreateSmartFolder = useCallback(async (name: string, rules: SmartFolderRules) => {
    await createSmartFolder(name, scopeType, scopeId, rules);
    const updated = await fetchSmartFolders(scopeType, scopeId);
    setSmartFolders(updated);
    setShowSmartFolderEditor(false);
    // Navigate to the newly created smart folder
    const newest = updated[updated.length - 1];
    if (newest) navigate(resPath(`/resources/smart/${newest.id}`));
  }, [scopeType, scopeId, navigate, resPath]);

  const handleEditSmartFolder = useCallback(async (name: string, rules: SmartFolderRules) => {
    if (!editingSmartFolder) return;
    await updateSmartFolder(String(editingSmartFolder.id), { name, rules });
    const updated = await fetchSmartFolders(scopeType, scopeId);
    setSmartFolders(updated);
    setEditingSmartFolder(null);
  }, [editingSmartFolder, scopeType, scopeId]);

  // ─── Breadcrumb ───────────────────────────────────────

  const breadcrumbSegments = useMemo((): BreadcrumbSegment[] => {
    if (isSharedView) return [{ label: t('resources.sharedManagement') }];
    if (isRecycleView) return [{ label: t('resources.recycleBin') }];
    if (isDownloadsView) return [{ label: t('resources.downloads') }];

    if (selectedSmartFolderId) {
      const sf = smartFolders.find((s) => String(s.id) === selectedSmartFolderId);
      return [
        { label: t('resources.smartFolders'), onClick: () => {} },
        { label: sf?.name ?? '' },
      ];
    }

    if (scopeType === 'team' && selectedLibraryId) {
      const lib = libraries.find((l) => String(l.id) === selectedLibraryId);
      const libName = lib?.name ?? t('resources.allFiles');
      const segments: BreadcrumbSegment[] = [];

      segments.push({
        label: libName,
        onClick: selectedFolderId ? () => navigate(resPath(`/resources/library/${selectedLibraryId}`)) : undefined,
      });

      if (selectedFolderId && folderChain.length > 0) {
        folderChain.forEach((f, idx) => {
          const isLast = idx === folderChain.length - 1;
          segments.push({
            label: f.name,
            onClick: isLast ? undefined : () => navigate(resPath(`/resources/library/${selectedLibraryId}/folder/${f.id}`)),
          });
        });
      }

      return segments;
    }

    // Personal mode
    const segments: BreadcrumbSegment[] = [];
    segments.push({
      label: t('resources.myResources'),
      onClick: selectedFolderId ? () => navigate(resPath('/resources')) : undefined,
    });

    if (selectedFolderId && folderChain.length > 0) {
      folderChain.forEach((f, idx) => {
        const isLast = idx === folderChain.length - 1;
        segments.push({
          label: f.name,
          onClick: isLast ? undefined : () => navigate(resPath(`/resources/folder/${f.id}`)),
        });
      });
    }

    return segments;
  }, [isSharedView, isRecycleView, isDownloadsView, selectedSmartFolderId, smartFolders, scopeType, selectedLibraryId, selectedFolderId, libraries, folders, folderChain, t, navigate, resPath]);

  // ─── Sort ────────────────────────────────────────────

  const currentItems = sidebarView === 'recycle'
    ? trashedResources
    : sidebarView === 'downloads'
      ? downloadedResources
      : resources;

  // Apply filter and search
  const filteredItems = useMemo(() => {
    let items = currentItems;

    // Apply type filters
    if (activeFilters.size > 0) {
      items = items.filter((item) => {
        const mime = item.resource?.mime_type || '';
        if (activeFilters.has('video') && mime.startsWith('video/')) return true;
        if (activeFilters.has('image') && mime.startsWith('image/')) return true;
        if (activeFilters.has('audio') && mime.startsWith('audio/')) return true;
        if (activeFilters.has('document') && (
          mime.startsWith('application/pdf') ||
          mime.startsWith('application/msword') ||
          mime.startsWith('application/vnd.') ||
          mime.startsWith('text/')
        )) return true;
        if (activeFilters.has('other')) {
          const isKnown = mime.startsWith('video/') || mime.startsWith('image/') || mime.startsWith('audio/') ||
            mime.startsWith('application/pdf') || mime.startsWith('application/msword') ||
            mime.startsWith('application/vnd.') || mime.startsWith('text/');
          if (!isKnown) return true;
        }
        return false;
      });
    }

    // Apply search
    if (debouncedSearch.trim()) {
      const q = debouncedSearch.trim().toLowerCase();
      items = items.filter((item) => {
        const filename = (item.resource?.filename || '').toLowerCase();
        const folderName = (item.resource?.folder_name || '').toLowerCase();
        return filename.includes(q) || folderName.includes(q);
      });
    }

    return items;
  }, [currentItems, activeFilters, debouncedSearch]);

  const sortedItems = useMemo(() => {
    const items = [...filteredItems];
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

  // Filter folders by search query
  const filteredFolders = useMemo(() => {
    if (!debouncedSearch.trim()) return childFolders;
    const q = debouncedSearch.trim().toLowerCase();
    return childFolders.filter((f) => f.name.toLowerCase().includes(q));
  }, [childFolders, debouncedSearch]);

  // Build ordered list of all selectable IDs for shift-click range selection
  const allSelectableIds = useMemo(() => {
    const ids: string[] = [];
    filteredFolders.forEach((f) => ids.push(`folder:${f.id}`));
    sortedItems.forEach((i) => ids.push(`item:${i.id}`));
    return ids;
  }, [filteredFolders, sortedItems]);

  const handleToggleSelect = useCallback((compositeId: string, e: React.MouseEvent) => {
    // Checkbox click always enters multi-select mode
    setMultiSelectMode(true);
    if (e.shiftKey && lastClickedId) {
      const allIds = allSelectableIds;
      const startIdx = allIds.indexOf(lastClickedId);
      const endIdx = allIds.indexOf(compositeId);
      if (startIdx >= 0 && endIdx >= 0) {
        const [from, to] = startIdx < endIdx ? [startIdx, endIdx] : [endIdx, startIdx];
        setSelectedIds((prev) => {
          const next = new Set(prev);
          for (let i = from; i <= to; i++) next.add(allIds[i]);
          return next;
        });
      }
    } else {
      // Toggle this item (no need for Cmd/Ctrl in multi-select mode)
      setSelectedIds((prev) => {
        const next = new Set(prev);
        if (next.has(compositeId)) next.delete(compositeId);
        else next.add(compositeId);
        return next;
      });
    }
    setLastClickedId(compositeId);
  }, [lastClickedId, allSelectableIds]);

  const handleCardClick = useCallback((compositeId: string, e?: React.MouseEvent) => {
    if (e && (e.metaKey || e.ctrlKey || e.shiftKey)) {
      handleToggleSelect(compositeId, e);
      return;
    }
    // Normal click exits multi-select mode → single select
    setMultiSelectMode(false);
    setSelectedIds(new Set([compositeId]));
    setLastClickedId(compositeId);
  }, [handleToggleSelect]);

  // ─── Keyboard shortcuts ─────────────────────────────

  useFileKeyboard({
    allSelectableIds,
    selectedIds,
    setSelectedIds,
    enabled: isResourcesView && !renamingResourceId && !renamingFolderId && !creatingFolder,
    onDelete: useCallback(() => {
      const resourceIds = sortedItems
        .filter((i) => selectedIds.has(`item:${i.id}`) && i.resource?.id)
        .map((i) => String(i.resource!.id));
      if (resourceIds.length > 0) {
        trashResources(resourceIds).then(async () => {
          const items = await fetchResources(scopeType, scopeId, selectedFolderId, selectedLibraryId);
          setResources(items);
          setSelectedIds(new Set());
          addToast(t('resources.trashedNotification', { name: `${resourceIds.length} items` }), 'success');
        }).catch(() => {});
      }
    }, [sortedItems, selectedIds, scopeType, scopeId, selectedFolderId, selectedLibraryId, addToast, t]),
    onRename: useCallback((compositeId: string) => {
      if (compositeId.startsWith('folder:')) {
        const fId = compositeId.replace('folder:', '');
        const folder = childFolders.find((f) => f.id === fId);
        if (folder) {
          setRenamingFolderId(fId);
          setRenameFolderValue(folder.name);
        }
      } else if (compositeId.startsWith('item:')) {
        const itemId = compositeId.replace('item:', '');
        const item = resources.find((r) => r.id === itemId);
        if (item?.resource) {
          setRenamingResourceId(itemId);
          setRenameValue(item.resource.filename ?? '');
        }
      }
    }, [childFolders, resources]),
    onOpen: useCallback((compositeId: string) => {
      if (compositeId.startsWith('folder:')) {
        const fId = compositeId.replace('folder:', '');
        if (selectedLibraryId) {
          navigate(resPath(`/resources/library/${selectedLibraryId}/folder/${fId}`));
        } else {
          navigate(resPath(`/resources/folder/${fId}`));
        }
      } else if (compositeId.startsWith('item:')) {
        const itemId = compositeId.replace('item:', '');
        const item = resources.find((r) => r.id === itemId);
        if (item?.resource?.id) {
          navigate(resPath(`/resources/file/${item.resource.id}`));
        }
      }
    }, [selectedLibraryId, navigate, resPath, resources]),
    onNewFolder: useCallback(() => setCreatingFolder(true), []),
    onCopy: useCallback(() => {
      const items = sortedItems.filter((i) => selectedIds.has(`item:${i.id}`));
      if (items.length > 0) {
        setClipboardItems(items);
        setClipboardMode('copy');
        addToast(t('resources.copiedToClipboard', { count: items.length }), 'info');
      }
    }, [sortedItems, selectedIds, addToast, t]),
    onCut: useCallback(() => {
      const items = sortedItems.filter((i) => selectedIds.has(`item:${i.id}`));
      if (items.length > 0) {
        setClipboardItems(items);
        setClipboardMode('cut');
        addToast(t('resources.cutToClipboard', { count: items.length }), 'info');
      }
    }, [sortedItems, selectedIds, addToast, t]),
    onPaste: useCallback(async () => {
      if (clipboardItems.length === 0 || !clipboardMode) return;
      try {
        if (clipboardMode === 'copy') {
          for (const item of clipboardItems) {
            if (item.resource?.id) {
              await copyResourceItem(String(item.resource.id), scopeType, scopeId, selectedFolderId, selectedLibraryId);
            }
          }
          addToast(t('resources.copySuccess', { count: clipboardItems.length }), 'success');
        } else {
          // cut = move
          if (clipboardItems.length === 1) {
            await moveResourceItem(clipboardItems[0].id, selectedFolderId, selectedLibraryId);
          } else {
            await moveResourceItems(clipboardItems.map((i) => i.id), selectedFolderId, selectedLibraryId);
          }
          addToast(t('resources.moveSuccess', { count: clipboardItems.length }), 'success');
          setClipboardItems([]);
          setClipboardMode(null);
        }
        const items = await fetchResources(scopeType, scopeId, selectedFolderId, selectedLibraryId);
        setResources(items);
      } catch { /* ignore */ }
    }, [clipboardItems, clipboardMode, scopeType, scopeId, selectedFolderId, selectedLibraryId, addToast, t]),
  });

  const toggleFilter = useCallback((type: FilterType) => {
    setActiveFilters((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }, []);

  const filterOptions: { value: FilterType; label: string }[] = [
    { value: 'video', label: t('smartFolder.fileTypes.video') },
    { value: 'image', label: t('smartFolder.fileTypes.image') },
    { value: 'audio', label: t('smartFolder.fileTypes.audio') },
    { value: 'document', label: t('smartFolder.fileTypes.document') },
    { value: 'other', label: t('smartFolder.fileTypes.other') },
  ];

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

  const sidebarItemClass = (active: boolean) =>
    `w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] rounded-lg transition-colors text-left ${
      active ? 'bg-zinc-800 text-white font-medium' : 'text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200'
    }`;

  return (
    <div className="flex h-full animate-in fade-in duration-300">
      {/* ── Left panel: Unified sidebar navigation ── */}
      <div className="w-56 shrink-0 border-r border-zinc-800/80 flex flex-col">
        {/* Navigation */}
        <div className="flex-1 overflow-y-auto px-2 py-3 space-y-0.5">
          {/* ── Top section: Shared / Recycle Bin ── */}
          <button
            onClick={() => navigate(resPath('/resources/shared'))}
            className={sidebarItemClass(isSharedView)}
          >
            <Share2 size={15} className="shrink-0 opacity-70" />
            <span>{t('resources.sharedManagement')}</span>
          </button>

          <button
            onClick={() => navigate(resPath('/resources/recycle'))}
            className={sidebarItemClass(isRecycleView)}
          >
            <Trash2 size={15} className="shrink-0 opacity-70" />
            <span>{t('resources.recycleBin')}</span>
          </button>

          {/* ── Divider ── */}
          <div className="mx-1 my-2.5 border-t border-zinc-800/60" />

          {/* ── Main section: Team Libraries / Personal Resources ── */}
          {scopeType === 'team' ? (
            <>
              {/* Team Libraries with ➕ */}
              <div className="flex items-center justify-between pr-1 mb-0.5">
                <span className="px-3 py-1 text-[11px] font-semibold text-zinc-500 uppercase tracking-widest">
                  {t('resources.teamLibraries')}
                </span>
                <button
                  onClick={() => setCreatingLibrary(true)}
                  className="p-1 text-zinc-600 hover:text-zinc-300 hover:bg-zinc-800 rounded-md transition-colors shrink-0"
                  title={t('resources.newLibrary')}
                >
                  <Plus size={14} />
                </button>
              </div>

              {/* Library list */}
              {libraries.map((lib) => (
                <button
                  key={lib.id}
                  onClick={() => navigate(resPath(`/resources/library/${lib.id}`))}
                  className={sidebarItemClass(isResourcesView && selectedLibraryId === String(lib.id))}
                >
                  <BookOpen size={15} className="shrink-0 opacity-70" />
                  <span className="truncate">{lib.name}</span>
                </button>
              ))}

              {/* Inline new library input */}
              {creatingLibrary && (
                <div className="flex items-center gap-1.5 px-2 py-1">
                  <input
                    ref={newLibraryInputRef}
                    type="text"
                    value={newLibraryName}
                    onChange={(e) => setNewLibraryName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleCreateLibrary();
                      if (e.key === 'Escape') {
                        setCreatingLibrary(false);
                        setNewLibraryName('');
                      }
                    }}
                    onBlur={() => {
                      if (!newLibraryName.trim()) {
                        setCreatingLibrary(false);
                        setNewLibraryName('');
                      }
                    }}
                    placeholder={t('resources.libraryName')}
                    disabled={savingLibrary}
                    className="flex-1 min-w-0 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 disabled:opacity-50"
                  />
                  {savingLibrary && (
                    <Loader2 size={12} className="animate-spin text-zinc-400" />
                  )}
                </div>
              )}


              {libraries.length === 0 && !creatingLibrary && (
                <div className="px-3 py-4 text-center">
                  <p className="text-xs text-zinc-500">{t('resources.noLibraries')}</p>
                </div>
              )}
            </>
          ) : (
            <>
              {/* Personal mode: My Downloads + My Resources */}
              <button
                onClick={() => navigate(resPath('/resources/downloads'))}
                className={sidebarItemClass(isDownloadsView)}
              >
                <Download size={15} className="shrink-0 opacity-70" />
                <span>{t('resources.downloads')}</span>
              </button>

              {/* My Resources with ➕ */}
              <div className="flex items-center justify-between pr-1">
                <button
                  onClick={() => navigate(resPath('/resources'))}
                  onDragOver={handleSidebarDragOver}
                  onDrop={(e) => handleSidebarDrop(e, null)}
                  className={sidebarItemClass(isResourcesView && selectedFolderId === null && !selectedSmartFolderId)}
                >
                  <FolderOpen size={15} className="shrink-0 opacity-70" />
                  <span>{t('resources.myResources')}</span>
                </button>
                <button
                  onClick={() => {
                    navigate(resPath('/resources'));
                    setCreatingFolder(true);
                  }}
                  className="p-1 text-zinc-600 hover:text-zinc-300 hover:bg-zinc-800 rounded-md transition-colors shrink-0"
                  title={t('resources.newFolder')}
                >
                  <Plus size={14} />
                </button>
              </div>

              {/* Folder tree */}
              {scopeType === 'team' && (
                <SidebarFolderTree
                  folders={folders}
                  currentFolderId={selectedFolderId}
                  onNavigate={(folderId) => {
                    if (folderId) {
                      navigate(resPath(`/resources/folder/${folderId}`));
                    } else {
                      navigate(resPath('/resources'));
                    }
                  }}
                  onDragOver={handleSidebarDragOver}
                  onDrop={handleSidebarDrop}
                />
              )}

            </>
          )}

          {/* ── Divider ── */}
          <div className="mx-1 my-2.5 border-t border-zinc-800/60" />

          {/* ── Smart Folders with ➕ ── */}
          <div className="flex items-center justify-between pr-1 mb-0.5">
            <span className="px-3 py-1 text-[11px] font-semibold text-zinc-500 uppercase tracking-widest">
              {t('resources.smartFolders')}
            </span>
            <button
              className="p-1 text-zinc-600 hover:text-zinc-300 hover:bg-zinc-800 rounded-md transition-colors shrink-0"
              title={t('smartFolder.createTitle')}
              onClick={(e) => { e.stopPropagation(); setShowSmartFolderEditor(true); }}
            >
              <Plus size={14} />
            </button>
          </div>

          {/* Smart folder items */}
          {smartFolders.map((sf) => (
            <button
              key={sf.id}
              onClick={() => navigate(resPath(`/resources/smart/${sf.id}`))}
              onContextMenu={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setContextMenu({ x: e.clientX, y: e.clientY, type: 'smartFolder' as any, target: sf as any });
              }}
              className={`group ${sidebarItemClass(isResourcesView && selectedSmartFolderId === String(sf.id))}`}
            >
              <Zap size={15} className="shrink-0 opacity-70" />
              <span className="truncate flex-1">{sf.name}</span>
              <span
                className="opacity-0 group-hover:opacity-100 ml-auto text-zinc-600 hover:text-zinc-300 transition-all"
                onClick={(e) => {
                  e.stopPropagation();
                  setEditingSmartFolder(sf);
                }}
                title={t('smartFolder.editSmartFolder')}
              >
                <Pencil size={12} />
              </span>
            </button>
          ))}

          {smartFolders.length === 0 && (
            <button
              onClick={() => setShowSmartFolderEditor(true)}
              className="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-zinc-600 hover:text-zinc-400 rounded-lg transition-colors text-left"
            >
              <Plus size={15} className="shrink-0 opacity-70" />
              <span>{t('smartFolder.createTitle')}</span>
            </button>
          )}
        </div>
      </div>

      {/* ── Center panel: Main content ── */}
      <div
        className="flex-1 min-w-0 flex flex-col"
      >
        {isDownloadsView ? (
          <RipVaultView />
        ) : (
        <>
        {/* Toolbar */}
        <div
          className="px-6 py-3 border-b border-zinc-800/80"
          style={{ paddingRight: selectedResource?.resource && showInfoPanel ? `${infoPanelWidth + 24}px` : undefined }}
        >
          {/* Single row: Breadcrumb + controls */}
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3 min-w-0">
              <Breadcrumb segments={breadcrumbSegments} />
              {!loading && (
                <span className="text-[11px] text-zinc-600 shrink-0 tabular-nums">
                  {filteredFolders.length + sortedItems.length} {t('resources.items')}
                </span>
              )}
            </div>

            <div className="flex items-center gap-2 shrink-0">
              {/* Search box */}
              <div className="relative flex items-center">
                <Search size={14} className="absolute left-2.5 text-zinc-500 pointer-events-none" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={t('resources.searchFiles')}
                  className="w-44 pl-8 pr-7 py-1.5 text-xs bg-zinc-800/60 border border-zinc-700/50 rounded-lg text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 transition-colors"
                />
                {searchQuery && (
                  <button
                    onClick={() => setSearchQuery('')}
                    className="absolute right-2 text-zinc-500 hover:text-zinc-300"
                  >
                    <X size={12} />
                  </button>
                )}
              </div>

              {/* Filter button */}
              <div className="relative">
                <button
                  onClick={() => setShowFilterPanel(!showFilterPanel)}
                  className={`flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-lg transition-colors ${
                    activeFilters.size > 0
                      ? 'text-indigo-400 bg-indigo-500/10 hover:bg-indigo-500/20'
                      : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/80'
                  }`}
                >
                  <Filter size={14} />
                  {activeFilters.size > 0 && (
                    <span className="min-w-[16px] h-4 flex items-center justify-center text-[10px] font-medium bg-indigo-500 text-white rounded-full px-1">
                      {activeFilters.size}
                    </span>
                  )}
                </button>
                {showFilterPanel && (
                  <>
                    <div className="fixed inset-0 z-10" onClick={() => setShowFilterPanel(false)} />
                    <div className="absolute right-0 top-full mt-1 z-20 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl py-1 w-40">
                      {filterOptions.map((opt) => (
                        <button
                          key={opt.value}
                          onClick={() => toggleFilter(opt.value)}
                          className={`w-full text-left px-3 py-1.5 text-xs transition-colors flex items-center justify-between ${
                            activeFilters.has(opt.value)
                              ? 'bg-zinc-800 text-indigo-400'
                              : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
                          }`}
                        >
                          <span>{opt.label}</span>
                          {activeFilters.has(opt.value) && (
                            <Check size={12} className="text-indigo-400" />
                          )}
                        </button>
                      ))}
                      {activeFilters.size > 0 && (
                        <>
                          <div className="mx-2 my-1 border-t border-zinc-700" />
                          <button
                            onClick={() => { setActiveFilters(new Set()); setShowFilterPanel(false); }}
                            className="w-full text-left px-3 py-1.5 text-xs text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors"
                          >
                            {t('resources.clearFilters')}
                          </button>
                        </>
                      )}
                    </div>
                  </>
                )}
              </div>

              {/* Sort dropdown */}
              <div className="relative">
                <button
                  onClick={() => setShowSortMenu(!showSortMenu)}
                  className="flex items-center gap-1 px-2.5 py-1.5 text-xs text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/80 rounded-lg transition-colors"
                >
                  <span>{currentSortLabel}</span>
                  <ChevronDown size={12} />
                </button>
                {showSortMenu && (
                  <>
                    <div className="fixed inset-0 z-10" onClick={() => setShowSortMenu(false)} />
                    <div className="absolute left-0 top-full mt-1 z-20 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl py-1 w-40">
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

              {/* View toggle — single button */}
              <button
                onClick={() => setViewMode(viewMode === 'grid' ? 'list' : 'grid')}
                className="p-1.5 rounded-lg bg-zinc-800/60 text-zinc-400 hover:text-zinc-200 transition-colors"
                title={viewMode === 'grid' ? t('resources.listView') : t('resources.gridView')}
              >
                {viewMode === 'grid' ? <LayoutList size={14} /> : <LayoutGrid size={14} />}
              </button>

              {/* Upload button (only on resources view) */}
              {canUpload && (
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
                  <input
                    ref={folderInputRef}
                    type="file"
                    // @ts-ignore - webkitdirectory is non-standard but widely supported
                    webkitdirectory=""
                    directory=""
                    multiple
                    className="hidden"
                    onChange={(e) => {
                      if (e.target.files?.length) {
                        handleUpload(e.target.files);
                        e.target.value = '';
                      }
                    }}
                  />
                  {uploading ? (
                    <button
                      disabled
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-indigo-800 text-white rounded-lg"
                    >
                      <Loader2 size={14} className="animate-spin" />
                      <span>{upload.overallProgress}%</span>
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
                        {/* Group 1 — Containers */}
                        <button
                          onClick={() => { setCreatingFolder(true); setShowNewDropdown(false); }}
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
                          onClick={() => { addToast(t('resources.comingSoon'), 'info'); setShowNewDropdown(false); }}
                          className="w-full text-left px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors flex items-center gap-2"
                        >
                          <FolderSearch size={14} className="text-purple-400" />
                          {t('resources.newSmartFolder')}
                        </button>
                        {/* Divider */}
                        <div className="border-t border-zinc-800 my-1" />
                        {/* Group 2 — Documents */}
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
                        {/* Group 3 — Other */}
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
          className="flex-1 overflow-y-auto p-6 relative"
          onDragEnter={canUpload ? handleDragEnter : undefined}
          onDragOver={canUpload ? handleDragOver : undefined}
          onDragLeave={canUpload ? handleDragLeave : undefined}
          onDrop={canUpload ? handleDrop : undefined}
          onContextMenu={canUpload ? handleEmptyAreaContextMenu : undefined}
          onClick={(e) => {
            // Click on empty area → deselect all
            const target = e.target as HTMLElement;
            if (!target.closest('[data-context-item]')) {
              if (selectedIds.size > 0 || multiSelectMode) {
                setSelectedIds(new Set());
                setMultiSelectMode(false);
              }
              setSelectedResource(null);
            }
          }}
        >
          {/* ── Drag-and-drop overlay ── */}
          {dragOver && canUpload && (
            <div className="absolute inset-0 z-30 flex flex-col items-center justify-center bg-zinc-950/80 backdrop-blur-sm border-2 border-dashed border-indigo-500 rounded-xl m-2 pointer-events-none">
              <UploadCloud size={56} className="text-indigo-400 mb-4 animate-bounce" />
              <p className="text-lg font-medium text-indigo-300">{t('resources.dropToUpload')}</p>
              <p className="text-sm text-zinc-400 mt-1">{t('resources.dropToUploadHint')}</p>
            </div>
          )}

          {/* Shared view placeholder */}
          {isSharedView && (
            <div className="flex flex-col items-center justify-center h-full min-h-[300px] text-center">
              <Share2 size={48} className="text-zinc-600 mb-4" />
              <p className="text-zinc-400 text-sm">{t('resources.sharedComingSoon')}</p>
            </div>
          )}

          {/* Inline new folder input (content area) */}
          {creatingFolder && !isSharedView && !isRecycleView && (
            <div className="mb-4 flex items-center gap-2 max-w-sm">
              <FolderPlus size={16} className="text-amber-400 shrink-0" />
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
                onBlur={() => {
                  if (!newFolderName.trim()) {
                    setCreatingFolder(false);
                    setNewFolderName('');
                  }
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

          {/* Resources / Recycle / Downloads content */}
          {!isSharedView && (
            loading ? (
              viewMode === 'grid' ? <SkeletonGrid /> : <SkeletonList />
            ) : (filteredFolders.length > 0 || sortedItems.length > 0) ? (
              <div className="space-y-5">
                {/* Folders section */}
                {filteredFolders.length > 0 && (
                  <div>
                    {sortedItems.length > 0 && (
                      <h3 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-3">{t('resources.folders')}</h3>
                    )}
                    {viewMode === 'grid' ? (
                      <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))' }}>
                        {filteredFolders.map((folder) => (
                          <FolderCard
                            key={`folder-${folder.id}`}
                            folder={folder}
                            onClick={(e?: any) => {
                              handleCardClick(`folder:${folder.id}`, e);
                              if (!(e?.metaKey || e?.ctrlKey || e?.shiftKey)) setSelectedResource(null);
                            }}
                            onDoubleClick={() => {
                              if (selectedLibraryId) {
                                navigate(resPath(`/resources/library/${selectedLibraryId}/folder/${folder.id}`));
                              } else {
                                navigate(resPath(`/resources/folder/${folder.id}`));
                              }
                            }}
                            viewMode="grid"
                            onContextMenu={(e) => handleFolderContextMenu(e, folder)}
                            renaming={renamingFolderId === folder.id}
                            renameValue={renamingFolderId === folder.id ? renameFolderValue : undefined}
                            onRenameChange={setRenameFolderValue}
                            onRenameConfirm={handleRenameFolderConfirm}
                            onRenameCancel={() => setRenamingFolderId(null)}
                            onStartRename={() => { setRenamingFolderId(folder.id); setRenameFolderValue(folder.name); }}
                            selectable
                            isChecked={selectedIds.has(`folder:${folder.id}`)}
                            onToggleSelect={(e) => handleToggleSelect(`folder:${folder.id}`, e)}
                            forceShowCheckbox={multiSelectMode}
                            onDropItems={(ids) => handleDropOnFolder(folder.id, ids)}
                            previewItems={folderPreviews[folder.id]}
                          />
                        ))}
                      </div>
                    ) : (
                      <div className="space-y-1.5">
                        {filteredFolders.map((folder) => (
                          <FolderCard
                            key={`folder-${folder.id}`}
                            folder={folder}
                            onClick={(e?: any) => {
                              handleCardClick(`folder:${folder.id}`, e);
                              if (!(e?.metaKey || e?.ctrlKey || e?.shiftKey)) setSelectedResource(null);
                            }}
                            onDoubleClick={() => {
                              if (selectedLibraryId) {
                                navigate(resPath(`/resources/library/${selectedLibraryId}/folder/${folder.id}`));
                              } else {
                                navigate(resPath(`/resources/folder/${folder.id}`));
                              }
                            }}
                            viewMode="list"
                            onContextMenu={(e) => handleFolderContextMenu(e, folder)}
                            renaming={renamingFolderId === folder.id}
                            renameValue={renamingFolderId === folder.id ? renameFolderValue : undefined}
                            onRenameChange={setRenameFolderValue}
                            onRenameConfirm={handleRenameFolderConfirm}
                            onRenameCancel={() => setRenamingFolderId(null)}
                            onStartRename={() => { setRenamingFolderId(folder.id); setRenameFolderValue(folder.name); }}
                            selectable
                            isChecked={selectedIds.has(`folder:${folder.id}`)}
                            onToggleSelect={(e) => handleToggleSelect(`folder:${folder.id}`, e)}
                            forceShowCheckbox={multiSelectMode}
                            onDropItems={(ids) => handleDropOnFolder(folder.id, ids)}
                            previewItems={folderPreviews[folder.id]}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* Files section */}
                {sortedItems.length > 0 && (
                  <div>
                    {filteredFolders.length > 0 && (
                      <h3 className="text-[11px] font-semibold text-zinc-500 uppercase tracking-widest mb-3">{t('resources.files')}</h3>
                    )}
                    {viewMode === 'list' && (
                      <div className="flex items-center gap-4 px-4 py-2 text-[11px] font-semibold text-zinc-500 uppercase tracking-wider border-b border-zinc-800/60 mb-1">
                        <div className="w-10" /> {/* thumbnail spacer */}
                        <button onClick={() => setSortBy(sortBy === 'name-az' ? 'name-za' : 'name-az')} className="flex-1 text-left hover:text-zinc-300 transition-colors cursor-pointer">
                          {t('resources.listHeaderName')} {sortBy === 'name-az' ? '↑' : sortBy === 'name-za' ? '↓' : ''}
                        </button>
                        <span className="w-24 text-left">{t('resources.listHeaderType')}</span>
                        <button onClick={() => setSortBy(sortBy === 'largest' ? 'smallest' : 'largest')} className="w-20 text-right hover:text-zinc-300 transition-colors cursor-pointer">
                          {t('resources.listHeaderSize')} {sortBy === 'largest' ? '↓' : sortBy === 'smallest' ? '↑' : ''}
                        </button>
                        <button onClick={() => setSortBy(sortBy === 'newest' ? 'oldest' : 'newest')} className="w-28 text-right hover:text-zinc-300 transition-colors cursor-pointer">
                          {t('resources.modifiedAt')} {sortBy === 'newest' ? '↓' : sortBy === 'oldest' ? '↑' : ''}
                        </button>
                      </div>
                    )}
                    {viewMode === 'grid' ? (
                      <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))' }}>
                        {sortedItems.map((item) => (
                          <ResourceCard
                            key={item.id}
                            item={item}
                            onClick={(e?: any) => {
                              handleCardClick(`item:${item.id}`, e);
                              if (!(e?.metaKey || e?.ctrlKey || e?.shiftKey)) handleResourceClick(item);
                            }}
                            onDoubleClick={() => handleResourceDoubleClick(item)}
                            viewMode="grid"
                            isSelected={selectedResource?.id === item.id}
                            showRestoreAction={isRecycleView}
                            onTrash={isRecycleView ? undefined : handleTrash}
                            onRestore={isRecycleView ? handleRestore : undefined}
                            onPermanentDelete={isRecycleView ? handlePermanentDelete : undefined}
                            onContextMenu={!isRecycleView ? (e) => handleFileContextMenu(e, item) : undefined}
                            renaming={renamingResourceId === item.id}
                            renameValue={renamingResourceId === item.id ? renameValue : undefined}
                            onRenameChange={setRenameValue}
                            onRenameConfirm={handleRenameResourceConfirm}
                            onRenameCancel={() => setRenamingResourceId(null)}
                            onStartRename={() => { setRenamingResourceId(item.id); setRenameValue(item.resource?.filename ?? ""); }}
                            selectable={!isRecycleView}
                            isChecked={selectedIds.has(`item:${item.id}`)}
                            onToggleSelect={(e) => handleToggleSelect(`item:${item.id}`, e)}
                            forceShowCheckbox={multiSelectMode}
                            selectedIds={selectedIds}
                            compositeId={`item:${item.id}`}
                          />
                        ))}
                      </div>
                    ) : (
                      <div className="space-y-1.5">
                        {sortedItems.map((item) => (
                          <ResourceCard
                            key={item.id}
                            item={item}
                            onClick={(e?: any) => {
                              handleCardClick(`item:${item.id}`, e);
                              if (!(e?.metaKey || e?.ctrlKey || e?.shiftKey)) handleResourceClick(item);
                            }}
                            onDoubleClick={() => handleResourceDoubleClick(item)}
                            viewMode="list"
                            isSelected={selectedResource?.id === item.id}
                            showRestoreAction={isRecycleView}
                            onTrash={isRecycleView ? undefined : handleTrash}
                            onRestore={isRecycleView ? handleRestore : undefined}
                            onPermanentDelete={isRecycleView ? handlePermanentDelete : undefined}
                            onContextMenu={!isRecycleView ? (e) => handleFileContextMenu(e, item) : undefined}
                            renaming={renamingResourceId === item.id}
                            renameValue={renamingResourceId === item.id ? renameValue : undefined}
                            onRenameChange={setRenameValue}
                            onRenameConfirm={handleRenameResourceConfirm}
                            onRenameCancel={() => setRenamingResourceId(null)}
                            onStartRename={() => { setRenamingResourceId(item.id); setRenameValue(item.resource?.filename ?? ""); }}
                            selectable={!isRecycleView}
                            isChecked={selectedIds.has(`item:${item.id}`)}
                            onToggleSelect={(e) => handleToggleSelect(`item:${item.id}`, e)}
                            forceShowCheckbox={multiSelectMode}
                            selectedIds={selectedIds}
                            compositeId={`item:${item.id}`}
                          />
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
        </>
        )}
      </div>

      {/* ── Right panel: Fixed overlay, from TopBar bottom to viewport bottom ── */}
      {selectedResource?.resource && (
        <div
          className={`fixed top-14 bottom-0 right-0 z-40 flex bg-zinc-900 border-l border-zinc-800 transition-transform duration-300 ease-in-out shadow-2xl ${
            showInfoPanel ? 'translate-x-0' : 'translate-x-full'
          }`}
          style={{ width: `${infoPanelWidth}px` }}
        >
          {/* Collapse tab — attached to left edge of panel */}
          <button
            onClick={() => setShowInfoPanel(false)}
            className="absolute -left-10 bottom-8 w-10 h-12 bg-zinc-900 border-l border-y border-zinc-800 rounded-l-xl flex items-center justify-center text-zinc-400 hover:text-white cursor-pointer hover:bg-zinc-800 transition-colors z-10"
            title={t('resources.toggleInfoPanel')}
          >
            <ChevronRight size={20} />
          </button>

          {/* Resize handle — left edge blue line on hover */}
          <div
            onMouseDown={handlePanelResizeStart}
            className="w-1 h-full cursor-col-resize shrink-0 hover:bg-blue-500 active:bg-blue-500 transition-colors"
          />

          {/* Panel content */}
          <ResourceInfoPanel
            resource={selectedResource.resource}
            allTags={allTags}
            assignedTags={selectedResourceTags}
            onClose={() => setSelectedResource(null)}
            onAddTag={handleAddTag}
            onRemoveTag={handleRemoveTag}
          />
        </div>
      )}

      {/* Expand tab — fixed to viewport right edge, visible when panel is closed */}
      {selectedResource?.resource && !showInfoPanel && (
        <button
          onClick={() => setShowInfoPanel(true)}
          className="fixed bottom-8 right-0 w-10 h-12 bg-zinc-900 border-l border-y border-zinc-800 rounded-l-xl flex items-center justify-center text-zinc-400 hover:text-white cursor-pointer hover:bg-zinc-800 transition-all z-50"
          title={t('resources.toggleInfoPanel')}
        >
          <ChevronLeft size={20} />
        </button>
      )}

      {/* ── Batch Selection Toolbar ── */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 bg-zinc-900 border border-zinc-700 rounded-xl px-5 py-3 shadow-2xl">
          <span className="text-sm text-zinc-300 font-medium">
            {t('resources.selected', { count: selectedIds.size })}
          </span>
          <div className="w-px h-5 bg-zinc-700" />
          {isRecycleView ? (
            <>
              <button
                onClick={async () => {
                  const resourceIds = currentItems
                    .filter((i) => selectedIds.has(`item:${i.id}`) && i.resource?.id)
                    .map((i) => String(i.resource!.id));
                  for (const id of resourceIds) {
                    await restoreResource(id);
                  }
                  setTrashedResources((prev) => prev.filter((r) => !resourceIds.includes(String(r.resource?.id))));
                  setSelectedIds(new Set());
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-emerald-400 hover:text-emerald-300 hover:bg-emerald-900/30 rounded-lg transition-colors"
              >
                <RefreshCw size={14} />
                {t('resources.batchRestore')}
              </button>
              <button
                onClick={() => {
                  const resourceIds = currentItems
                    .filter((i) => selectedIds.has(`item:${i.id}`) && i.resource?.id)
                    .map((i) => String(i.resource!.id));
                  setPendingBatchPermanentDelete(resourceIds);
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-red-400 hover:text-red-300 hover:bg-red-900/30 rounded-lg transition-colors"
              >
                <Trash2 size={14} />
                {t('resources.batchPermanentDelete')}
              </button>
            </>
          ) : (
            <>
              <button
                onClick={() => {
                  const items = sortedItems.filter((i) => selectedIds.has(`item:${i.id}`));
                  const flds = childFolders.filter((f) => selectedIds.has(`folder:${f.id}`));
                  setOperationTargetItems(items);
                  setOperationTargetFolders(flds);
                  setFolderPickerMode('move');
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-zinc-300 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
              >
                <Move size={14} />
                {t('resources.batchMove')}
              </button>
              <button
                onClick={() => {
                  const items = sortedItems.filter((i) => selectedIds.has(`item:${i.id}`));
                  setOperationTargetItems(items);
                  setOperationTargetFolders([]);
                  setFolderPickerMode('copy');
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-zinc-300 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
              >
                <Copy size={14} />
                {t('resources.batchCopy')}
              </button>
              <button
                onClick={async () => {
                  const resourceIds = sortedItems
                    .filter((i) => selectedIds.has(`item:${i.id}`) && i.resource?.id)
                    .map((i) => String(i.resource!.id));
                  if (resourceIds.length > 0) {
                    try {
                      await trashResources(resourceIds);
                      const items = await fetchResources(scopeType, scopeId, selectedFolderId, selectedLibraryId);
                      setResources(items);
                      setSelectedIds(new Set());
                    } catch { /* ignore */ }
                  }
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-red-400 hover:text-red-300 hover:bg-red-900/30 rounded-lg transition-colors"
              >
                <Trash2 size={14} />
                {t('resources.batchDelete')}
              </button>
            </>
          )}
          <div className="w-px h-5 bg-zinc-700" />
          <button
            onClick={() => setSelectedIds(new Set())}
            className="p-1.5 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* ── Context Menu ── */}
      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          items={contextMenuItems}
          onClose={closeContextMenu}
        />
      )}

      {/* ── Smart Folder Editor (Create) ── */}
      {showSmartFolderEditor && (
        <SmartFolderEditor
          onSave={handleCreateSmartFolder}
          onClose={() => setShowSmartFolderEditor(false)}
        />
      )}

      {/* ── Smart Folder Editor (Edit) ── */}
      {editingSmartFolder && (
        <SmartFolderEditor
          initialName={editingSmartFolder.name}
          initialRules={editingSmartFolder.smart_rules as SmartFolderRules | undefined}
          onSave={handleEditSmartFolder}
          onClose={() => setEditingSmartFolder(null)}
        />
      )}

      {/* ── Share Modal ── */}
      {shareTarget && (
        <ShareModal
          isOpen={true}
          onClose={() => setShareTarget(null)}
          resourceId={shareTarget.resourceId}
          folderId={shareTarget.folderId}
        />
      )}

      {/* ── Folder Picker Modal (Copy/Move) ── */}
      {folderPickerMode && (
        <FolderPickerModal
          isOpen={true}
          onClose={() => {
            setFolderPickerMode(null);
            setOperationTargetItems([]);
            setOperationTargetFolders([]);
          }}
          onConfirm={handleFolderPickerConfirm}
          mode={folderPickerMode}
          scopeType={scopeType}
          scopeId={scopeId}
          currentLibraryId={selectedLibraryId}
          excludeFolderIds={operationTargetFolders.map((f) => f.id)}
        />
      )}

      {/* ── Permanent Delete Confirmation Dialog ── */}
      {(pendingPermanentDelete || pendingBatchPermanentDelete) && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => { setPendingPermanentDelete(null); setPendingBatchPermanentDelete(null); }}
          />
          <div className="relative bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl w-full max-w-sm mx-4">
            <div className="p-6 text-center">
              <div className="w-12 h-12 rounded-full bg-red-500/10 flex items-center justify-center mx-auto mb-4">
                <AlertTriangle className="text-red-500" size={24} />
              </div>
              <h3 className="text-lg font-semibold text-white mb-2">
                {t('resources.confirmPermanentDelete')}
              </h3>
              <p className="text-sm text-zinc-400">
                {t('resources.permanentDeleteWarning')}
              </p>
            </div>
            <div className="flex gap-3 p-4 border-t border-zinc-800">
              <button
                onClick={() => { setPendingPermanentDelete(null); setPendingBatchPermanentDelete(null); }}
                className="flex-1 px-4 py-2 text-sm font-medium text-zinc-300 bg-zinc-800 hover:bg-zinc-700 rounded-lg transition-colors"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={confirmPermanentDelete}
                className="flex-1 px-4 py-2 text-sm font-medium text-white bg-red-600 hover:bg-red-500 rounded-lg transition-colors"
              >
                {t('resources.deleteForever')}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};
