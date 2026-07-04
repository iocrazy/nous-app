import {
  LayoutGrid, Star, Clock, Zap, Archive,
  FolderOpen, ChevronLeft, ChevronRight,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

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
  { key: 'all', labelKey: 'projects.view.all', icon: LayoutGrid },
  { key: 'starred', labelKey: 'projects.view.starred', icon: Star },
  { key: 'recent', labelKey: 'projects.view.recent', icon: Clock },
  { key: 'active', labelKey: 'projects.view.active', icon: Zap },
  { key: 'archived', labelKey: 'projects.view.archived', icon: Archive },
] as const;

const iconBtnClass = 'rounded p-1 text-ink-500 transition-colors hover:text-ink-300';

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
    : 'text-ink-500 hover:text-ink-300 hover:bg-ink-800/40';

  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center gap-3 rounded-md px-3 py-2 text-[13px] transition-colors ${style}`}
    >
      <Icon size={16} className="shrink-0" />
      <span className="flex-1 truncate text-left">{label}</span>
      <span className="text-xs text-ink-600">{count}</span>
    </button>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="px-2 text-[11px] font-medium uppercase tracking-wider text-ink-600">
      {children}
    </span>
  );
}

export function ProjectFilterSidebar({
  activeFilter, onFilterChange, folders, projectCounts,
  folderCounts, onCreateProject, collapsed, onToggleCollapse,
}: ProjectFilterSidebarProps) {
  const { t } = useTranslation();

  if (collapsed) {
    return (
      <div className="relative w-4 flex-shrink-0">
        <button
          onClick={onToggleCollapse}
          className="absolute top-1/2 -translate-y-1/2 left-0 z-10 w-4 h-10 flex items-center justify-center rounded-r-md bg-ink-800/80 text-ink-500 hover:text-ink-200 hover:bg-ink-700 transition-colors"
          title={t('projects.nav.expandSidebar')}
        >
          <ChevronRight size={12} />
        </button>
      </div>
    );
  }

  return (
    <div className={`group relative flex w-52 flex-col border-r border-ink-800/40`}>
      {/* Header */}
      <div className="px-4 pt-4 pb-3">
        <span className="text-sm font-semibold text-ink-200">{t('mediatrack.projects')}</span>
      </div>

      {/* View filters */}
      <div className="px-2">
        <SectionLabel>{t('projects.view.title')}</SectionLabel>
        <div className="mt-1 flex flex-col gap-0.5">
          {VIEW_FILTERS.map(({ key, labelKey, icon }) => (
            <FilterItem
              key={key}
              icon={icon}
              label={t(labelKey)}
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
          <div className="mx-3 my-2 border-t border-ink-800/80" />
          <div className="px-2 pb-2 space-y-0.5">
            <SectionLabel>{t('projects.view.folders')}</SectionLabel>
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

      {/* Collapse toggle — right edge, mid-height */}
      <button
        onClick={onToggleCollapse}
        className="absolute top-1/2 -translate-y-1/2 right-0 z-10 w-4 h-10 flex items-center justify-center rounded-l-md bg-ink-800/80 text-ink-500 hover:text-ink-200 hover:bg-ink-700 transition-colors opacity-0 group-hover:opacity-100"
        title={t('projects.nav.collapseSidebar')}
      >
        <ChevronLeft size={12} />
      </button>
    </div>
  );
}
