import React, { useState, useEffect, useCallback } from 'react';
import {
  Plus,
  Search,
  Layers,
  ChevronDown,
  MousePointerClick,
  Palette,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
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
type SortOrder = 'asc' | 'desc';

// ─── Helpers ──────────────────────────────────────────────────────────────────

function sortProjects(
  projects: ProjectSummary[],
  sortField: SortField,
  sortOrder: SortOrder,
): ProjectSummary[] {
  return [...projects].sort((a, b) => {
    let cmp: number;
    if (sortField === 'name') {
      cmp = a.name.localeCompare(b.name);
    } else {
      const aTime = new Date(
        sortField === 'created_at' ? a.created_at : a.updated_at,
      ).getTime();
      const bTime = new Date(
        sortField === 'created_at' ? b.created_at : b.updated_at,
      ).getTime();
      cmp = bTime - aTime;
    }
    return sortOrder === 'desc' ? cmp : -cmp;
  });
}

const SORT_FIELD_LABELS: Record<SortField, string> = {
  name: 'Name',
  created_at: 'Creation date',
  updated_at: 'Modified date',
};

// ─── Skeleton row ─────────────────────────────────────────────────────────────

function SkeletonRow() {
  return (
    <div className="flex items-center gap-4 rounded-xl border border-zinc-800 bg-zinc-900 px-4 py-3.5 animate-pulse">
      <div className="h-10 w-10 rounded-lg bg-zinc-800" />
      <div className="flex-1 space-y-2">
        <div className="h-3.5 w-48 rounded bg-zinc-800" />
        <div className="h-2.5 w-32 rounded bg-zinc-800" />
      </div>
    </div>
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

export function ProjectListPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { teamId } = useParams<{ teamId: string }>();
  const { setCurrentProject, setProjectList, projectList = [] } = useStoryboardStore();
  const { selectedTeamId } = useTeamContext();

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [sortField, setSortField] = useState<SortField>('created_at');
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc');
  const [showNewDialog, setShowNewDialog] = useState(false);
  const [selectMode, setSelectMode] = useState(false);

  // ─── Load projects ─────────────────────────────────────────────────────────

  const loadProjects = useCallback(async () => {
    if (!selectedTeamId) return;
    setLoading(true);
    setError(null);
    try {
      const result = await fetchProjects(selectedTeamId);
      setProjectList(result.data ?? []);
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
    p.name.toLowerCase().includes(search.toLowerCase()),
  );
  const sorted = sortProjects(filtered, sortField, sortOrder);

  // ─── Handlers ──────────────────────────────────────────────────────────────

  const handleCardClick = useCallback(
    (id: string) => {
      navigate(`/team/${teamId}/storyboard/${id}`);
    },
    [navigate, teamId],
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
    [loadProjects],
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
    [projectList, loadProjects],
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
    [projectList, selectedTeamId, loadProjects],
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
        navigate(`/team/${teamId}/storyboard/${project.id}`);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
      }
    },
    [selectedTeamId, loadProjects, setCurrentProject],
  );

  // ─── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full bg-zinc-950 min-h-0">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-zinc-800 flex-shrink-0">
        <div className="flex items-center gap-4">
          <h1 className="text-xl font-bold text-zinc-100">Projects</h1>

          {/* Sort field pill */}
          <div className="relative">
            <select
              value={sortField}
              onChange={(e) => setSortField(e.target.value as SortField)}
              className="appearance-none cursor-pointer rounded-full bg-zinc-800 border border-zinc-700 pl-3 pr-8 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-indigo-500 transition-colors"
            >
              <option value="created_at">Creation date</option>
              <option value="updated_at">Modified date</option>
              <option value="name">Name</option>
            </select>
            <ChevronDown
              size={12}
              className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400"
            />
          </div>

          {/* Sort order pill */}
          <div className="relative">
            <select
              value={sortOrder}
              onChange={(e) => setSortOrder(e.target.value as SortOrder)}
              className="appearance-none cursor-pointer rounded-full bg-zinc-800 border border-zinc-700 pl-3 pr-8 py-1.5 text-xs text-zinc-200 focus:outline-none focus:border-indigo-500 transition-colors"
            >
              <option value="desc">Descending</option>
              <option value="asc">Ascending</option>
            </select>
            <ChevronDown
              size={12}
              className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400"
            />
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Select Mode */}
          <button
            type="button"
            onClick={() => setSelectMode((prev) => !prev)}
            className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs transition-colors ${
              selectMode
                ? 'border-indigo-500 bg-indigo-600/20 text-indigo-300'
                : 'border-zinc-700 bg-zinc-800 text-zinc-200 hover:bg-zinc-700'
            }`}
          >
            <MousePointerClick size={14} />
            Select Mode
          </button>

          {/* Style Template */}
          <button
            type="button"
            className="flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs text-zinc-200 transition-colors hover:bg-zinc-700"
          >
            <Palette size={14} />
            Style Template
          </button>

          {/* New Project */}
          <button
            type="button"
            onClick={() => setShowNewDialog(true)}
            className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500"
          >
            <Plus size={16} />
            New Project
          </button>
        </div>
      </div>

      {/* Search bar */}
      <div className="px-6 py-3 border-b border-zinc-800 flex-shrink-0">
        <div className="relative max-w-xs">
          <Search
            size={14}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500 pointer-events-none"
          />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('storyboard.search')}
            className="w-full pl-8 pr-3 py-2 bg-zinc-900 border border-zinc-800 rounded-lg text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 transition-colors"
          />
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
          <div className="flex flex-col gap-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <SkeletonRow key={i} />
            ))}
          </div>
        ) : sorted.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 gap-4 text-center">
            <Layers size={48} className="text-zinc-700" />
            <div>
              <p className="text-sm font-medium text-zinc-400">
                {search ? 'No projects match your search' : t('storyboard.noProjects')}
              </p>
              {!search && (
                <p className="text-xs text-zinc-600 mt-1">{t('storyboard.createFirst')}</p>
              )}
            </div>
            {!search && (
              <button
                type="button"
                onClick={() => setShowNewDialog(true)}
                className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-colors"
              >
                <Plus size={14} />
                New Project
              </button>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-3">
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
