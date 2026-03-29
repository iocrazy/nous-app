import { useState, useEffect, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Plus, Clapperboard, Layers } from 'lucide-react';
import { useTeamContext } from '../../contexts/TeamContext';
import {
  fetchProjects as fetchStoryboardProjects,
  createProject as createStoryboardProject,
} from '../../services/storyboardService';
import { ProjectSummary } from '../../types';

interface Props {
  projectId: string;
}

export function ProjectStoryboardTab({ projectId }: Props) {
  const navigate = useNavigate();
  const { teamId } = useParams<{ teamId: string }>();
  const { selectedTeamId } = useTeamContext();
  const [items, setItems] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!selectedTeamId) return;
    setLoading(true);
    try {
      const result = await fetchStoryboardProjects(selectedTeamId, 1, 20, projectId);
      setItems(result.data ?? []);
    } catch (err) {
      console.error('Failed to load storyboards:', err);
    } finally {
      setLoading(false);
    }
  }, [selectedTeamId, projectId]);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async () => {
    if (!selectedTeamId) return;
    const name = window.prompt('Storyboard name:');
    if (!name?.trim()) return;
    try {
      const sb = await createStoryboardProject({
        team_id: selectedTeamId,
        name: name.trim(),
        project_id: projectId,
      });
      navigate(`/team/${teamId}/projects/${projectId}/storyboard/${sb.id}`);
    } catch (err) {
      console.error('Failed to create storyboard:', err);
    }
  };

  const handleOpen = (sbId: string) => {
    navigate(`/team/${teamId}/projects/${projectId}/storyboard/${sbId}`);
  };

  if (loading) {
    return (
      <div className="flex flex-wrap gap-4">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="w-[280px] h-[140px] rounded-xl bg-zinc-900 animate-pulse" />
        ))}
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-zinc-400">
          {items.length} storyboard{items.length !== 1 ? 's' : ''}
        </h3>
        <button
          onClick={handleCreate}
          className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg px-3 py-1.5 text-xs font-medium transition-colors"
        >
          <Plus size={14} />
          New Storyboard
        </button>
      </div>

      {items.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-48 gap-3 text-center">
          <Clapperboard size={40} className="text-zinc-700" />
          <p className="text-sm text-zinc-500">No storyboards yet</p>
          <button
            onClick={handleCreate}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-colors"
          >
            <Plus size={14} />
            Create First Storyboard
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-4">
          {items.map((sb) => (
            <button
              key={sb.id}
              onClick={() => handleOpen(sb.id)}
              className="w-[280px] text-left group relative flex flex-col rounded-xl border border-zinc-800 bg-zinc-900 p-4 transition-colors hover:border-zinc-600 cursor-pointer"
            >
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-blue-600/20">
                  <Layers size={18} className="text-blue-400" />
                </div>
                <div className="min-w-0 flex-1">
                  <h4 className="truncate text-sm font-semibold text-zinc-100">{sb.name}</h4>
                </div>
              </div>
              <div className="mt-2.5">
                <span className="rounded-full border border-blue-800/50 bg-blue-900/40 px-2 py-0.5 text-[11px] text-blue-400">
                  Storyboard
                </span>
              </div>
              <div className="mt-2 text-xs text-zinc-500">
                {sb.frame_count ?? 0} frames
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
