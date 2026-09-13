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
  Sparkles,
  Boxes,
  Users,
  MapPin,
  Package,
  Shirt,
  FileText,
  AudioLines,
} from 'lucide-react';
import { ASSET_TYPES, type AssetType } from './assets/assetSlots';
import { formatCappedCount } from '../utils/cappedCount';
import { useTranslation } from 'react-i18next';
import { useResourcesContext } from '../contexts/ResourcesContext';
import { SecondarySidebarHeader } from './layout/SecondarySidebarHeader';
import { useModuleStatus } from '../hooks/useModuleStatus';
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
    active
      ? 'bg-[var(--accent-soft)] text-[var(--accent-text)] font-medium'
      : 'text-content-2 hover:bg-island-2 hover:text-content-2'
  }`;

// type → rail icon. Keyed by AssetType so a seventh type added to the slot
// table fails to compile here rather than rendering an iconless row.
// Lucide only (CLAUDE.md: no emoji in UI).
const ASSET_TYPE_ICON: Record<AssetType, React.ComponentType<{ size?: number; className?: string }>> = {
  character: Users,
  location: MapPin,
  prop: Package,
  costume: Shirt,
  prompt: FileText,
  audio: AudioLines,
};

// Section label (island redesign D3) — visual grouping only, no behavior.
const SectionLabel: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="px-3 pt-1.5 pb-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-content-4 select-none">
    {children}
  </div>
);

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
    isPersonal,
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
    isGeneratedView,
    isAssetsView,
    selectedAssetType,
    resPath,
    navigate,
    myResourcesCount,
    downloadsCount,
    generatedUnreviewedCount,
    assetCounts,
    promptEntryCount,
  } = useResourcesContext();

  // Module Control Center display switch — My Downloads is the media-parser
  // surface inside Resources, so it hides with that module.
  const { visible: mediaParserVisible } = useModuleStatus('media-parser');

  // Island redesign: neutral ink surfaces/borders/text aligned to the mock
  // --content/--line/--island ladder.
  const cText200 = 'text-content-2';
  const cText400 = 'text-content-2';
  const cText500 = 'text-content-3';
  const cText600 = 'text-content-4';
  const cHover200 = 'hover:text-content-2';
  const cHover300 = 'hover:text-content-2';
  const cHover400 = 'hover:text-content-2';
  const cBadgeBg = 'bg-island-2';
  const cHandleBg = 'bg-island-2';
  const cHoverSurface800 = 'hover:bg-island-2';
  const cHoverSurface700 = 'hover:bg-island-2';
  const cBorderRail = 'border-line';
  const cBorderSection = 'border-line';
  const cBorderChild = 'border-line';
  const cInputBg = 'bg-island-2';
  const cInputBorder = 'border-line';
  const cPlaceholder = 'placeholder-content-4';

  // ── Local UI state ──
  const [librariesExpanded, setLibrariesExpanded] = useState(true);
  const [assetsExpanded, setAssetsExpanded] = useState(true);
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
            className={`absolute top-1/2 -translate-y-1/2 left-0 z-10 w-4 h-10 flex items-center justify-center rounded-r-md ${cHandleBg} ${cText500} ${cHover200} ${cHoverSurface700} transition-colors`}
            title="Expand sidebar"
          >
            <ChevronRight size={12} />
          </button>
        )}
      </div>
    ) : (
    <div className={`group hidden md:flex md:static w-52 shrink-0 border-r ${cBorderRail} flex-col`} style={{ position: 'relative' }}>
      {/* Header — shared rail title. The rail used to carry an extra pt-4 of
          its own on top of the header's, putting this module's title 16px
          lower than every other rail's. */}
      <SecondarySidebarHeader title={t('sidebar.resources', 'Resources')} />
      {/* Navigation */}
      <div className="flex-1 overflow-y-auto px-2 pb-4 space-y-0.5">
        {/* ── Locations (island redesign D3: high-frequency content first) ── */}
        <SectionLabel>{t('resources.sectionLocations', 'Locations')}</SectionLabel>

        {/* ── Main section: Team Libraries / Personal Resources ── */}
        {!isPersonal ? (
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
                  <span className={`text-[10px] min-w-[18px] h-[18px] flex items-center justify-center rounded-full ${cBadgeBg} ${cText500} font-medium`}>{libraries.length}</span>
                )}
                <ChevronDown
                  size={12}
                  className={`shrink-0 ${cText500} transition-transform duration-200 ${librariesExpanded ? '' : '-rotate-90'}`}
                />
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); setCreatingLibrary(true); setLibrariesExpanded(true); }}
                className={`p-1 ${cText600} ${cHover300} ${cHoverSurface800} rounded-md transition-colors shrink-0`}
                title={t('resources.newLibrary')}
              >
                <Plus size={14} />
              </button>
            </div>

            {/* Library children (indented with left border) */}
            {librariesExpanded && (
              <div className={`ml-3 border-l ${cBorderChild} pl-0.5`}>
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
                        if (e.key === 'Enter' && !e.nativeEvent.isComposing) handleCreateLibrary();
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
                      className={`flex-1 min-w-0 ${cInputBg} border ${cInputBorder} rounded px-2 py-1 text-xs ${cText200} ${cPlaceholder} focus:outline-none focus:border-indigo-500 disabled:opacity-50`}
                    />
                    {savingLibrary && (
                      <Loader2 size={12} className={`animate-spin ${cText400}`} />
                    )}
                  </div>
                )}

                {/* Create library button */}
                {!creatingLibrary && (
                  <button
                    onClick={() => setCreatingLibrary(true)}
                    className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] ${cText600} ${cHover400} rounded-lg transition-colors text-left`}
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
            {/* Personal mode: My Downloads + My Uploads */}
            {mediaParserVisible && (
              <button
                onClick={() => navigate(resPath('/resources/downloads'))}
                className={sidebarItemClass(isDownloadsView)}
              >
                <Download size={15} className="shrink-0 opacity-70" />
                <span className="flex-1 truncate">{t('resources.downloads')}</span>
                {downloadsCount !== null && downloadsCount > 0 && (
                  <span className={`text-[11px] ${cText500} tabular-nums`}>{formatCappedCount(downloadsCount)}</span>
                )}
              </button>
            )}

            {/* My Uploads with + */}
            <div className="flex items-center justify-between pr-1">
              <button
                onClick={() => navigate(resPath('/resources'))}
                onDragOver={onSidebarDragOver}
                onDrop={(e) => onSidebarDrop(e, null)}
                className={sidebarItemClass(isResourcesView && selectedFolderId === null && !selectedSmartFolderId)}
              >
                <FolderOpen size={15} className="shrink-0 opacity-70" />
                <span className="flex-1 truncate">{t('resources.myResources')}</span>
                {myResourcesCount !== null && myResourcesCount > 0 && (
                  <span className={`text-[11px] ${cText500} tabular-nums`}>{formatCappedCount(myResourcesCount)}</span>
                )}
              </button>
              <button
                onClick={() => {
                  navigate(resPath('/resources'));
                  onNewFolder();
                }}
                className={`p-1 ${cText600} ${cHover300} ${cHoverSurface800} rounded-md transition-colors shrink-0`}
                title={t('resources.newFolder')}
              >
                <Plus size={14} />
              </button>
            </div>

          </>
        )}

        {/* Generated — the AI-output inbox (replaces Project Assets). Rendered
            OUTSIDE the personal/team split: `generated_media.scope_id` IS a
            team id, so the route, the view flag and fetchGeneratedCounts all
            work in team scope — keeping the button inside the personal branch
            made the inbox reachable by URL but not by clicking (spec §2.6/§6.1
            make it a fixed rail position). Sitting just after the ternary puts
            it directly below the file-store block in BOTH scopes: My Uploads
            in personal, the Library group in team.
            The pill counts UNREVIEWED items only, and is omitted entirely when
            the count is 0 or not yet known: a "0" badge would be a permanent
            decoration, and a badge on `null` would assert a number we failed
            to fetch. */}
        <button
          onClick={() => navigate(resPath('/resources/generated'))}
          className={sidebarItemClass(isGeneratedView)}
        >
          <Sparkles size={15} className="shrink-0 opacity-70" />
          <span className="flex-1 truncate">{t('resources.generated', 'Generated')}</span>
          {generatedUnreviewedCount !== null && generatedUnreviewedCount > 0 && (
            <span className="text-[10px] min-w-[18px] h-[18px] px-1 flex items-center justify-center rounded-full bg-warn-soft text-warn border border-warn-line font-medium tabular-nums">
              {formatCappedCount(generatedUnreviewedCount)}
            </span>
          )}
        </button>

        {/* Assets — the asset library (P2). Like Generated, rendered OUTSIDE
            the personal/team split: `assets.scope_id` IS a team id, so the
            routes, the view flag and fetchAssetCounts all work in both scopes.
            The parent row navigates to the landing page AND toggles the
            children, because a group whose only affordance is expansion hides
            the "everything" view behind six type-specific ones. */}
        <div className="flex items-center justify-between pr-1">
          <button
            onClick={() => {
              navigate(resPath('/resources/assets'));
              setAssetsExpanded(true);
            }}
            className={sidebarItemClass(isAssetsView && !selectedAssetType)}
          >
            <Boxes size={15} className="shrink-0 opacity-70" />
            <span className="flex-1 truncate">{t('resources.assets', 'Assets')}</span>
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              setAssetsExpanded((v) => !v);
            }}
            className={`p-1 ${cText600} ${cHover300} ${cHoverSurface800} rounded-md transition-colors shrink-0`}
            // NOT `resources.assets`: the navigation row beside it already
            // carries that name, and two buttons with the same accessible name
            // in one group is ambiguous to a screen reader AND to any
            // name-based locator. `e2e-prod/walkthrough.spec.ts` had to take
            // `.first()` to disambiguate — a positional workaround for what is
            // really a naming defect.
            aria-label={t('resources.assetsExpand', 'Expand Assets')}
            aria-expanded={assetsExpanded}
          >
            <ChevronDown
              size={12}
              className={`shrink-0 ${cText500} transition-transform duration-200 ${assetsExpanded ? '' : '-rotate-90'}`}
            />
          </button>
        </div>

        {/* One child per asset type, in slot-table order. The count rides as
            plain text (not a pill) — these are inventory sizes, not a backlog
            demanding attention, and six warn-toned pills would drown the one
            pill above that does mean "act on me".
            A count is omitted when it is 0 or not yet known: `assetCounts` is
            null while loading OR after a failed fetch, and rendering six zeros
            on a fetch we never got back would assert an empty library. */}
        {assetsExpanded && (
          <div className={`ml-3 border-l ${cBorderChild} pl-0.5`}>
            {ASSET_TYPES.map((type) => {
              const Icon = ASSET_TYPE_ICON[type];
              // Prompts names a different page (the unified catalog, presets
              // included), so it carries a different number. Every other type
              // is an asset shelf and counts asset rows.
              const count = type === 'prompt' ? (promptEntryCount ?? undefined) : assetCounts?.[type];
              return (
                <button
                  key={type}
                  onClick={() => navigate(resPath(`/resources/assets/${type}`))}
                  className={sidebarItemClass(isAssetsView && selectedAssetType === type)}
                >
                  <Icon size={14} className="shrink-0 opacity-60" />
                  <span className="flex-1 truncate">{t(`assets.types.${type}`)}</span>
                  {count !== undefined && count > 0 && (
                    <span className={`text-[11px] ${cText500} tabular-nums`}>
                      {formatCappedCount(count)}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        )}

        {/* ── Divider ── */}
        <div className={`mx-1 my-2.5 border-t ${cBorderSection}`} />

        {/* ── Smart Folders — collapsible parent item ── */}
        <div className="flex items-center justify-between pr-1">
          <button
            onClick={() => setSmartFoldersExpanded(!smartFoldersExpanded)}
            className={sidebarItemClass(isResourcesView && !!selectedSmartFolderId && !smartFoldersExpanded)}
          >
            <Zap size={15} className="shrink-0 opacity-70" />
            <span className="flex-1 truncate">{t('resources.smartFolders')}</span>
            {smartFolders.length > 0 && (
              <span className={`text-[10px] min-w-[18px] h-[18px] flex items-center justify-center rounded-full ${cBadgeBg} ${cText500} font-medium`}>{smartFolders.length}</span>
            )}
            <ChevronDown
              size={12}
              className={`shrink-0 ${cText500} transition-transform duration-200 ${smartFoldersExpanded ? '' : '-rotate-90'}`}
            />
          </button>
          <button
            className={`p-1 ${cText600} ${cHover300} ${cHoverSurface800} rounded-md transition-colors shrink-0`}
            title={t('smartFolder.createTitle')}
            onClick={(e) => { e.stopPropagation(); onCreateSmartFolder(); }}
          >
            <Plus size={14} />
          </button>
        </div>

        {/* Smart folder children (indented with left border) */}
        {smartFoldersExpanded && (
          <div className={`ml-3 border-l ${cBorderChild} pl-0.5`}>
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
                  className={`opacity-0 group-hover:opacity-100 ml-auto ${cText600} ${cHover300} transition-all`}
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
                className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] ${cText600} ${cHover400} rounded-lg transition-colors text-left`}
              >
                <Plus size={14} className="shrink-0 opacity-70" />
                <span>{t('smartFolder.createTitle')}</span>
              </button>
            )}
          </div>
        )}
      </div>

      {/* ── Manage section — pinned to the bottom (island redesign D3:
          low-frequency management items sink below the content groups) ── */}
      <div className={`px-2 pb-3 pt-2 border-t ${cBorderSection} space-y-0.5`}>
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
      </div>

      {/* Collapse toggle — same as project sidebar */}
      {onToggleCollapse && (
        <button
          onClick={onToggleCollapse}
          className={`absolute top-1/2 -translate-y-1/2 right-0 z-10 w-4 h-10 flex items-center justify-center rounded-l-md ${cHandleBg} ${cText500} ${cHover200} ${cHoverSurface700} transition-colors opacity-0 group-hover:opacity-100`}
          title="Collapse sidebar"
        >
          <ChevronLeft size={12} />
        </button>
      )}
    </div>
    )
  );
};
