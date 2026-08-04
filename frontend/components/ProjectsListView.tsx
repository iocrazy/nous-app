import React, { useState, useMemo, useCallback, useEffect } from 'react';
import {
  Plus, Star, LayoutGrid, LayoutList, FolderOpen,
  Search, ArrowUpDown, MoreVertical
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Project, ProjectSuggestionItem } from '../types';
import { updateProject, deleteProject, fetchProjectSuggestions } from '../services/projectsService';
import { useTeamContext } from '../contexts/TeamContext';
import { formatRelativeTime } from '../utils/relativeTime';
import { ProjectCard } from './ProjectCard';
import { ProjectContextMenu } from './ProjectContextMenu';
import { ProjectSettingsPanel } from './ProjectSettingsPanel';
import { ProjectMembersPanel } from './ProjectMembersPanel';
import { ProjectsQueueView } from './ProjectsQueueView';
import { UiSelect } from './ui';
import { PageHeader } from './layout/PageHeader';

interface ProjectsListViewProps {
  projects: Project[];
  onProjectSelect: (project: Project) => void;
  onCreateProject: () => void;
  onProjectsChange?: () => void;
  title?: string;
}

type SortKey = 'updated_at' | 'created_at' | 'name';
/** Homepage top-level view (PR-9, G7) — Queue (work-queue rows) is the default, Grid is secondary. */
type HomeView = 'queue' | 'grid';

const HOME_VIEW_STORAGE_KEY = 'mediahub.projects.view';

function readStoredHomeView(): HomeView {
  try {
    return localStorage.getItem(HOME_VIEW_STORAGE_KEY) === 'grid' ? 'grid' : 'queue';
  } catch {
    return 'queue';
  }
}

export const ProjectsListView: React.FC<ProjectsListViewProps> = ({
  projects, onProjectSelect, onCreateProject, onProjectsChange, title: externalTitle
}) => {
  const { t } = useTranslation();
  const { selectedTeamId, personalTeamId } = useTeamContext();
  const [homeView, setHomeView] = useState<HomeView>(readStoredHomeView);
  const [viewMode, setViewMode] = useState<'grid' | 'table'>('grid');
  const [searchQuery, setSearchQuery] = useState('');
  const [sortBy, setSortBy] = useState<SortKey>('updated_at');
  const [sortDir, setSortDir] = useState<'desc' | 'asc'>('desc');
  const [contextMenu, setContextMenu] = useState<{ project: Project; x: number; y: number } | null>(null);
  const [settingsProject, setSettingsProject] = useState<Project | null>(null);
  const [membersProject, setMembersProject] = useState<Project | null>(null);
  const [suggestions, setSuggestions] = useState<ProjectSuggestionItem[]>([]);

  // Fetched once here (mount + whenever a generate CTA — queue row or grid
  // card — asks for a refetch) and passed down to both the queue and grid
  // views, so a project only needs one round-trip through the suggestions
  // batch endpoint regardless of which view is active.
  const loadSuggestions = useCallback(async () => {
    try {
      const isPersonal = !selectedTeamId || selectedTeamId === personalTeamId;
      const data = await fetchProjectSuggestions(isPersonal ? 'personal' : selectedTeamId ?? undefined);
      setSuggestions(data);
    } catch (err) {
      console.error('Failed to load project suggestions:', err);
    }
  }, [selectedTeamId, personalTeamId]);

  useEffect(() => {
    loadSuggestions();
  }, [loadSuggestions]);

  const suggestionsById = useMemo(() => {
    const map = new Map<string, ProjectSuggestionItem>();
    for (const item of suggestions) map.set(String(item.project_id), item);
    return map;
  }, [suggestions]);

  const handleHomeViewChange = useCallback((next: HomeView) => {
    setHomeView(next);
    try {
      localStorage.setItem(HOME_VIEW_STORAGE_KEY, next);
    } catch {
      /* localStorage unavailable — in-memory only for this session */
    }
  }, []);

  const handleToggleStar = async (e: React.MouseEvent, project: Project) => {
    e.stopPropagation();
    try {
      await updateProject(project.id, { is_starred: !project.is_starred });
      onProjectsChange?.();
    } catch (err) {
      console.error('Failed to toggle star:', err);
    }
  };

  const handleToggleStarById = async (project: Project) => {
    try {
      await updateProject(project.id, { is_starred: !project.is_starred });
      onProjectsChange?.();
    } catch (err) {
      console.error('Failed to toggle star:', err);
    }
  };

  const handleToggleArchive = async (project: Project) => {
    try {
      await updateProject(project.id, { archived: !project.archived_at });
      onProjectsChange?.();
    } catch (err) {
      console.error('Failed to toggle archive:', err);
    }
  };

  const handleDeleteProject = async (project: Project) => {
    if (!window.confirm(t('projects.confirmDelete', `Delete "${project.name}"? This cannot be undone.`))) return;
    try {
      await deleteProject(project.id);
      onProjectsChange?.();
    } catch (err) {
      console.error('Failed to delete project:', err);
    }
  };

  const handleContextMenu = (e: React.MouseEvent, project: Project) => {
    e.stopPropagation();
    setContextMenu({ project, x: e.clientX, y: e.clientY });
  };

  const handleColorLabel = async (project: Project, color: string | null) => {
    try {
      await updateProject(project.id, { color_label: color });
      onProjectsChange?.();
    } catch (err) {
      console.error('Failed to set color label:', err);
    }
  };

  const typeColors: Record<string, string> = {
    internal: 'text-blue-400 bg-blue-500/20',
    external: 'text-orange-400 bg-orange-500/20',
    personal: 'text-purple-400 bg-purple-500/20',
  };

  const colorLabelDots: Record<string, string> = {
    red: 'bg-red-500',
    orange: 'bg-orange-500',
    yellow: 'bg-yellow-500',
    green: 'bg-green-500',
    blue: 'bg-blue-500',
    purple: 'bg-purple-500',
    pink: 'bg-pink-500',
  };

  const filteredProjects = useMemo(() => {
    let items = projects;
    // Search
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase();
      items = items.filter(p => p.name.toLowerCase().includes(q));
    }
    // Sort
    items = [...items].sort((a, b) => {
      let cmp = 0;
      if (sortBy === 'name') cmp = a.name.localeCompare(b.name);
      else if (sortBy === 'updated_at') cmp = new Date(a.updated_at).getTime() - new Date(b.updated_at).getTime();
      else cmp = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
      return sortDir === 'desc' ? -cmp : cmp;
    });
    return items;
  }, [projects, searchQuery, sortBy, sortDir]);

  return (
    <div>
      {/* Header */}
      <PageHeader
        level="content"
        title={externalTitle || t('mediatrack.projects', 'Projects')}
        actions={
          <>
          {/* Search */}
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-500" />
            <input
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder={t('projects.searchProjects', 'Search projects')}
              className="w-44 pl-8 pr-3 py-1.5 text-xs bg-ink-800/60 border border-ink-700/50
                         rounded-lg text-ink-200 placeholder-ink-500 focus:outline-none
                         focus:border-indigo-500 transition-colors"
            />
          </div>

          {/* Home view toggle: Queue (default work queue) vs Grid (cards/table) */}
          <div className="flex bg-ink-800 rounded-lg p-0.5" role="group">
            <button
              onClick={() => handleHomeViewChange('queue')}
              aria-label={t('projects.view.queue', 'Queue view')}
              title={t('projects.view.queue', 'Queue view')}
              data-testid="home-view-queue-btn"
              className={`px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${
                homeView === 'queue' ? 'bg-indigo-600 text-white' : 'text-ink-400 hover:text-ink-200'
              }`}
            >
              ☰ {t('projects.view.queue', 'Queue')}
            </button>
            <button
              onClick={() => handleHomeViewChange('grid')}
              aria-label={t('projects.view.grid', 'Grid view')}
              title={t('projects.view.grid', 'Grid view')}
              data-testid="home-view-grid-btn"
              className={`px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${
                homeView === 'grid' ? 'bg-indigo-600 text-white' : 'text-ink-400 hover:text-ink-200'
              }`}
            >
              ▦ {t('projects.view.grid', 'Grid')}
            </button>
          </div>

          {/* New Project button */}
          <button
            onClick={onCreateProject}
            data-testid="new-project-btn"
            className="flex items-center gap-2 px-4 py-1.5 btn-tint-indigo rounded-lg font-medium transition-colors text-sm"
          >
            <Plus size={14} />
            {t('mediatrack.newProject', 'New Project')}
          </button>
          </>
        }
      />

      {/* Stats + sort + (grid-only) card/table view toggle */}
      <div className="flex items-center justify-between mb-4">
        <span className="text-xs text-ink-500">
          {t('projects.totalProjects', '{{count}} projects', { count: filteredProjects.length })}
        </span>
        {homeView === 'grid' && (
          <div className="flex items-center gap-2">
            {/* Sort */}
            <UiSelect value={sortBy} onChange={e => setSortBy(e.target.value as SortKey)}
              className="h-8 text-xs">
              <option value="updated_at">{t('projects.sortUpdatedAt', 'Last active')}</option>
              <option value="created_at">{t('projects.sortCreatedAt', 'Created')}</option>
              <option value="name">{t('projects.sortName', 'Name')}</option>
            </UiSelect>
            <button
              onClick={() => setSortDir(d => d === 'desc' ? 'asc' : 'desc')}
              className="p-1.5 text-ink-400 hover:text-ink-200 hover:bg-ink-800 rounded-lg transition-colors"
              title={sortDir === 'desc' ? t('projects.sort.descending') : t('projects.sort.ascending')}
            >
              <ArrowUpDown size={14} />
            </button>

            {/* View toggle */}
            <div className="flex bg-ink-800 rounded-lg p-0.5">
              <button
                onClick={() => setViewMode('grid')}
                className={`p-1.5 rounded-md transition-colors ${
                  viewMode === 'grid' ? 'bg-indigo-600 text-white' : 'text-ink-400 hover:text-ink-200'
                }`}
              >
                <LayoutGrid size={14} />
              </button>
              <button
                onClick={() => setViewMode('table')}
                className={`p-1.5 rounded-md transition-colors ${
                  viewMode === 'table' ? 'bg-indigo-600 text-white' : 'text-ink-400 hover:text-ink-200'
                }`}
              >
                <LayoutList size={14} />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Queue view (default) */}
      {homeView === 'queue' && (
        <ProjectsQueueView
          projects={filteredProjects}
          suggestions={suggestions}
          onProjectSelect={onProjectSelect}
          onRefetchSuggestions={loadSuggestions}
        />
      )}

      {/* Empty state (grid view only — queue view has its own empty state) */}
      {homeView === 'grid' && filteredProjects.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="p-4 bg-ink-800 rounded-2xl mb-4">
            <FolderOpen size={40} className="text-ink-500" />
          </div>
          <h3 className="text-lg font-medium text-ink-300 mb-2">{t('mediatrack.noProjects', 'No projects')}</h3>
          {searchQuery ? (
            <p className="text-sm text-ink-500">
              {t('projects.noSearchResults', 'No projects match your search')}
            </p>
          ) : (
            <button
              onClick={onCreateProject}
              className="mt-4 flex items-center gap-2 px-5 py-2.5 btn-tint-indigo rounded-xl font-medium transition-colors text-sm"
            >
              <Plus size={16} />
              {t('mediatrack.createProject', 'Create Project')}
            </button>
          )}
        </div>
      )}

      {/* Grid view — cards */}
      {homeView === 'grid' && filteredProjects.length > 0 && viewMode === 'grid' && (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4">
          {filteredProjects.map(project => (
            <div key={project.id} data-testid="project-card">
              <ProjectCard
                project={project}
                onClick={() => onProjectSelect(project)}
                onToggleStar={(e) => handleToggleStar(e, project)}
                onContextMenu={(e) => handleContextMenu(e, project)}
                suggestion={suggestionsById.get(String(project.id))}
                onSuggestionRefetch={loadSuggestions}
              />
            </div>
          ))}
        </div>
      )}

      {/* Grid view — table */}
      {homeView === 'grid' && filteredProjects.length > 0 && viewMode === 'table' && (
        <div className="bg-ink-800/50 border border-ink-700/50 rounded-xl overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-ink-700/50">
                <th className="text-left px-4 py-3 text-xs font-medium text-ink-400 uppercase tracking-wider">{t('mediatrack.projectName', 'Project Name')}</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-ink-400 uppercase tracking-wider">{t('mediatrack.projectType', 'Type')}</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-ink-400 uppercase tracking-wider">{t('mediatrack.projectGroup', 'Group')}</th>
                <th className="text-center px-4 py-3 text-xs font-medium text-ink-400 uppercase tracking-wider">{t('mediatrack.files', 'Files')}</th>
                <th className="text-center px-4 py-3 text-xs font-medium text-ink-400 uppercase tracking-wider">{t('mediatrack.starred', 'Starred')}</th>
                <th className="text-right px-4 py-3 text-xs font-medium text-ink-400 uppercase tracking-wider">{t('mediatrack.updatedAt', 'Updated')}</th>
                <th className="w-10"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-700/30">
              {filteredProjects.map(project => (
                <tr
                  key={project.id}
                  onClick={() => onProjectSelect(project)}
                  className="hover:bg-ink-700/30 cursor-pointer transition-colors"
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      {project.color_label && colorLabelDots[project.color_label] && (
                        <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${colorLabelDots[project.color_label]}`} />
                      )}
                      <span className="text-sm text-ink-50 font-medium">{project.name}</span>
                      {project.display_code && (
                        <span className="text-[11px] font-mono text-ink-500">{project.display_code}</span>
                      )}
                      {project.workflow_badge?.current_node_name && (
                        <span
                          data-testid="project-workflow-stage-chip"
                          className="text-[11px] px-2 py-0.5 rounded-full font-medium bg-[var(--accent-soft)] text-[var(--accent-text)] truncate max-w-[10rem]"
                        >
                          {project.workflow_badge.current_node_name}
                        </span>
                      )}
                      {(project.workflow_badge?.agents_active ?? 0) > 0 && (
                        <span
                          data-testid="project-agents-active-chip"
                          className="flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full font-medium bg-amber-500/15 text-amber-400"
                        >
                          <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" aria-hidden />
                          {t('projects.workflow.agentsActive', { count: project.workflow_badge!.agents_active })}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${typeColors[project.project_type] || ''}`}>
                      {t(`mediatrack.${project.project_type}`, project.project_type)}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-sm text-ink-400">{project.project_group || '—'}</span>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <span className="text-sm text-ink-300">{project.file_count}</span>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <button
                      onClick={(e) => handleToggleStar(e, project)}
                      className="p-1 rounded hover:bg-ink-600 transition-colors inline-flex"
                    >
                      <Star
                        size={14}
                        className={project.is_starred ? 'text-yellow-400 fill-yellow-400' : 'text-ink-500'}
                      />
                    </button>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="text-sm text-ink-500">{formatRelativeTime(project.updated_at, t)}</span>
                  </td>
                  <td className="px-2 py-3 text-center">
                    <button
                      onClick={(e) => handleContextMenu(e, project)}
                      className="p-1 rounded hover:bg-ink-600 text-ink-500 hover:text-ink-200 transition-colors inline-flex"
                    >
                      <MoreVertical size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {/* Context Menu */}
      {contextMenu && (
        <ProjectContextMenu
          project={contextMenu.project}
          position={{ x: contextMenu.x, y: contextMenu.y }}
          onClose={() => setContextMenu(null)}
          onSettings={() => setSettingsProject(contextMenu.project)}
          onMembers={() => setMembersProject(contextMenu.project)}
          onColorLabel={(color) => handleColorLabel(contextMenu.project, color)}
          onToggleStar={() => handleToggleStarById(contextMenu.project)}
          onArchive={() => handleToggleArchive(contextMenu.project)}
          onDelete={() => handleDeleteProject(contextMenu.project)}
        />
      )}
      {/* Settings Panel */}
      {settingsProject && (
        <ProjectSettingsPanel
          project={settingsProject}
          isOpen={!!settingsProject}
          onClose={() => setSettingsProject(null)}
          onUpdated={() => {
            setSettingsProject(null);
            onProjectsChange?.();
          }}
          onDeleted={() => {
            setSettingsProject(null);
            onProjectsChange?.();
          }}
        />
      )}
      {/* Members Panel */}
      {membersProject && (
        <ProjectMembersPanel
          project={membersProject}
          isOpen={!!membersProject}
          onClose={() => setMembersProject(null)}
        />
      )}
    </div>
  );
};
