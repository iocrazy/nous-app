import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Plus, Clapperboard, Layers, X, Loader2 } from 'lucide-react';
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
  const [showCreateModal, setShowCreateModal] = useState(false);

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

  const handleCreated = (sbId: string) => {
    navigate(`/team/${teamId}/projects/${projectId}/storyboard/${sbId}`);
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
          onClick={() => setShowCreateModal(true)}
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
            onClick={() => setShowCreateModal(true)}
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

      {showCreateModal && selectedTeamId && (
        <CreateStoryboardModal
          teamId={selectedTeamId}
          projectId={projectId}
          onClose={() => setShowCreateModal(false)}
          onCreated={handleCreated}
        />
      )}
    </div>
  );
}

// ─── Create Modal ────────────────────────────────────────────────────────────

function CreateStoryboardModal({
  teamId,
  projectId,
  onClose,
  onCreated,
}: {
  teamId: string;
  projectId: string;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [name, setName] = useState('');
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSubmit = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setCreating(true);
    setError(null);
    try {
      const sb = await createStoryboardProject({
        team_id: teamId,
        name: trimmed,
        project_id: projectId,
      });
      onCreated(sb.id);
    } catch (err) {
      console.error('Failed to create storyboard:', err);
      setError(err instanceof Error ? err.message : 'Failed to create storyboard');
      setCreating(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && name.trim()) void handleSubmit();
    if (e.key === 'Escape') onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-sm rounded-xl bg-zinc-900 border border-zinc-700 shadow-2xl p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-zinc-100">New Storyboard</h3>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300 transition-colors">
            <X size={16} />
          </button>
        </div>

        <label className="block text-xs text-zinc-400 mb-1.5">Name</label>
        <input
          ref={inputRef}
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Enter storyboard name..."
          className="w-full rounded-lg bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 transition-colors"
        />

        {error && (
          <p className="mt-2 text-xs text-red-400">{error}</p>
        )}

        <div className="flex justify-end gap-2 mt-4">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs font-medium text-zinc-400 hover:text-zinc-200 rounded-lg hover:bg-zinc-800 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => void handleSubmit()}
            disabled={!name.trim() || creating}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {creating && <Loader2 size={12} className="animate-spin" />}
            {creating ? 'Creating...' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  );
}
