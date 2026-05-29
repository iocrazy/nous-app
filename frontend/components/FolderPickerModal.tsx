import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  Folder as FolderIcon, ChevronRight, ChevronDown, Search, FolderPlus, X,
  Library as LibraryIcon, Home, File, Film, Image, Music, FileText,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Folder, Library } from '../types';
import { fetchFolders, buildFolderTree, createFolder } from '../services/resourceService';
import { fetchLibraries } from '../services/libraryService';

export interface MovingItemInfo {
  name: string;
  thumbnail?: string | null;
  mediaType?: string;
}

interface FolderPickerModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (targetFolderId: string | null, targetLibraryId?: string | null) => void;
  mode: 'copy' | 'move';
  isPersonal: boolean;
  scopeId: string;
  currentLibraryId?: string | null;
  excludeFolderIds?: string[];
  movingItems?: MovingItemInfo[];
}

// ── Left panel folder node ──
interface FolderNodeProps {
  folder: Folder;
  depth: number;
  selectedId: string | null;
  expandedIds: Set<string>;
  excludeIds: Set<string>;
  onSelect: (id: string) => void;
  onToggleExpand: (id: string) => void;
}

const FolderNode: React.FC<FolderNodeProps> = ({
  folder, depth, selectedId, expandedIds, excludeIds, onSelect, onToggleExpand,
}) => {
  if (excludeIds.has(folder.id)) return null;
  const isExpanded = expandedIds.has(folder.id);
  const isSelected = selectedId === folder.id;
  const hasChildren = folder.children && folder.children.length > 0;

  return (
    <div>
      <div
        className={`flex items-center gap-1.5 px-2 py-1.5 rounded-md cursor-pointer transition-all text-sm select-none ${
          isSelected
            ? 'bg-indigo-500/20 text-indigo-300'
            : 'hover:bg-zinc-800/80 text-zinc-400 hover:text-zinc-200'
        }`}
        style={{ paddingLeft: `${8 + depth * 16}px` }}
        onClick={() => onSelect(folder.id)}
      >
        {hasChildren ? (
          <button
            onClick={(e) => { e.stopPropagation(); onToggleExpand(folder.id); }}
            className="p-0.5 hover:bg-zinc-700 rounded transition-colors flex-shrink-0"
          >
            {isExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          </button>
        ) : (
          <span className="w-[18px] flex-shrink-0" />
        )}
        <FolderIcon size={14} className="text-amber-400/80 flex-shrink-0" />
        <span className="truncate">{folder.name}</span>
      </div>
      {isExpanded && folder.children && (
        <div>
          {folder.children.map((child) => (
            <FolderNode
              key={child.id}
              folder={child}
              depth={depth + 1}
              selectedId={selectedId}
              expandedIds={expandedIds}
              excludeIds={excludeIds}
              onSelect={onSelect}
              onToggleExpand={onToggleExpand}
            />
          ))}
        </div>
      )}
    </div>
  );
};

// ── File type icon helper ──
function getFileIcon(mediaType?: string) {
  if (!mediaType) return <File size={12} className="text-zinc-500" />;
  if (mediaType.startsWith('video')) return <Film size={12} className="text-blue-400" />;
  if (mediaType.startsWith('image')) return <Image size={12} className="text-emerald-400" />;
  if (mediaType.startsWith('audio')) return <Music size={12} className="text-purple-400" />;
  return <FileText size={12} className="text-zinc-400" />;
}

// ── Main Component ──
export const FolderPickerModal: React.FC<FolderPickerModalProps> = ({
  isOpen, onClose, onConfirm, mode, isPersonal, scopeId,
  currentLibraryId, excludeFolderIds = [], movingItems = [],
}) => {
  const { t } = useTranslation();
  const [folders, setFolders] = useState<Folder[]>([]);
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [selectedLibraryId, setSelectedLibraryId] = useState<string | null>(currentLibraryId ?? null);
  const [selectedFolderId, setSelectedFolderId] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [searchQuery, setSearchQuery] = useState('');
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const newFolderRef = useRef<HTMLInputElement>(null);

  const excludeSet = useMemo(() => new Set(excludeFolderIds), [excludeFolderIds]);

  const loadFolders = useCallback(async () => {
    try {
      const data = await fetchFolders(scopeId, isPersonal, selectedLibraryId);
      setFolders(data);
    } catch { /* ignore */ }
  }, [isPersonal, scopeId, selectedLibraryId]);

  useEffect(() => {
    if (!isOpen) return;
    loadFolders();
    if (!isPersonal) {
      fetchLibraries(scopeId).then(setLibraries).catch(() => {});
    }
  }, [isOpen, loadFolders, isPersonal, scopeId]);

  useEffect(() => {
    if (creatingFolder && newFolderRef.current) {
      newFolderRef.current.focus();
    }
  }, [creatingFolder]);

  const folderTree = useMemo(() => buildFolderTree(folders), [folders]);

  const filteredTree = useMemo(() => {
    if (!searchQuery.trim()) return folderTree;
    const q = searchQuery.toLowerCase();
    const filterNode = (node: Folder): Folder | null => {
      const childMatches = (node.children || []).map(filterNode).filter(Boolean) as Folder[];
      if (node.name.toLowerCase().includes(q) || childMatches.length > 0) {
        return { ...node, children: childMatches };
      }
      return null;
    };
    return folderTree.map(filterNode).filter(Boolean) as Folder[];
  }, [folderTree, searchQuery]);

  const handleToggleExpand = useCallback((id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const handleCreateFolder = useCallback(async () => {
    if (!newFolderName.trim()) {
      setCreatingFolder(false);
      return;
    }
    try {
      await createFolder({
        name: newFolderName.trim(),
        parent_id: selectedFolderId,
        isPersonal,
        scope_id: scopeId,
      });
      setNewFolderName('');
      setCreatingFolder(false);
      await loadFolders();
    } catch { /* ignore */ }
  }, [newFolderName, selectedFolderId, isPersonal, scopeId, loadFolders]);

  // Derive right-panel content: subfolders of selected folder
  const rightPanelFolders = useMemo(() => {
    if (selectedFolderId === null) {
      // Root selected — show top-level folders
      return filteredTree;
    }
    // Find the selected folder in the tree
    const findNode = (nodes: Folder[]): Folder | null => {
      for (const n of nodes) {
        if (n.id === selectedFolderId) return n;
        if (n.children) {
          const found = findNode(n.children);
          if (found) return found;
        }
      }
      return null;
    };
    const node = findNode(folderTree);
    return node?.children || [];
  }, [selectedFolderId, filteredTree, folderTree]);

  // Breadcrumb path
  const breadcrumb = useMemo(() => {
    if (selectedFolderId === null) {
      const lib = libraries.find(l => l.id === selectedLibraryId);
      return lib ? [{ id: null, name: lib.name }] : [{ id: null, name: 'Root' }];
    }
    const path: { id: string | null; name: string }[] = [];
    const findPath = (nodes: Folder[]): boolean => {
      for (const n of nodes) {
        if (n.id === selectedFolderId) {
          path.push({ id: n.id, name: n.name });
          return true;
        }
        if (n.children && findPath(n.children)) {
          path.unshift({ id: n.id, name: n.name });
          return true;
        }
      }
      return false;
    };
    findPath(folderTree);
    const lib = libraries.find(l => l.id === selectedLibraryId);
    path.unshift({ id: null, name: lib?.name || 'Root' });
    return path;
  }, [selectedFolderId, folderTree, libraries, selectedLibraryId]);

  // Handle right panel folder click — navigate into it
  const handleRightPanelNav = useCallback((folderId: string) => {
    setSelectedFolderId(folderId);
    setExpandedIds(prev => new Set([...prev, folderId]));
  }, []);

  if (!isOpen) return null;

  const itemCount = movingItems.length;
  const actionVerb = mode === 'copy' ? 'Copy' : 'Move';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div
        className="w-full max-w-2xl bg-zinc-900 border border-zinc-700/50 rounded-2xl shadow-2xl flex flex-col max-h-[75vh] overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* ── Header ── */}
        <div className="px-5 py-4 border-b border-zinc-800/80">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-[15px] font-semibold text-white select-none">
              {actionVerb} {itemCount > 0 ? `${itemCount} item${itemCount > 1 ? 's' : ''} to...` : ''}
            </h2>
            <button
              onClick={onClose}
              className="p-1.5 hover:bg-zinc-800 rounded-lg text-zinc-500 hover:text-white transition-colors"
            >
              <X size={16} />
            </button>
          </div>

          {/* Moving items preview */}
          {itemCount > 0 && (
            <div className="flex items-center gap-2 flex-wrap">
              {movingItems.slice(0, 4).map((item, idx) => (
                <div
                  key={idx}
                  className="flex items-center gap-1.5 px-2.5 py-1 bg-zinc-800/80 border border-zinc-700/50 rounded-lg text-xs text-zinc-300 max-w-[160px]"
                >
                  {item.thumbnail ? (
                    <img src={item.thumbnail} alt="" className="w-4 h-4 rounded object-cover flex-shrink-0" />
                  ) : (
                    getFileIcon(item.mediaType)
                  )}
                  <span className="truncate">{item.name}</span>
                </div>
              ))}
              {itemCount > 4 && (
                <span className="text-xs text-zinc-500">+{itemCount - 4} more</span>
              )}
            </div>
          )}
        </div>

        {/* ── Body: Two-panel ── */}
        <div className="flex flex-1 min-h-0">
          {/* Left Panel — Tree */}
          <div className="w-[200px] border-r border-zinc-800/80 flex flex-col flex-shrink-0">
            {/* Search */}
            <div className="p-3 pb-2">
              <div className="relative">
                <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-600" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search..."
                  className="w-full bg-zinc-800/60 border border-zinc-700/50 rounded-lg pl-8 pr-2 py-1.5 text-xs text-zinc-300 placeholder-zinc-600 focus:outline-none focus:border-indigo-500/50 transition-colors"
                />
              </div>
            </div>

            <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-0.5">
              {/* Libraries section */}
              {!isPersonal && libraries.length > 0 && (
                <>
                  <div className="px-2 pt-1 pb-1">
                    <span className="text-[10px] font-semibold text-zinc-600 uppercase tracking-wider">Libraries</span>
                  </div>
                  {libraries.map((lib) => (
                    <div
                      key={lib.id}
                      onClick={() => {
                        setSelectedLibraryId(lib.id);
                        setSelectedFolderId(null);
                      }}
                      className={`flex items-center gap-1.5 px-2 py-1.5 rounded-md cursor-pointer transition-all text-sm select-none ${
                        selectedLibraryId === lib.id && selectedFolderId === null
                          ? 'bg-indigo-500/20 text-indigo-300'
                          : 'hover:bg-zinc-800/80 text-zinc-400 hover:text-zinc-200'
                      }`}
                    >
                      <LibraryIcon size={14} className="text-indigo-400/70 flex-shrink-0" />
                      <span className="truncate">{lib.name}</span>
                    </div>
                  ))}
                </>
              )}

              {/* Folders section */}
              <div className="px-2 pt-2 pb-1">
                <span className="text-[10px] font-semibold text-zinc-600 uppercase tracking-wider">Folders</span>
              </div>

              {/* Root */}
              <div
                className={`flex items-center gap-1.5 px-2 py-1.5 rounded-md cursor-pointer transition-all text-sm select-none ${
                  selectedFolderId === null && (isPersonal || libraries.length === 0)
                    ? 'bg-indigo-500/20 text-indigo-300'
                    : 'hover:bg-zinc-800/80 text-zinc-400 hover:text-zinc-200'
                }`}
                onClick={() => setSelectedFolderId(null)}
              >
                <Home size={14} className="text-zinc-500 flex-shrink-0" />
                <span className="truncate">{t('resources.rootFolder')}</span>
              </div>

              {filteredTree.map((folder) => (
                <FolderNode
                  key={folder.id}
                  folder={folder}
                  depth={0}
                  selectedId={selectedFolderId}
                  expandedIds={expandedIds}
                  excludeIds={excludeSet}
                  onSelect={setSelectedFolderId}
                  onToggleExpand={handleToggleExpand}
                />
              ))}
            </div>
          </div>

          {/* Right Panel — Contents of selected */}
          <div className="flex-1 flex flex-col min-w-0">
            {/* Breadcrumb */}
            <div className="flex items-center gap-1 px-4 py-2.5 border-b border-zinc-800/50 text-xs select-none overflow-x-auto">
              {breadcrumb.map((crumb, idx) => (
                <React.Fragment key={idx}>
                  {idx > 0 && <ChevronRight size={11} className="text-zinc-600 flex-shrink-0" />}
                  <button
                    onClick={() => setSelectedFolderId(crumb.id as string | null)}
                    className={`px-1.5 py-0.5 rounded transition-colors whitespace-nowrap flex-shrink-0 ${
                      idx === breadcrumb.length - 1
                        ? 'text-zinc-200 font-medium'
                        : 'text-zinc-500 hover:text-zinc-300'
                    }`}
                  >
                    {crumb.name}
                  </button>
                </React.Fragment>
              ))}
            </div>

            {/* Subfolders */}
            <div className="flex-1 overflow-y-auto p-3 space-y-0.5">
              {rightPanelFolders.length > 0 ? (
                rightPanelFolders.map((folder) => (
                  <div
                    key={folder.id}
                    className="flex items-center gap-2.5 px-3 py-2 rounded-lg cursor-pointer hover:bg-zinc-800/60 text-zinc-300 hover:text-zinc-100 transition-all group"
                    onClick={() => handleRightPanelNav(folder.id)}
                  >
                    <FolderIcon size={16} className="text-amber-400/80 flex-shrink-0" />
                    <span className="text-sm truncate flex-1">{folder.name}</span>
                    <ChevronRight size={14} className="text-zinc-700 group-hover:text-zinc-500 transition-colors flex-shrink-0" />
                  </div>
                ))
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-center py-8">
                  <FolderIcon size={32} className="text-zinc-800 mb-2" />
                  <p className="text-xs text-zinc-600">No subfolders</p>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* ── New folder inline ── */}
        {creatingFolder && (
          <div className="px-5 py-2.5 border-t border-zinc-800/50 flex items-center gap-2 bg-zinc-900/80">
            <FolderPlus size={14} className="text-amber-400 flex-shrink-0" />
            <input
              ref={newFolderRef}
              type="text"
              value={newFolderName}
              onChange={(e) => setNewFolderName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleCreateFolder();
                if (e.key === 'Escape') { setCreatingFolder(false); setNewFolderName(''); }
              }}
              placeholder={t('resources.folderName')}
              className="flex-1 bg-zinc-800/60 border border-zinc-700/50 rounded-lg px-3 py-1.5 text-sm text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500/50"
            />
          </div>
        )}

        {/* ── Footer ── */}
        <div className="flex items-center justify-between px-5 py-3.5 border-t border-zinc-800/80 bg-zinc-950/30">
          <button
            onClick={() => { setCreatingFolder(true); setNewFolderName(''); }}
            className="flex items-center gap-1.5 text-sm text-zinc-500 hover:text-indigo-400 transition-colors"
          >
            <FolderPlus size={14} />
            New Folder
          </button>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-zinc-400 hover:text-white bg-zinc-800/60 hover:bg-zinc-700 border border-zinc-700/50 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={() => onConfirm(selectedFolderId, selectedLibraryId)}
              className="px-4 py-2 text-sm text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors font-medium shadow-lg shadow-indigo-900/20"
            >
              {actionVerb} Here
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
