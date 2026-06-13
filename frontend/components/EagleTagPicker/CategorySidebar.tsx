import React from 'react';
import { LayoutGrid, Circle, FolderOpen } from 'lucide-react';

interface GroupInfo {
  name: string;
  count: number;
}

interface CategorySidebarProps {
  totalCount: number;
  uncategorizedCount: number;
  groups: GroupInfo[];
  selectedGroup: string | null; // null = "All"
  onSelectGroup: (group: string | null) => void;
}

export const CategorySidebar: React.FC<CategorySidebarProps> = ({
  totalCount,
  uncategorizedCount,
  groups,
  selectedGroup,
  onSelectGroup,
}) => {
  const itemClass = (active: boolean) =>
    `flex items-center justify-between gap-2 px-2 py-1.5 rounded text-xs cursor-pointer transition-colors ${
      active
        ? 'bg-indigo-500/20 text-indigo-300'
        : 'text-ink-400 hover:bg-ink-800 hover:text-ink-200'
    }`;

  return (
    <div className="w-[120px] shrink-0 border-r border-ink-800 overflow-y-auto py-2 px-1.5">
      {/* All */}
      <button
        className={itemClass(selectedGroup === null)}
        onClick={() => onSelectGroup(null)}
      >
        <span className="flex items-center gap-1.5">
          <LayoutGrid size={12} />
          <span>All</span>
        </span>
        <span className="text-ink-600">{totalCount}</span>
      </button>

      {/* Uncategorized */}
      <button
        className={itemClass(selectedGroup === '__uncategorized__')}
        onClick={() => onSelectGroup('__uncategorized__')}
      >
        <span className="flex items-center gap-1.5">
          <Circle size={12} />
          <span>Uncategorized</span>
        </span>
        <span className="text-ink-600">{uncategorizedCount}</span>
      </button>

      {/* Groups header */}
      {groups.length > 0 && (
        <div className="mt-3 mb-1 px-2">
          <span className="text-[10px] font-semibold text-ink-600 uppercase tracking-wider">
            Groups ({groups.length})
          </span>
        </div>
      )}

      {/* Group items */}
      {groups.map((group) => (
        <button
          key={group.name}
          className={itemClass(selectedGroup === group.name)}
          onClick={() => onSelectGroup(group.name)}
        >
          <span className="flex items-center gap-1.5 min-w-0">
            <FolderOpen size={12} className="shrink-0" />
            <span className="truncate">{group.name}</span>
          </span>
          <span className="text-ink-600 shrink-0">{group.count}</span>
        </button>
      ))}
    </div>
  );
};
