import React, { useState, useEffect } from 'react';
import { Plus, Star, LayoutGrid, LayoutList, Loader2, FolderOpen } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Project } from '../types';
import { fetchProjects, updateProject } from '../services/projectsService';
import { ProjectCard } from './ProjectCard';

interface ProjectsListViewProps {
  onProjectSelect: (project: Project) => void;
  onCreateProject: () => void;
}

export const ProjectsListView: React.FC<ProjectsListViewProps> = ({ onProjectSelect, onCreateProject }) => {
  const { t } = useTranslation();
  const [projects, setProjects] = useState<Project[]>([]);
  const [filter, setFilter] = useState<'all' | 'starred'>('all');
  const [viewMode, setViewMode] = useState<'grid' | 'table'>('grid');
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    loadProjects();
  }, [filter]);

  const loadProjects = async () => {
    setIsLoading(true);
    try {
      const params = filter === 'starred' ? { starred: true } : undefined;
      const data = await fetchProjects(params);
      setProjects(data);
    } catch (err) {
      console.error('Failed to load projects:', err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleToggleStar = async (e: React.MouseEvent, project: Project) => {
    e.stopPropagation();
    try {
      await updateProject(project.id, { is_starred: !project.is_starred });
      setProjects(prev =>
        prev.map(p => p.id === project.id ? { ...p, is_starred: !p.is_starred } : p)
      );
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

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold text-white">{t('mediatrack.projects')}</h1>
        </div>
        <div className="flex items-center gap-3">
          {/* Filter tabs */}
          <div className="flex bg-zinc-800 rounded-xl p-1">
            <button
              onClick={() => setFilter('all')}
              className={`px-4 py-1.5 text-sm rounded-lg font-medium transition-colors ${
                filter === 'all'
                  ? 'bg-zinc-700 text-white'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              {t('mediatrack.allProjects')}
            </button>
            <button
              onClick={() => setFilter('starred')}
              className={`px-4 py-1.5 text-sm rounded-lg font-medium transition-colors flex items-center gap-1.5 ${
                filter === 'starred'
                  ? 'bg-zinc-700 text-white'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <Star size={14} />
              {t('mediatrack.starred')}
            </button>
          </div>

          {/* View toggle */}
          <div className="flex bg-zinc-800 rounded-xl p-1">
            <button
              onClick={() => setViewMode('grid')}
              className={`p-2 rounded-lg transition-colors ${
                viewMode === 'grid' ? 'bg-zinc-700 text-white' : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <LayoutGrid size={16} />
            </button>
            <button
              onClick={() => setViewMode('table')}
              className={`p-2 rounded-lg transition-colors ${
                viewMode === 'table' ? 'bg-zinc-700 text-white' : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <LayoutList size={16} />
            </button>
          </div>

          {/* New Project button */}
          <button
            onClick={onCreateProject}
            className="flex items-center gap-2 px-4 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl font-medium transition-colors text-sm"
          >
            <Plus size={16} />
            {t('mediatrack.newProject')}
          </button>
        </div>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="w-8 h-8 text-indigo-400 animate-spin" />
        </div>
      )}

      {/* Empty state */}
      {!isLoading && projects.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="p-4 bg-zinc-800 rounded-2xl mb-4">
            <FolderOpen size={40} className="text-zinc-500" />
          </div>
          <h3 className="text-lg font-medium text-zinc-300 mb-2">{t('mediatrack.noProjects')}</h3>
          <button
            onClick={onCreateProject}
            className="mt-4 flex items-center gap-2 px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl font-medium transition-colors text-sm"
          >
            <Plus size={16} />
            {t('mediatrack.createProject')}
          </button>
        </div>
      )}

      {/* Grid view */}
      {!isLoading && projects.length > 0 && viewMode === 'grid' && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {projects.map(project => (
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
      {!isLoading && projects.length > 0 && viewMode === 'table' && (
        <div className="bg-zinc-800/50 border border-zinc-700/50 rounded-xl overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-zinc-700/50">
                <th className="text-left px-4 py-3 text-xs font-medium text-zinc-400 uppercase tracking-wider">{t('mediatrack.projectName')}</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-zinc-400 uppercase tracking-wider">{t('mediatrack.projectType')}</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-zinc-400 uppercase tracking-wider">{t('mediatrack.projectGroup')}</th>
                <th className="text-center px-4 py-3 text-xs font-medium text-zinc-400 uppercase tracking-wider">{t('mediatrack.files')}</th>
                <th className="text-center px-4 py-3 text-xs font-medium text-zinc-400 uppercase tracking-wider">{t('mediatrack.starred')}</th>
                <th className="text-right px-4 py-3 text-xs font-medium text-zinc-400 uppercase tracking-wider">{t('mediatrack.updatedAt')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-700/30">
              {projects.map(project => (
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
                      {t(`mediatrack.${project.project_type}`)}
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
