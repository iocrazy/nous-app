import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Folder as FolderIcon, ChevronRight, ChevronDown, Search, FolderPlus, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Folder, Library } from '../types';
import { fetchFolders, buildFolderTree, createFolder } from '../services/resourceService';
import { fetchLibraries } from '../services/libraryService';

interface FolderPickerModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (targetFolderId: string | null, targetLibraryId?: string | null) => void;
  mode: 'copy' | 'move';
  scopeType: 'personal' | 'team';
  scopeId: string;
  currentLibraryId?: string | null;
  excludeFolderIds?: string[];
}

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
  folder,
  depth,
  selectedId,
  expandedIds,
  excludeIds,
  onSelect,
  onToggleExpand,
}) => {
  if (excludeIds.has(folder.id)) return null;
  const isExpanded = expandedIds.has(folder.id);
  const isSelected = selectedId === folder.id;
  const hasChildren = folder.children && folder.children.length > 0;

  return (
    <div>
      <div
        className={`flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors ${
          isSelected
            ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30'
            : 'hover:bg-zinc-800 text-zinc-300'
        }`}
        style={{ paddingLeft: `${12 + depth * 20}px` }}
        onClick={() => onSelect(folder.id)}
      >
        {hasChildren ? (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggleExpand(folder.id);
            }}
            className="p-0.5 hover:bg-zinc-700 rounded transition-colors"
          >
            {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        ) : (
          <span className="w-5" />
        )}
        <FolderIcon size={16} className="text-amber-400 flex-shrink-0" fill="currentColor" fillOpacity={0.15} />
        <span className="text-sm truncate">{folder.name}</span>
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

export const FolderPickerModal: React.FC<FolderPickerModalProps> = ({
  isOpen,
  onClose,
  onConfirm,
  mode,
  scopeType,
  scopeId,
  currentLibraryId,
  excludeFolderIds = [],
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
      const data = await fetchFolders(scopeType, scopeId, selectedLibraryId);
      setFolders(data);
    } catch {
      /* ignore */
    }
  }, [scopeType, scopeId, selectedLibraryId]);

  useEffect(() => {
    if (!isOpen) return;
    loadFolders();
    if (scopeType === 'team') {
      fetchLibraries(scopeId).then(setLibraries).catch(() => {});
    }
  }, [isOpen, loadFolders, scopeType, scopeId]);

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
        scope_type: scopeType,
        scope_id: scopeId,
      });
      setNewFolderName('');
      setCreatingFolder(false);
      await loadFolders();
    } catch {
      /* ignore */
    }
  }, [newFolderName, selectedFolderId, scopeType, scopeId, loadFolders]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-full max-w-md bg-zinc-900 border border-zinc-700 rounded-2xl shadow-2xl flex flex-col max-h-[80vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-800">
          <h2 className="text-base font-semibold text-white">
            {mode === 'copy' ? t('resources.copyToTitle') : t('resources.moveToTitle')}
          </h2>
          <button
            onClick={onClose}
            className="p-1.5 hover:bg-zinc-800 rounded-lg text-zinc-400 hover:text-white transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {/* Search */}
        <div className="px-5 pt-4">
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t('resources.searchFolders')}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-lg pl-9 pr-3 py-2 text-sm text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
            />
          </div>
        </div>

        {/* Library tabs (team mode) */}
        {scopeType === 'team' && libraries.length > 0 && (
          <div className="px-5 pt-3 flex gap-2 overflow-x-auto">
            {libraries.map((lib) => (
              <button
                key={lib.id}
                onClick={() => {
                  setSelectedLibraryId(lib.id);
                  setSelectedFolderId(null);
                }}
                className={`px-3 py-1.5 text-xs rounded-lg whitespace-nowrap transition-colors ${
                  selectedLibraryId === lib.id
                    ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30'
                    : 'bg-zinc-800 text-zinc-400 hover:text-zinc-200 border border-zinc-700'
                }`}
              >
                {lib.name}
              </button>
            ))}
          </div>
        )}

        {/* Folder tree */}
        <div className="flex-1 overflow-y-auto px-5 py-3 space-y-0.5 min-h-[200px]">
          {/* Root option */}
          <div
            className={`flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors ${
              selectedFolderId === null
                ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30'
                : 'hover:bg-zinc-800 text-zinc-300'
            }`}
            onClick={() => setSelectedFolderId(null)}
          >
            <span className="w-5" />
            <FolderIcon size={16} className="text-amber-400 flex-shrink-0" fill="currentColor" fillOpacity={0.15} />
            <span className="text-sm">{t('resources.rootFolder')}</span>
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

        {/* New folder inline */}
        {creatingFolder && (
          <div className="px-5 py-2 flex items-center gap-2">
            <FolderPlus size={14} className="text-amber-400 flex-shrink-0" />
            <input
              ref={newFolderRef}
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
              className="flex-1 bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-1.5 text-sm text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
            />
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-4 border-t border-zinc-800">
          <button
            onClick={() => {
              setCreatingFolder(true);
              setNewFolderName('');
            }}
            className="flex items-center gap-1.5 text-sm text-indigo-400 hover:text-indigo-300 transition-colors"
          >
            <FolderPlus size={14} />
            {t('resources.newFolderInline')}
          </button>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-zinc-400 hover:text-white bg-zinc-800 hover:bg-zinc-700 rounded-lg transition-colors"
            >
              {t('resources.cancel')}
            </button>
            <button
              onClick={() => onConfirm(selectedFolderId, selectedLibraryId)}
              className="px-4 py-2 text-sm text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors"
            >
              {t('resources.confirm')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
