import React, { useMemo, useState, useCallback } from 'react';
import { Star, Flame, FolderOpen } from 'lucide-react';
import { TagRow } from './TagRow';
import type { Tag } from '../../types';
import type { PickerSettings } from '../../services/tagPreferencesService';

interface TagContentProps {
  allTags: Tag[];
  selectedIds: Set<string>;
  starredIds: string[];
  settings: PickerSettings;
  selectedGroup: string | null;
  search: string;
  onToggleTag: (tagId: string) => void;
  onToggleStar: (tagId: string) => void;
  /** Count of currently-selected user tags eligible for merge. From the browser. */
  selectedUserTagCount?: number;
  /** Opens the merge dialog for the current selection. */
  onRequestMerge?: () => void;
}

export const TagContent: React.FC<TagContentProps> = ({
  allTags,
  selectedIds,
  starredIds,
  settings,
  selectedGroup,
  search,
  onToggleTag,
  onToggleStar,
  selectedUserTagCount = 0,
  onRequestMerge,
}) => {
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; tagId: string } | null>(null);

  const starredSet = useMemo(() => new Set(starredIds), [starredIds]);

  // Filter by search
  const searchFiltered = useMemo(() => {
    if (!search) return allTags;
    const q = search.toLowerCase();
    return allTags.filter(
      (t) => t.name.toLowerCase().includes(q) || (t.name_zh && t.name_zh.toLowerCase().includes(q)),
    );
  }, [allTags, search]);

  // Filter by group
  const groupFiltered = useMemo(() => {
    if (selectedGroup === null) return searchFiltered;
    if (selectedGroup === '__uncategorized__') return searchFiltered.filter((t) => !t.group_name);
    return searchFiltered.filter((t) => t.group_name === selectedGroup);
  }, [searchFiltered, selectedGroup]);

  // Starred tags
  const starredTags = useMemo(
    () => (settings.showStarred ? groupFiltered.filter((t) => starredSet.has(String(t.id))) : []),
    [groupFiltered, starredSet, settings.showStarred],
  );

  // Frequently used (top 6 by media_count, only when not searching)
  const frequentTags = useMemo(() => {
    if (!settings.showRecently || search) return [];
    return [...groupFiltered]
      .filter((t) => (t.media_count ?? t.video_count ?? 0) > 0)
      .sort((a, b) => (b.media_count ?? b.video_count ?? 0) - (a.media_count ?? a.video_count ?? 0))
      .slice(0, 6);
  }, [groupFiltered, settings.showRecently, search]);

  // Grouped tags
  const grouped = useMemo(() => {
    const map = new Map<string, Tag[]>();
    const ungrouped: Tag[] = [];
    for (const tag of groupFiltered) {
      const group = tag.group_name;
      if (!group) ungrouped.push(tag);
      else {
        if (!map.has(group)) map.set(group, []);
        map.get(group)!.push(tag);
      }
    }
    const entries = Array.from(map.entries());
    if (ungrouped.length > 0) entries.push(['Uncategorized', ungrouped]);
    return entries;
  }, [groupFiltered]);

  const handleContextMenu = useCallback((e: React.MouseEvent, tagId: string) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY, tagId });
  }, []);

  const closeContextMenu = useCallback(() => setContextMenu(null), []);

  const colWidthClass = settings.columnWidth === 'small' ? 'grid-cols-3' : settings.columnWidth === 'large' ? 'grid-cols-1' : 'grid-cols-2';
  const gridClass = settings.layout === 'grid' ? 'flex flex-wrap gap-1' : `grid ${colWidthClass} gap-x-1`;

  const renderTag = (tag: Tag) => (
    <TagRow
      key={tag.id}
      tag={tag}
      isSelected={selectedIds.has(String(tag.id))}
      isStarred={starredSet.has(String(tag.id))}
      showCount={settings.showCount}
      onClick={() => onToggleTag(String(tag.id))}
      onContextMenu={(e) => handleContextMenu(e, String(tag.id))}
    />
  );

  const SectionHeader: React.FC<{ icon: React.ReactNode; label: string; count: number }> = ({ icon, label, count }) => (
    <div className="flex items-center gap-1.5 px-1 py-1.5">
      {icon}
      <span className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider">
        {label} ({count})
      </span>
    </div>
  );

  return (
    <div className="flex-1 overflow-y-auto p-2 space-y-2" onClick={closeContextMenu}>
      {/* Starred */}
      {starredTags.length > 0 && (
        <div>
          <SectionHeader icon={<Star size={10} className="text-yellow-500 fill-yellow-500" />} label="Starred" count={starredTags.length} />
          <div className={gridClass}>{starredTags.map(renderTag)}</div>
        </div>
      )}

      {/* Frequently Used */}
      {frequentTags.length > 0 && (
        <div>
          <SectionHeader icon={<Flame size={10} className="text-orange-500" />} label="Frequently Used" count={frequentTags.length} />
          <div className={gridClass}>{frequentTags.map(renderTag)}</div>
        </div>
      )}

      {/* Grouped tags */}
      {grouped.map(([groupName, groupTags]) => (
        <div key={groupName}>
          <SectionHeader icon={<FolderOpen size={10} className="text-zinc-500" />} label={groupName} count={groupTags.length} />
          <div className={gridClass}>{groupTags.map(renderTag)}</div>
        </div>
      ))}

      {groupFiltered.length === 0 && (
        <p className="text-xs text-zinc-600 text-center py-6">No tags found</p>
      )}

      {/* Right-click context menu */}
      {contextMenu && (
        <div
          className="fixed z-[80] bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl py-1 w-32"
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          <button
            onClick={() => { onToggleStar(contextMenu.tagId); closeContextMenu(); }}
            className="w-full px-3 py-1.5 text-xs text-left text-zinc-300 hover:bg-zinc-800 flex items-center gap-2"
          >
            <Star size={10} className={starredSet.has(contextMenu.tagId) ? 'fill-yellow-500 text-yellow-500' : ''} />
            {starredSet.has(contextMenu.tagId) ? 'Unstar' : 'Star'}
          </button>
          {onRequestMerge && selectedUserTagCount >= 2 && (
            <button
              onClick={() => { onRequestMerge(); closeContextMenu(); }}
              className="w-full px-3 py-1.5 text-xs text-left text-zinc-300 hover:bg-zinc-800 flex items-center gap-2"
            >
              <span className="w-2.5 text-center">⛙</span>
              Merge {selectedUserTagCount} Tags
            </button>
          )}
        </div>
      )}
    </div>
  );
};
