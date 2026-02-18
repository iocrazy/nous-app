import React, { useState, useMemo } from 'react';
import {
  Plus, Star, LayoutGrid, LayoutList, FolderOpen,
  Search, ArrowUpDown, MoreVertical
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Project } from '../types';
import { updateProject, deleteProject } from '../services/projectsService';
import { ProjectCard } from './ProjectCard';

interface ProjectsListViewProps {
  projects: Project[];
  onProjectSelect: (project: Project) => void;
  onCreateProject: () => void;
  onProjectsChange?: () => void;
}

type FilterTab = 'all' | 'internal' | 'external';
type SortKey = 'updated_at' | 'created_at' | 'name';

export const ProjectsListView: React.FC<ProjectsListViewProps> = ({
  projects, onProjectSelect, onCreateProject, onProjectsChange
}) => {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<FilterTab>('all');
  const [viewMode, setViewMode] = useState<'grid' | 'table'>('grid');
  const [searchQuery, setSearchQuery] = useState('');
  const [sortBy, setSortBy] = useState<SortKey>('updated_at');
  const [sortDir, setSortDir] = useState<'desc' | 'asc'>('desc');

  const handleToggleStar = async (e: React.MouseEvent, project: Project) => {
    e.stopPropagation();
    try {
      await updateProject(project.id, { is_starred: !project.is_starred });
      onProjectsChange?.();
    } catch (err) {
      console.error('Failed to toggle star:', err);
    }
  };

  const formatRelativeTime = (dateStr: string): string => {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMinutes = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMinutes / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffMinutes < 1) return 'just now';
    if (diffMinutes < 60) return `${diffMinutes}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString();
  };

  const typeColors: Record<string, string> = {
    internal: 'text-blue-400 bg-blue-500/20',
    external: 'text-orange-400 bg-orange-500/20',
    personal: 'text-purple-400 bg-purple-500/20',
  };

  const filteredProjects = useMemo(() => {
    let items = projects;
    // Tab filter
    if (filter === 'internal') items = items.filter(p => p.project_type !== 'external');
    if (filter === 'external') items = items.filter(p => p.project_type === 'external');
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
  }, [projects, filter, searchQuery, sortBy, sortDir]);

  const tabs: { key: FilterTab; label: string }[] = [
    { key: 'all', label: t('projects.tab.all', 'All Projects') },
    { key: 'internal', label: t('projects.tab.internal', 'Internal') },
    { key: 'external', label: t('projects.tab.external', 'External') },
  ];

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-bold text-white">{t('mediatrack.projects', 'Projects')}</h1>
        <div className="flex items-center gap-2">
          {/* Search */}
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500" />
            <input
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder={t('projects.searchProjects', 'Search projects')}
              className="w-44 pl-8 pr-3 py-1.5 text-xs bg-zinc-800/60 border border-zinc-700/50
                         rounded-lg text-zinc-200 placeholder-zinc-500 focus:outline-none
                         focus:border-indigo-500 transition-colors"
            />
          </div>

          {/* New Project button */}
          <button
            onClick={onCreateProject}
            className="flex items-center gap-2 px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-medium transition-colors text-sm"
          >
            <Plus size={14} />
            {t('mediatrack.newProject', 'New Project')}
          </button>
        </div>
      </div>

      {/* Tab bar (underline style) */}
      <div className="flex gap-6 border-b border-zinc-800 mb-4">
        {tabs.map(tab => (
          <button key={tab.key} onClick={() => setFilter(tab.key)}
            className={`pb-2 text-sm font-medium border-b-2 transition-colors ${
              filter === tab.key
                ? 'border-indigo-500 text-white'
                : 'border-transparent text-zinc-500 hover:text-zinc-300'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Stats + sort + view toggle */}
      <div className="flex items-center justify-between mb-4">
        <span className="text-xs text-zinc-500">
          {t('projects.totalProjects', '{{count}} projects', { count: filteredProjects.length })}
        </span>
        <div className="flex items-center gap-2">
          {/* Sort */}
          <select value={sortBy} onChange={e => setSortBy(e.target.value as SortKey)}
            className="text-xs bg-zinc-800 border border-zinc-700/50 rounded-lg px-2 py-1.5 text-zinc-300
                       focus:outline-none focus:border-indigo-500 cursor-pointer">
            <option value="updated_at">{t('projects.sortUpdatedAt', 'Last active')}</option>
            <option value="created_at">{t('projects.sortCreatedAt', 'Created')}</option>
            <option value="name">{t('projects.sortName', 'Name')}</option>
          </select>
          <button
            onClick={() => setSortDir(d => d === 'desc' ? 'asc' : 'desc')}
            className="p-1.5 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 rounded-lg transition-colors"
            title={sortDir === 'desc' ? 'Descending' : 'Ascending'}
          >
            <ArrowUpDown size={14} />
          </button>

          {/* View toggle */}
          <div className="flex bg-zinc-800 rounded-lg p-0.5">
            <button
              onClick={() => setViewMode('grid')}
              className={`p-1.5 rounded-md transition-colors ${
                viewMode === 'grid' ? 'bg-zinc-700 text-white' : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <LayoutGrid size={14} />
            </button>
            <button
              onClick={() => setViewMode('table')}
              className={`p-1.5 rounded-md transition-colors ${
                viewMode === 'table' ? 'bg-zinc-700 text-white' : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <LayoutList size={14} />
            </button>
          </div>
        </div>
      </div>

      {/* Empty state */}
      {filteredProjects.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="p-4 bg-zinc-800 rounded-2xl mb-4">
            <FolderOpen size={40} className="text-zinc-500" />
          </div>
          <h3 className="text-lg font-medium text-zinc-300 mb-2">{t('mediatrack.noProjects', 'No projects')}</h3>
          {searchQuery ? (
            <p className="text-sm text-zinc-500">
              {t('projects.noSearchResults', 'No projects match your search')}
            </p>
          ) : (
            <button
              onClick={onCreateProject}
              className="mt-4 flex items-center gap-2 px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl font-medium transition-colors text-sm"
            >
              <Plus size={16} />
              {t('mediatrack.createProject', 'Create Project')}
            </button>
          )}
        </div>
      )}

      {/* Grid view */}
      {filteredProjects.length > 0 && viewMode === 'grid' && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {filteredProjects.map(project => (
            <ProjectCard
              key={project.id}
              project={project}
              onClick={() => onProjectSelect(project)}
              onToggleStar={(e) => handleToggleStar(e, project)}
            />
          ))}
        </div>
      )}

      {/* Table view */}
      {filteredProjects.length > 0 && viewMode === 'table' && (
        <div className="bg-zinc-800/50 border border-zinc-700/50 rounded-xl overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-zinc-700/50">
                <th className="text-left px-4 py-3 text-xs font-medium text-zinc-400 uppercase tracking-wider">{t('mediatrack.projectName', 'Project Name')}</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-zinc-400 uppercase tracking-wider">{t('mediatrack.projectType', 'Type')}</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-zinc-400 uppercase tracking-wider">{t('mediatrack.projectGroup', 'Group')}</th>
                <th className="text-center px-4 py-3 text-xs font-medium text-zinc-400 uppercase tracking-wider">{t('mediatrack.files', 'Files')}</th>
                <th className="text-center px-4 py-3 text-xs font-medium text-zinc-400 uppercase tracking-wider">{t('mediatrack.starred', 'Starred')}</th>
                <th className="text-right px-4 py-3 text-xs font-medium text-zinc-400 uppercase tracking-wider">{t('mediatrack.updatedAt', 'Updated')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-700/30">
              {filteredProjects.map(project => (
                <tr
                  key={project.id}
                  onClick={() => onProjectSelect(project)}
                  className="hover:bg-zinc-700/30 cursor-pointer transition-colors"
                >
                  <td className="px-4 py-3">
                    <span className="text-sm text-white font-medium">{project.name}</span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${typeColors[project.project_type] || ''}`}>
                      {t(`mediatrack.${project.project_type}`, project.project_type)}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-sm text-zinc-400">{project.project_group || '—'}</span>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <span className="text-sm text-zinc-300">{project.file_count}</span>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <button
                      onClick={(e) => handleToggleStar(e, project)}
                      className="p-1 rounded hover:bg-zinc-600 transition-colors inline-flex"
                    >
                      <Star
                        size={14}
                        className={project.is_starred ? 'text-yellow-400 fill-yellow-400' : 'text-zinc-500'}
                      />
                    </button>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="text-sm text-zinc-500">{formatRelativeTime(project.updated_at)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
