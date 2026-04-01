import React, { useState, useEffect, useRef } from 'react';
import {
  Share2,
  Trash2,
  Download,
  FolderOpen,
  Plus,
  Zap,
  BookOpen,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Pencil,
  Loader2,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useResourcesContext } from '../contexts/ResourcesContext';
import type { SmartCollection } from '../types';

// ─── Props ─────────────────────────────────────────────

export interface ResourcesSidebarProps {
  onSidebarDragOver: (e: React.DragEvent) => void;
  onSidebarDrop: (e: React.DragEvent, targetFolderId: string | null) => void;
  onCreateSmartFolder: () => void;
  onEditSmartFolder: (sf: SmartCollection) => void;
  onDeleteSmartFolder: (id: string) => void;
  onCreateLibrary: (name: string) => Promise<void>;
  onNewFolder: () => void;
  onSmartFolderContextMenu: (e: React.MouseEvent, sf: SmartCollection) => void;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}

// ─── Helper ────────────────────────────────────────────

const sidebarItemClass = (active: boolean) =>
  `w-full flex items-center gap-3 px-3 py-2 text-[13px] rounded-lg transition-colors text-left cursor-pointer select-none ${
    active ? 'bg-zinc-800/80 text-white font-medium' : 'text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200'
  }`;

// ─── Component ─────────────────────────────────────────

export const ResourcesSidebar: React.FC<ResourcesSidebarProps> = ({
  onSidebarDragOver,
  onSidebarDrop,
  onCreateSmartFolder,
  onEditSmartFolder,
  onDeleteSmartFolder,
  onCreateLibrary,
  onNewFolder,
  onSmartFolderContextMenu,
  collapsed = false,
  onToggleCollapse,
}) => {
  const { t } = useTranslation();
  const {
    scopeType,
    sidebarView,
    selectedFolderId,
    selectedSmartFolderId,
    selectedLibraryId,
    libraries,
    setLibraries,
    smartFolders,
    isResourcesView,
    isRecycleView,
    isSharedView,
    isDownloadsView,
    resPath,
    navigate,
  } = useResourcesContext();

  // ── Local UI state ──
  const [librariesExpanded, setLibrariesExpanded] = useState(true);
  const [smartFoldersExpanded, setSmartFoldersExpanded] = useState(true);
  const [creatingLibrary, setCreatingLibrary] = useState(false);
  const [newLibraryName, setNewLibraryName] = useState('');
  const [savingLibrary, setSavingLibrary] = useState(false);
  const newLibraryInputRef = useRef<HTMLInputElement>(null);

  // Auto-focus library name input
  useEffect(() => {
    if (creatingLibrary && newLibraryInputRef.current) {
      newLibraryInputRef.current.focus();
    }
  }, [creatingLibrary]);

  // ── Handlers ──

  const handleCreateLibrary = async () => {
    const trimmed = newLibraryName.trim();
    if (!trimmed || savingLibrary) return;
    setSavingLibrary(true);
    try {
      await onCreateLibrary(trimmed);
      setNewLibraryName('');
      setCreatingLibrary(false);
    } catch {
      // Keep input open on error
    } finally {
      setSavingLibrary(false);
    }
  };

  return (
    collapsed ? (
      <div className="max-md:hidden w-4 shrink-0" style={{ position: 'relative', height: '100%' }}>
        {onToggleCollapse && (
          <button
            onClick={onToggleCollapse}
            className="absolute top-1/2 -translate-y-1/2 left-0 z-10 w-4 h-10 flex items-center justify-center rounded-r-md bg-zinc-800/80 text-zinc-500 hover:text-zinc-200 hover:bg-zinc-700 transition-colors"
            title="Expand sidebar"
          >
            <ChevronRight size={12} />
          </button>
        )}
      </div>
    ) : (
    <div className="group hidden md:flex md:static w-52 shrink-0 border-r border-zinc-800/40 flex-col pt-16" style={{ position: 'relative' }}>
      {/* Navigation */}
      <div className="flex-1 overflow-y-auto px-2 pt-4 pb-4 space-y-0.5">
        {/* ── Top section: Shared / Recycle Bin ── */}
        <button
          onClick={() => navigate(resPath('/resources/shared'))}
          className={sidebarItemClass(isSharedView)}
        >
          <Share2 size={15} className="shrink-0 opacity-70" />
          <span className="flex-1">{t('resources.sharedManagement')}</span>
        </button>

        <button
          onClick={() => navigate(resPath('/resources/recycle'))}
          className={sidebarItemClass(isRecycleView)}
        >
          <Trash2 size={15} className="shrink-0 opacity-70" />
          <span className="flex-1">{t('resources.recycleBin')}</span>
        </button>

        {/* ── Divider ── */}
        <div className="mx-1 my-2.5 border-t border-zinc-800/60" />

        {/* ── Main section: Team Libraries / Personal Resources ── */}
        {scopeType === 'team' ? (
          <>
            {/* ── Library — collapsible parent item ── */}
            <div className="flex items-center justify-between pr-1">
              <button
                onClick={() => setLibrariesExpanded(!librariesExpanded)}
                className={sidebarItemClass(isResourcesView && !!selectedLibraryId && !librariesExpanded)}
              >
                <BookOpen size={15} className="shrink-0 opacity-70" />
                <span className="flex-1 truncate">{t('resources.library')}</span>
                {libraries.length > 0 && (
                  <span className="text-[10px] min-w-[18px] h-[18px] flex items-center justify-center rounded-full bg-zinc-800 text-zinc-500 font-medium">{libraries.length}</span>
                )}
                <ChevronDown
                  size={12}
                  className={`shrink-0 text-zinc-500 transition-transform duration-200 ${librariesExpanded ? '' : '-rotate-90'}`}
                />
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); setCreatingLibrary(true); setLibrariesExpanded(true); }}
                className="p-1 text-zinc-600 hover:text-zinc-300 hover:bg-zinc-800 rounded-md transition-colors shrink-0"
                title={t('resources.newLibrary')}
              >
                <Plus size={14} />
              </button>
            </div>

            {/* Library children (indented with left border) */}
            {librariesExpanded && (
              <div className="ml-3 border-l border-zinc-700/40 pl-0.5">
                {libraries.map((lib) => (
                  <button
                    key={lib.id}
                    onClick={() => navigate(resPath(`/resources/library/${lib.id}`))}
                    className={`group ${sidebarItemClass(isResourcesView && selectedLibraryId === String(lib.id))}`}
                  >
                    <BookOpen size={14} className="shrink-0 opacity-60" />
                    <span className="truncate flex-1">{lib.name}</span>
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

                {/* Create library button */}
                {!creatingLibrary && (
                  <button
                    onClick={() => setCreatingLibrary(true)}
                    className="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-zinc-600 hover:text-zinc-400 rounded-lg transition-colors text-left"
                  >
                    <Plus size={14} className="shrink-0 opacity-70" />
                    <span>{t('resources.newLibrary')}</span>
                  </button>
                )}
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

            {/* My Resources with + */}
            <div className="flex items-center justify-between pr-1">
              <button
                onClick={() => navigate(resPath('/resources'))}
                onDragOver={onSidebarDragOver}
                onDrop={(e) => onSidebarDrop(e, null)}
                className={sidebarItemClass(isResourcesView && selectedFolderId === null && !selectedSmartFolderId)}
              >
                <FolderOpen size={15} className="shrink-0 opacity-70" />
                <span>{t('resources.myResources')}</span>
              </button>
              <button
                onClick={() => {
                  navigate(resPath('/resources'));
                  onNewFolder();
                }}
                className="p-1 text-zinc-600 hover:text-zinc-300 hover:bg-zinc-800 rounded-md transition-colors shrink-0"
                title={t('resources.newFolder')}
              >
                <Plus size={14} />
              </button>
            </div>


          </>
        )}

        {/* ── Divider ── */}
        <div className="mx-1 my-2.5 border-t border-zinc-800/60" />

        {/* ── Smart Folders — collapsible parent item ── */}
        <div className="flex items-center justify-between pr-1">
          <button
            onClick={() => setSmartFoldersExpanded(!smartFoldersExpanded)}
            className={sidebarItemClass(isResourcesView && !!selectedSmartFolderId && !smartFoldersExpanded)}
          >
            <Zap size={15} className="shrink-0 opacity-70" />
            <span className="flex-1 truncate">{t('resources.smartFolders')}</span>
            {smartFolders.length > 0 && (
              <span className="text-[10px] min-w-[18px] h-[18px] flex items-center justify-center rounded-full bg-zinc-800 text-zinc-500 font-medium">{smartFolders.length}</span>
            )}
            <ChevronDown
              size={12}
              className={`shrink-0 text-zinc-500 transition-transform duration-200 ${smartFoldersExpanded ? '' : '-rotate-90'}`}
            />
          </button>
          <button
            className="p-1 text-zinc-600 hover:text-zinc-300 hover:bg-zinc-800 rounded-md transition-colors shrink-0"
            title={t('smartFolder.createTitle')}
            onClick={(e) => { e.stopPropagation(); onCreateSmartFolder(); }}
          >
            <Plus size={14} />
          </button>
        </div>

        {/* Smart folder children (indented with left border) */}
        {smartFoldersExpanded && (
          <div className="ml-3 border-l border-zinc-700/40 pl-0.5">
            {smartFolders.map((sf) => (
              <button
                key={sf.id}
                onClick={() => navigate(resPath(`/resources/smart/${sf.id}`))}
                onContextMenu={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  onSmartFolderContextMenu(e, sf);
                }}
                className={`group ${sidebarItemClass(isResourcesView && selectedSmartFolderId === String(sf.id))}`}
              >
                <Zap size={14} className="shrink-0 opacity-60" />
                <span className="truncate flex-1">{sf.name}</span>
                <span
                  className="opacity-0 group-hover:opacity-100 ml-auto text-zinc-600 hover:text-zinc-300 transition-all"
                  onClick={(e) => {
                    e.stopPropagation();
                    onEditSmartFolder(sf);
                  }}
                  title={t('smartFolder.editSmartFolder')}
                >
                  <Pencil size={12} />
                </span>
              </button>
            ))}

            {smartFolders.length === 0 && (
              <button
                onClick={() => onCreateSmartFolder()}
                className="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-zinc-600 hover:text-zinc-400 rounded-lg transition-colors text-left"
              >
                <Plus size={14} className="shrink-0 opacity-70" />
                <span>{t('smartFolder.createTitle')}</span>
              </button>
            )}
          </div>
        )}
      </div>

      {/* Collapse toggle — same as project sidebar */}
      {onToggleCollapse && (
        <button
          onClick={onToggleCollapse}
          className="absolute top-1/2 -translate-y-1/2 right-0 z-10 w-4 h-10 flex items-center justify-center rounded-l-md bg-zinc-800/80 text-zinc-500 hover:text-zinc-200 hover:bg-zinc-700 transition-colors opacity-0 group-hover:opacity-100"
          title="Collapse sidebar"
        >
          <ChevronLeft size={12} />
        </button>
      )}
    </div>
    )
  );
};
