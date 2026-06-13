import React, { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  LayoutGrid, Plus, Star, FolderOpen, ChevronDown, ChevronRight,
  Globe, Users, PanelLeftClose, PanelLeftOpen
} from 'lucide-react';
import { Project } from '../types';

// ── CollapsibleSection ──────────────────────────────────────
interface CollapsibleSectionProps {
  title: string;
  icon: React.ReactNode;
  count?: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

const CollapsibleSection: React.FC<CollapsibleSectionProps> = ({
  title, icon, count, defaultOpen = false, children
}) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-ink-400
                   hover:text-ink-200 hover:bg-ink-800/50 rounded-md transition-colors"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {icon}
        <span className="flex-1 text-left truncate">{title}</span>
        {count != null && (
          <span className="text-[10px] text-ink-600">({count})</span>
        )}
      </button>
      {open && <div className="ml-4 mt-0.5 space-y-0.5">{children}</div>}
    </div>
  );
};

// ── SidebarProjectItem ──────────────────────────────────────
const SidebarProjectItem: React.FC<{
  project: Project;
  active: boolean;
  onClick: () => void;
}> = ({ project, active, onClick }) => (
  <button
    onClick={onClick}
    className={`w-full flex items-center gap-2 px-2 py-1 text-xs rounded-md
                transition-colors truncate
                ${active
                  ? 'bg-indigo-500/20 text-indigo-300'
                  : 'text-ink-400 hover:text-ink-200 hover:bg-ink-800/50'}`}
  >
    <span className="truncate">{project.name}</span>
  </button>
);

// ── Main Sidebar ────────────────────────────────────────────
interface ProjectsSidebarProps {
  projects: Project[];
  starredProjects: Project[];
  selectedProjectId: string | null;
  onProjectSelect: (project: Project) => void;
  onCreateProject: () => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

export const ProjectsSidebar: React.FC<ProjectsSidebarProps> = ({
  projects, starredProjects, selectedProjectId,
  onProjectSelect, onCreateProject, collapsed, onToggleCollapse
}) => {
  const { t } = useTranslation();

  const internalProjects = projects.filter(p => p.project_type !== 'external');
  const externalProjects = projects.filter(p => p.project_type === 'external');

  const groupedProjects = useMemo(() => {
    const groups: Record<string, Project[]> = {};
    for (const p of projects) {
      const group = p.project_group?.trim();
      if (group) {
        if (!groups[group]) groups[group] = [];
        groups[group].push(p);
      }
    }
    return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b));
  }, [projects]);

  if (collapsed) {
    return (
      <div className="w-10 border-r border-ink-800 flex flex-col items-center py-3 shrink-0">
        <button onClick={onToggleCollapse} className="p-1.5 text-ink-500 hover:text-ink-300 transition-colors">
          <PanelLeftOpen size={16} />
        </button>
      </div>
    );
  }

  return (
    <aside className="w-52 border-r border-ink-800/80 flex flex-col h-full shrink-0">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3 border-b border-ink-800/50">
        <span className="flex items-center gap-2 text-sm font-medium text-ink-200">
          <LayoutGrid size={14} />
          {t('projects.sidebar.projectList', 'Projects')}
        </span>
        <div className="flex items-center gap-1">
          <button onClick={onCreateProject}
            className="p-1 text-ink-500 hover:text-ink-300 hover:bg-ink-800 rounded transition-colors">
            <Plus size={14} />
          </button>
          <button onClick={onToggleCollapse}
            className="p-1 text-ink-500 hover:text-ink-300 hover:bg-ink-800 rounded transition-colors">
            <PanelLeftClose size={14} />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto py-2 px-1 space-y-1">
        {/* Starred */}
        <CollapsibleSection
          title={t('projects.sidebar.starredProjects', 'Starred')}
          icon={<Star size={12} className="text-yellow-500" />}
          defaultOpen={starredProjects.length > 0}
        >
          {starredProjects.length === 0 ? (
            <p className="text-[10px] text-ink-600 px-2 py-1">
              {t('projects.sidebar.noStarred', 'No starred projects')}
            </p>
          ) : (
            starredProjects.map(p => (
              <SidebarProjectItem
                key={p.id} project={p}
                active={p.id === selectedProjectId}
                onClick={() => onProjectSelect(p)}
              />
            ))
          )}
        </CollapsibleSection>

        {/* Groups */}
        {groupedProjects.length > 0 && (
          <>
            {groupedProjects.map(([groupName, groupProjects]) => (
              <CollapsibleSection
                key={groupName}
                title={groupName}
                icon={<FolderOpen size={12} />}
                count={groupProjects.length}
              >
                {groupProjects.map(p => (
                  <SidebarProjectItem
                    key={p.id} project={p}
                    active={p.id === selectedProjectId}
                    onClick={() => onProjectSelect(p)}
                  />
                ))}
              </CollapsibleSection>
            ))}
          </>
        )}

        <div className="border-t border-ink-800/50 my-2" />

        {/* Internal */}
        <CollapsibleSection
          title={t('projects.sidebar.internalProjects', 'Internal Projects')}
          icon={<Users size={12} />}
          count={internalProjects.length}
          defaultOpen
        >
          {internalProjects.map(p => (
            <SidebarProjectItem
              key={p.id} project={p}
              active={p.id === selectedProjectId}
              onClick={() => onProjectSelect(p)}
            />
          ))}
        </CollapsibleSection>

        {/* External */}
        <CollapsibleSection
          title={t('projects.sidebar.externalProjects', 'External Projects')}
          icon={<Globe size={12} />}
          count={externalProjects.length}
        >
          {externalProjects.map(p => (
            <SidebarProjectItem
              key={p.id} project={p}
              active={p.id === selectedProjectId}
              onClick={() => onProjectSelect(p)}
            />
          ))}
        </CollapsibleSection>
      </div>
    </aside>
  );
};
