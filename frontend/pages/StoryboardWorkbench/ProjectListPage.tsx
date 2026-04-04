import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Plus,
  Search,
  Layers,
  ArrowUpDown,
  Check,
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
  const [showSortMenu, setShowSortMenu] = useState(false);
  const sortRef = useRef<HTMLDivElement>(null);

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
        {/* Left: title + count */}
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-bold text-zinc-100">Projects</h1>
          {!loading && (
            <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-zinc-800 text-zinc-400">
              {sorted.length}
            </span>
          )}
        </div>

        {/* Right: toolbar */}
        <div className="flex items-center gap-2 shrink-0">
          {/* Search */}
          <div className="relative w-48">
            <Search
              size={14}
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500 pointer-events-none"
            />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('storyboard.search')}
              className="w-full pl-8 pr-3 py-1.5 bg-zinc-900 border border-zinc-800 rounded-lg text-xs text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 transition-colors"
            />
          </div>

          {/* Sort dropdown */}
          <div className="relative" ref={sortRef}>
            <button
              type="button"
              onClick={() => setShowSortMenu((prev) => !prev)}
              className={`p-1.5 rounded-lg transition-colors ${
                sortField !== 'created_at' || sortOrder !== 'desc'
                  ? 'text-indigo-400 bg-indigo-500/10 hover:bg-indigo-500/20'
                  : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'
              }`}
              title={`${SORT_FIELD_LABELS[sortField]} (${sortOrder === 'desc' ? 'Desc' : 'Asc'})`}
            >
              <ArrowUpDown size={14} />
            </button>
            {showSortMenu && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setShowSortMenu(false)} />
                <div className="absolute right-0 top-full mt-1.5 z-20 bg-zinc-900/95 backdrop-blur-sm border border-zinc-700/80 rounded-xl shadow-2xl py-1.5 w-44 animate-dropdown">
                  {/* Sort field options */}
                  {(Object.entries(SORT_FIELD_LABELS) as [SortField, string][]).map(([field, label]) => (
                    <button
                      key={field}
                      type="button"
                      onClick={() => { setSortField(field); setShowSortMenu(false); }}
                      className={`w-full text-left px-3 py-2 text-xs transition-colors flex items-center justify-between ${
                        sortField === field
                          ? 'bg-indigo-500/10 text-indigo-400'
                          : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
                      }`}
                    >
                      <span>{label}</span>
                      {sortField === field && <Check size={12} className="text-indigo-400" />}
                    </button>
                  ))}
                  {/* Divider */}
                  <div className="mx-2.5 my-1.5 border-t border-zinc-700/60" />
                  {/* Sort order options */}
                  {([['desc', 'Newest first'], ['asc', 'Oldest first']] as const).map(([order, label]) => (
                    <button
                      key={order}
                      type="button"
                      onClick={() => { setSortOrder(order); setShowSortMenu(false); }}
                      className={`w-full text-left px-3 py-2 text-xs transition-colors flex items-center justify-between ${
                        sortOrder === order
                          ? 'bg-indigo-500/10 text-indigo-400'
                          : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
                      }`}
                    >
                      <span>{label}</span>
                      {sortOrder === order && <Check size={12} className="text-indigo-400" />}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>

          {/* New Project */}
          <button
            type="button"
            onClick={() => setShowNewDialog(true)}
            className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg px-3 py-1.5 text-xs font-medium transition-colors"
          >
            <Plus size={14} />
            New Project
          </button>
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
          <div className="flex flex-wrap gap-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="w-[280px]">
                <SkeletonRow />
              </div>
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
          <div className="flex flex-wrap gap-4">
            {sorted.map((project) => (
              <div key={project.id} className="w-[280px]">
                <ProjectCard
                  project={project}
                  selectMode={false}
                  onClick={handleCardClick}
                  onRename={handleRename}
                  onDelete={handleDelete}
                />
              </div>
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
