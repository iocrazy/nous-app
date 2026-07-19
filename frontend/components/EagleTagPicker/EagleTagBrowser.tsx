// frontend/components/EagleTagPicker/EagleTagBrowser.tsx
//
// Pure content of the Eagle tag picker — search bar, optional create row,
// category sidebar (or horizontal tabs on mobile) and the tag grid. This
// component has no portal/positioning logic of its own so it can be
// embedded in a FloatingPanel, a filter dropdown, or any other surface.
//
// Features left to the surrounding container:
//   - Mount / unmount lifecycle (open / close)
//   - Outside-click dismissal
//   - Positioning and resize
//   - Backdrop
//
// Two use cases today:
//   1. EagleTagPicker → FloatingPanel (portal-positioned, with close/settings)
//   2. Resources filter bar (rendered inline inside FilterChip's dropdown)

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Palette, Plus, Search, Settings, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { CategorySidebar } from './CategorySidebar';
import { TagContent } from './TagContent';
import { MergeTagsDialog } from './MergeTagsDialog';
import { SettingsPopover } from './SettingsPopover';
import type { Tag } from '../../types';
import type { PickerSettings } from '../../services/tagPreferencesService';

const TAG_COLORS = [
  '#ef4444', '#f97316', '#eab308', '#22c55e',
  '#14b8a6', '#3b82f6', '#8b5cf6', '#ec4899',
];

export interface EagleTagBrowserProps {
  allTags: Tag[];
  /** Hidden shadow tags (origin === 'note', not yet assigned) excluded from
   *  `allTags`. They stay out of the default list but are (a) matched for the
   *  exact-name check so Create never offers a name that already exists, and
   *  (b) revealed in results when the search text matches them, so a user can
   *  click/Enter to attach one instead of hitting a dead key. */
  shadowTags?: Tag[];
  selectedIds: Set<string>;
  starredIds: string[];
  settings: PickerSettings;
  onToggleTag: (tagId: string) => void;
  onToggleStar: (tagId: string) => void;
  onUpdateSettings: (partial: Partial<PickerSettings>) => void;

  /** When provided, enables right-click "Merge N Tags" on the current selection.
   *  Receives the chosen survivor id and the source ids to merge away. */
  onMergeTags?: (targetId: string, sourceIds: string[]) => Promise<void>;

  /** When provided, renders a "Create \"name\"" row below the search bar.
   *  Hide in contexts where creating tags doesn't make sense (e.g. filter). */
  onCreate?: (name: string, color: string) => Promise<Tag | null>;

  /** When provided, renders a close (X) button in the search row.
   *  Hide when the surrounding surface manages its own dismissal. */
  onClose?: () => void;

  /** Hide the settings (gear) button. Default: show on desktop only. */
  hideSettings?: boolean;

  /** Force mobile layout (horizontal tabs instead of CategorySidebar). When
   *  undefined, detect from viewport width at mount. */
  forceMobileLayout?: boolean;

  /** Autofocus the search input on mount. Default: true on desktop, false on
   *  mobile (matches FloatingPanel behavior). */
  autoFocusSearch?: boolean;

  /** Fixed height container — when set, the browser fills it. Otherwise the
   *  component grows to its intrinsic content height. Useful for dropdowns
   *  that want a bounded height. */
  className?: string;
}

export const EagleTagBrowser: React.FC<EagleTagBrowserProps> = ({
  allTags,
  shadowTags = [],
  selectedIds,
  starredIds,
  settings,
  onToggleTag,
  onToggleStar,
  onUpdateSettings,
  onMergeTags,
  onCreate,
  onClose,
  hideSettings = false,
  forceMobileLayout,
  autoFocusSearch,
  className,
}) => {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [showColorPicker, setShowColorPicker] = useState(false);
  const [newColor, setNewColor] = useState(TAG_COLORS[5]);
  const [mergeOpen, setMergeOpen] = useState(false);
  const selectedUserTags = useMemo(
    () => allTags.filter((t) => selectedIds.has(String(t.id)) && t.type === 'user'),
    [allTags, selectedIds],
  );

  // Detect mobile when not forced
  const [autoMobile, setAutoMobile] = useState(() => window.innerWidth < 640);
  useEffect(() => {
    if (forceMobileLayout !== undefined) return;
    const handler = () => setAutoMobile(window.innerWidth < 640);
    window.addEventListener('resize', handler);
    return () => window.removeEventListener('resize', handler);
  }, [forceMobileLayout]);
  const isMobile = forceMobileLayout ?? autoMobile;

  const shouldAutoFocus = autoFocusSearch ?? !isMobile;

  // Shadow tags whose name/name_zh matches the current search — revealed in the
  // rendered list so they can be clicked/Entered to attach (same match rule as
  // the browser's normal tag search). Empty search keeps shadows hidden.
  const shadowMatches = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return [] as Tag[];
    return shadowTags.filter(
      (tag) =>
        tag.name.toLowerCase().includes(q) ||
        (tag.name_zh ?? '').toLowerCase().includes(q),
    );
  }, [search, shadowTags]);

  // Tags actually rendered: the visible set plus any search-revealed shadows.
  const visibleTags = useMemo(
    () => (shadowMatches.length ? [...allTags, ...shadowMatches] : allTags),
    [allTags, shadowMatches],
  );

  // Groups / counts
  const { groups, totalCount, uncategorizedCount } = useMemo(() => {
    const groupMap = new Map<string, number>();
    let uncat = 0;
    for (const tag of visibleTags) {
      if (tag.group_name) {
        groupMap.set(tag.group_name, (groupMap.get(tag.group_name) || 0) + 1);
      } else {
        uncat++;
      }
    }
    return {
      groups: Array.from(groupMap.entries()).map(([name, count]) => ({ name, count })),
      totalCount: visibleTags.length,
      uncategorizedCount: uncat,
    };
  }, [visibleTags]);

  // Exact-name match against visible tags AND hidden shadow tags — a hidden
  // shadow tag's exact name must not offer Create (backend would 409).
  const exactShadowMatch = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return null;
    return (
      shadowTags.find(
        (tag) =>
          tag.name.toLowerCase() === q ||
          (tag.name_zh ?? '').toLowerCase() === q,
      ) ?? null
    );
  }, [search, shadowTags]);

  // Create affordance: show only when search has no exact match (in either the
  // visible tags or the hidden shadow tags).
  const noExactMatch = useMemo(() => {
    if (!search.trim()) return false;
    const q = search.trim().toLowerCase();
    if (exactShadowMatch) return false;
    return !allTags.some(
      (tag) =>
        tag.name.toLowerCase() === q ||
        (tag.name_zh ?? '').toLowerCase() === q,
    );
  }, [search, allTags, exactShadowMatch]);

  const handleCreate = useCallback(async () => {
    const name = search.trim();
    if (!name || !onCreate) return;
    const created = await onCreate(name, newColor);
    if (created) {
      setSearch('');
      setShowColorPicker(false);
    }
  }, [search, newColor, onCreate]);

  const searchBar = (
    <div className="flex items-center gap-2 px-3 py-2 border-b border-ink-800">
      <div className="flex-1 relative">
        <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-ink-500" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t('resources.searchOrCreate', 'Search or create...')}
          className="w-full bg-ink-800 border border-ink-700/50 rounded pl-7 pr-2 py-1.5 text-xs text-ink-200 placeholder-ink-600 focus:outline-none focus:border-indigo-500/50"
          autoFocus={shouldAutoFocus}
          onKeyDown={(e) => {
            if (e.nativeEvent.isComposing) return; // IME 组合中的 Enter 属于输入法
            if (e.key !== 'Enter') return;
            if (noExactMatch && onCreate) {
              e.preventDefault();
              handleCreate();
            } else if (exactShadowMatch) {
              // Search exactly matches a hidden shadow tag — attach it instead
              // of dead-keying (no Create row is shown for it).
              e.preventDefault();
              onToggleTag(String(exactShadowMatch.id));
            }
          }}
        />
      </div>
      {!hideSettings && !isMobile && (
        <div className="relative">
          <button
            onClick={() => setShowSettings(!showSettings)}
            className="p-1.5 rounded text-ink-400 hover:text-ink-200 hover:bg-ink-800 transition-colors"
          >
            <Settings size={14} />
          </button>
          {showSettings && (
            <SettingsPopover settings={settings} onUpdate={onUpdateSettings} onClose={() => setShowSettings(false)} />
          )}
        </div>
      )}
      {onClose && (
        <button
          onClick={onClose}
          className="p-1.5 rounded text-ink-400 hover:text-ink-200 hover:bg-ink-800 transition-colors"
        >
          <X size={14} />
        </button>
      )}
    </div>
  );

  const createOption = noExactMatch && onCreate && (
    <div className="border-b border-ink-800 px-3 py-1.5">
      {!showColorPicker ? (
        <button
          onClick={() => setShowColorPicker(true)}
          className="flex items-center gap-2 w-full px-2 py-1.5 text-xs text-ink-300 hover:bg-ink-800 rounded transition-colors"
        >
          <Plus size={12} className="text-indigo-400" />
          <span>Create &quot;{search.trim()}&quot;</span>
        </button>
      ) : (
        <div className="space-y-1.5 py-1">
          <div className="flex items-center gap-2">
            <Palette size={10} className="text-ink-500 shrink-0" />
            <div className="flex gap-1">
              {TAG_COLORS.map((c) => (
                <button
                  key={c}
                  onClick={() => setNewColor(c)}
                  className={`w-5 h-5 sm:w-4 sm:h-4 rounded-full border-2 transition-all ${
                    newColor === c ? 'border-white scale-110' : 'border-transparent'
                  }`}
                  style={{ backgroundColor: c }}
                />
              ))}
            </div>
          </div>
          <button
            onClick={handleCreate}
            className="w-full py-1.5 text-xs bg-indigo-600 hover:bg-indigo-500 text-white rounded transition-colors"
          >
            Create &quot;{search.trim()}&quot;
          </button>
        </div>
      )}
    </div>
  );

  const rootClass = `flex flex-col min-h-0 ${className ?? ''}`.trim();

  if (isMobile) {
    return (
      <div className={rootClass}>
        {searchBar}
        {createOption}

        {/* Horizontal category tabs */}
        <div className="flex items-center gap-1 px-3 py-2 border-b border-ink-800 overflow-x-auto no-scrollbar">
          <button
            onClick={() => setSelectedGroup(null)}
            className={`shrink-0 px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
              selectedGroup === null ? 'bg-indigo-500/20 text-indigo-300' : 'bg-ink-800 text-ink-400'
            }`}
          >
            All {totalCount}
          </button>
          <button
            onClick={() => setSelectedGroup('__uncategorized__')}
            className={`shrink-0 px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
              selectedGroup === '__uncategorized__' ? 'bg-indigo-500/20 text-indigo-300' : 'bg-ink-800 text-ink-400'
            }`}
          >
            Uncategorized {uncategorizedCount}
          </button>
          {groups.map((g) => (
            <button
              key={g.name}
              onClick={() => setSelectedGroup(g.name)}
              className={`shrink-0 px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
                selectedGroup === g.name ? 'bg-indigo-500/20 text-indigo-300' : 'bg-ink-800 text-ink-400'
              }`}
            >
              {g.name} {g.count}
            </button>
          ))}
        </div>

        {/* Full-width tag content */}
        <TagContent
          allTags={visibleTags}
          selectedIds={selectedIds}
          starredIds={starredIds}
          settings={settings}
          selectedGroup={selectedGroup}
          search={search}
          onToggleTag={onToggleTag}
          onToggleStar={onToggleStar}
          selectedUserTagCount={onMergeTags ? selectedUserTags.length : 0}
          onRequestMerge={onMergeTags ? () => setMergeOpen(true) : undefined}
        />
        {onMergeTags && mergeOpen && selectedUserTags.length >= 2 && (
          <MergeTagsDialog
            tags={selectedUserTags}
            onClose={() => setMergeOpen(false)}
            onConfirm={async (target, sources) => {
              await onMergeTags(target, sources);
              setMergeOpen(false);
            }}
          />
        )}
      </div>
    );
  }

  // Desktop: sidebar + content
  return (
    <div className={rootClass}>
      {searchBar}
      {createOption}
      <div className="flex flex-1 min-h-0">
        <CategorySidebar
          totalCount={totalCount}
          uncategorizedCount={uncategorizedCount}
          groups={groups}
          selectedGroup={selectedGroup}
          onSelectGroup={setSelectedGroup}
        />
        <TagContent
          allTags={visibleTags}
          selectedIds={selectedIds}
          starredIds={starredIds}
          settings={settings}
          selectedGroup={selectedGroup}
          search={search}
          onToggleTag={onToggleTag}
          onToggleStar={onToggleStar}
          selectedUserTagCount={onMergeTags ? selectedUserTags.length : 0}
          onRequestMerge={onMergeTags ? () => setMergeOpen(true) : undefined}
        />
      </div>
      {onMergeTags && mergeOpen && selectedUserTags.length >= 2 && (
        <MergeTagsDialog
          tags={selectedUserTags}
          onClose={() => setMergeOpen(false)}
          onConfirm={async (target, sources) => {
            await onMergeTags(target, sources);
            setMergeOpen(false);
          }}
        />
      )}
    </div>
  );
};
