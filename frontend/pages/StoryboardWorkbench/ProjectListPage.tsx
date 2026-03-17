import React, { useState, useEffect, useCallback } from 'react';
import { Plus, Search, Loader2, Layers } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useStoryboardStore } from '../../stores/storyboardStore';
import { useTeamContext } from '../../contexts/TeamContext';
import {
  fetchProjects,
  createProject,
  deleteProject as deleteProjectApi,
  updateProject,
} from '../../services/storyboardService';
import ProjectCard from '../../components/storyboard/project/ProjectCard';
import NewProjectDialog from '../../components/storyboard/project/NewProjectDialog';
import { ProjectSummary } from '../../types';

// ─── Types ────────────────────────────────────────────────────────────────────

type SortField = 'name' | 'created_at' | 'updated_at';

// ─── Helpers ──────────────────────────────────────────────────────────────────

function sortProjects(projects: ProjectSummary[], sortField: SortField): ProjectSummary[] {
  return [...projects].sort((a, b) => {
    if (sortField === 'name') {
      return a.name.localeCompare(b.name);
    }
    const aTime = new Date(sortField === 'created_at' ? a.created_at : a.updated_at).getTime();
    const bTime = new Date(sortField === 'created_at' ? b.created_at : b.updated_at).getTime();
    return bTime - aTime;
  });
}

// ─── Skeleton card ────────────────────────────────────────────────────────────

function SkeletonCard() {
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-xl overflow-hidden animate-pulse">
      <div className="h-36 bg-gray-700" />
      <div className="px-4 py-3 space-y-2">
        <div className="h-3 bg-gray-700 rounded w-3/4" />
        <div className="h-2 bg-gray-700 rounded w-1/2" />
        <div className="h-2 bg-gray-700 rounded w-1/3" />
      </div>
    </div>
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

export function ProjectListPage() {
  const { t } = useTranslation();
  const { setCurrentProject, setProjectList, projectList = [] } = useStoryboardStore();
  const { selectedTeamId } = useTeamContext();

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [sortField, setSortField] = useState<SortField>('updated_at');
  const [showNewDialog, setShowNewDialog] = useState(false);

  // ─── Load projects ─────────────────────────────────────────────────────────

  const loadProjects = useCallback(async () => {
    if (!selectedTeamId) return;
    setLoading(true);
    setError(null);
    try {
      const result = await fetchProjects(selectedTeamId);
      setProjectList(Array.isArray(result?.data) ? result.data : []);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [selectedTeamId, setProjectList]);

  useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  // ─── Derived state ─────────────────────────────────────────────────────────

  const filtered = (projectList ?? []).filter((p) =>
    p.name.toLowerCase().includes(search.toLowerCase())
  );
  const sorted = sortProjects(filtered, sortField);

  // ─── Handlers ──────────────────────────────────────────────────────────────

  const handleCardClick = useCallback(
    (id: string) => {
      setCurrentProject(id);
    },
    [setCurrentProject]
  );

  const handleDelete = useCallback(
    async (id: string) => {
      try {
        await deleteProjectApi(id);
        await loadProjects();
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
      }
    },
    [loadProjects]
  );

  const handleRename = useCallback(
    async (id: string) => {
      const project = projectList.find((p) => p.id === id);
      if (!project) return;
      const newName = window.prompt('Rename project:', project.name);
      if (!newName || newName.trim() === project.name) return;
      try {
        await updateProject(id, { name: newName.trim() });
        await loadProjects();
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
      }
    },
    [projectList, loadProjects]
  );

  const handleDuplicate = useCallback(
    async (id: string) => {
      const project = projectList.find((p) => p.id === id);
      if (!project || !selectedTeamId) return;
      try {
        await createProject({
          team_id: selectedTeamId,
          name: `${project.name} (Copy)`,
        });
        await loadProjects();
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
      }
    },
    [projectList, selectedTeamId, loadProjects]
  );

  const handleExport = useCallback((_id: string) => {
    // Export handled within CanvasEditorPage; no-op from list
  }, []);

  const handleCreate = useCallback(
    async (name: string) => {
      if (!selectedTeamId) return;
      try {
        const project = await createProject({ team_id: selectedTeamId, name });
        setShowNewDialog(false);
        await loadProjects();
        setCurrentProject(project.id);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
      }
    },
    [selectedTeamId, loadProjects, setCurrentProject]
  );

  // ─── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full bg-gray-950 min-h-0">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 flex-shrink-0">
        <h1 className="text-xl font-semibold text-gray-100">
          {t('storyboard.title')}
        </h1>
        <button
          type="button"
          onClick={() => setShowNewDialog(true)}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors"
        >
          <Plus size={16} />
          {t('storyboard.newProject')}
        </button>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3 px-6 py-3 border-b border-gray-800 flex-shrink-0">
        <div className="relative flex-1 max-w-xs">
          <Search
            size={14}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 pointer-events-none"
          />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('storyboard.search')}
            className="w-full pl-8 pr-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 transition-colors"
          />
        </div>

        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500">{t('storyboard.sortBy')}</span>
          <select
            value={sortField}
            onChange={(e) => setSortField(e.target.value as SortField)}
            className="px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-300 focus:outline-none focus:border-blue-500 transition-colors"
          >
            <option value="name">{t('storyboard.sort.name', 'Name')}</option>
            <option value="created_at">{t('storyboard.sort.created', 'Created')}</option>
            <option value="updated_at">{t('storyboard.sort.updated', 'Updated')}</option>
          </select>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="mx-6 mt-4 px-4 py-3 bg-red-950/50 border border-red-800 rounded-lg text-sm text-red-400 flex-shrink-0">
          {error}
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {loading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <SkeletonCard key={i} />
            ))}
          </div>
        ) : sorted.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 gap-4 text-center">
            <Layers size={48} className="text-gray-700" />
            <div>
              <p className="text-sm font-medium text-gray-400">
                {search ? 'No projects match your search' : t('storyboard.noProjects')}
              </p>
              {!search && (
                <p className="text-xs text-gray-600 mt-1">{t('storyboard.createFirst')}</p>
              )}
            </div>
            {!search && (
              <button
                type="button"
                onClick={() => setShowNewDialog(true)}
                className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors"
              >
                <Plus size={14} />
                {t('storyboard.newProject')}
              </button>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {sorted.map((project) => (
              <ProjectCard
                key={project.id}
                project={project}
                onClick={handleCardClick}
                onRename={handleRename}
                onDuplicate={handleDuplicate}
                onExport={handleExport}
                onDelete={handleDelete}
              />
            ))}
          </div>
        )}
      </div>

      {/* New Project Dialog */}
      {showNewDialog && (
        <NewProjectDialog
          onCreate={(name) => handleCreate(name)}
          onCancel={() => setShowNewDialog(false)}
        />
      )}
    </div>
  );
}
