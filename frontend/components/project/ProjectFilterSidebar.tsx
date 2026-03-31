import {
  LayoutGrid, Plus, Star, Clock, Zap, Archive,
  FolderOpen, PanelLeftClose, PanelLeftOpen,
} from 'lucide-react';

interface ProjectFilterSidebarProps {
  activeFilter: string;
  onFilterChange: (filter: string) => void;
  folders: string[];
  projectCounts: {
    all: number;
    starred: number;
    recent: number;
    active: number;
    archived: number;
  };
  folderCounts: Record<string, number>;
  onCreateProject: () => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

const VIEW_FILTERS = [
  { key: 'all', label: 'All', icon: LayoutGrid },
  { key: 'starred', label: 'Starred', icon: Star },
  { key: 'recent', label: 'Recent', icon: Clock },
  { key: 'active', label: 'Active', icon: Zap },
  { key: 'archived', label: 'Archived', icon: Archive },
] as const;

const iconBtnClass = 'rounded p-1 text-zinc-500 transition-colors hover:text-zinc-300';

function FilterItem({
  icon: Icon, label, count, active, onClick,
}: {
  icon: React.ElementType;
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  const style = active
    ? 'bg-indigo-500/8 text-indigo-300'
    : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/40';

  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center gap-3 rounded-md px-3 py-2 text-[13px] transition-colors ${style}`}
    >
      <Icon size={16} className="shrink-0" />
      <span className="flex-1 truncate text-left">{label}</span>
      <span className="text-xs text-zinc-600">{count}</span>
    </button>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="px-2 text-[11px] font-medium uppercase tracking-wider text-zinc-600">
      {children}
    </span>
  );
}

export function ProjectFilterSidebar({
  activeFilter, onFilterChange, folders, projectCounts,
  folderCounts, onCreateProject, collapsed, onToggleCollapse,
}: ProjectFilterSidebarProps) {
  if (collapsed) {
    return (
      <div className="flex w-10 flex-col items-center border-r border-zinc-800/40 pt-16">
        <button onClick={onToggleCollapse} className={iconBtnClass} title="Expand sidebar">
          <PanelLeftOpen size={16} />
        </button>
      </div>
    );
  }

  return (
    <div className="flex w-52 flex-col border-r border-zinc-800/40 pt-16">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3">
        <span className="text-sm font-semibold text-zinc-200">Projects</span>
        <div className="flex items-center gap-1">
          <button onClick={onCreateProject} className={iconBtnClass} title="Create project">
            <Plus size={16} />
          </button>
          <button onClick={onToggleCollapse} className={iconBtnClass} title="Collapse sidebar">
            <PanelLeftClose size={16} />
          </button>
        </div>
      </div>

      {/* View filters */}
      <div className="px-2">
        <SectionLabel>View</SectionLabel>
        <div className="mt-1 flex flex-col gap-0.5">
          {VIEW_FILTERS.map(({ key, label, icon }) => (
            <FilterItem
              key={key}
              icon={icon}
              label={label}
              count={projectCounts[key]}
              active={activeFilter === key}
              onClick={() => onFilterChange(key)}
            />
          ))}
        </div>
      </div>

      {/* Folders */}
      {folders.length > 0 && (
        <>
          <div className="mx-3 my-2 border-t border-zinc-800/80" />
          <div className="flex-1 overflow-y-auto px-2 pb-2">
            <SectionLabel>Folders</SectionLabel>
            <div className="mt-1 flex flex-col gap-0.5">
              {folders.map((folder) => (
                <FilterItem
                  key={folder}
                  icon={FolderOpen}
                  label={folder}
                  count={folderCounts[folder] ?? 0}
                  active={activeFilter === folder}
                  onClick={() => onFilterChange(folder)}
                />
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
