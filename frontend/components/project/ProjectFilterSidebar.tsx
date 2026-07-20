import {
  LayoutGrid, Star, Archive, Clock,
  FolderOpen, PanelLeftClose, PanelLeftOpen, GitBranch,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface ProjectFilterSidebarProps {
  activeFilter: string;
  onFilterChange: (filter: string) => void;
  folders: string[];
  projectCounts: {
    recent: number;
    all: number;
    starred: number;
    archived: number;
  };
  folderCounts: Record<string, number>;
  onCreateProject: () => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
  /** Hide the Workflow Templates entry for viewers (spec §1). Defaults to true. */
  canManageTemplates?: boolean;
}

/** The reserved filter key that swaps the main pane to the template editor. */
export const WORKFLOW_TEMPLATES_FILTER = 'workflow-templates';

// Recent leads the Views rail — a cross-project feed of recently-edited
// scripts / canvases (not a project filter, so the main pane swaps to a
// Recent list). All / Starred / Archived remain project-card filters.
const VIEW_FILTERS = [
  { key: 'recent', labelKey: 'projects.view.recent', icon: Clock },
  { key: 'all', labelKey: 'projects.view.all', icon: LayoutGrid },
  { key: 'starred', labelKey: 'projects.view.starred', icon: Star },
  { key: 'archived', labelKey: 'projects.view.archived', icon: Archive },
] as const;

const iconBtnClass = 'rounded p-1 text-ink-400 transition-colors hover:text-ink-200';

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
    ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
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
  canManageTemplates = true,
}: ProjectFilterSidebarProps) {
  const { t } = useTranslation();

  if (collapsed) {
    // Slim full-height rail (no mid-air pill) — one click restores the panel.
    return (
      <div className="flex w-6 flex-shrink-0 flex-col border-r border-ink-800/40 transition-colors hover:bg-ink-800/20">
        <button
          onClick={onToggleCollapse}
          aria-label={t('projects.nav.expandSidebar')}
          title={t('projects.nav.expandSidebar')}
          className={`mt-3 flex w-full items-center justify-center ${iconBtnClass}`}
        >
          <PanelLeftOpen size={16} />
        </button>
      </div>
    );
  }

  return (
    <div className={`flex w-52 flex-col border-r border-ink-800/40`}>
      {/* Header — title + inline collapse control */}
      <div className="flex items-center justify-between px-4 pt-4 pb-3">
        <span className="text-sm font-semibold text-ink-200">{t('mediatrack.projects')}</span>
        <button
          onClick={onToggleCollapse}
          aria-label={t('projects.nav.collapseSidebar')}
          title={t('projects.nav.collapseSidebar')}
          className={iconBtnClass}
        >
          <PanelLeftClose size={16} />
        </button>
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

      {/* Global entry — workflow template editor (spec §1). Pinned to the
          bottom with a divider; hidden for viewers. */}
      {canManageTemplates && (
        <div className="mt-auto px-2 pb-3 pt-2">
          <div className="mx-1 mb-2 border-t border-ink-800/80" />
          <button
            data-testid="workflow-templates-entry"
            onClick={() => onFilterChange(WORKFLOW_TEMPLATES_FILTER)}
            className={`flex w-full items-center gap-3 rounded-md px-3 py-2 text-[13px] transition-colors ${
              activeFilter === WORKFLOW_TEMPLATES_FILTER
                ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                : 'text-ink-500 hover:text-ink-300 hover:bg-ink-800/40'
            }`}
          >
            <GitBranch size={16} className="shrink-0" />
            <span className="flex-1 truncate text-left">
              {t('projects.workflow.templatesEntry')}
            </span>
          </button>
        </div>
      )}
    </div>
  );
}
