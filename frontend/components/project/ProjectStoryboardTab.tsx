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
          <div key={i} className="w-[280px] h-[140px] rounded-xl bg-ink-900 animate-pulse" />
        ))}
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-semibold text-ink-100">Storyboards</h2>
          <p className="text-xs text-ink-500 mt-0.5">{items.length} item{items.length !== 1 ? 's' : ''}</p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg px-4 py-2 text-sm font-medium transition-colors"
        >
          <Plus size={15} />
          New Storyboard
        </button>
      </div>

      {items.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-64 gap-4 text-center">
          <div className="w-16 h-16 rounded-2xl bg-ink-800/50 flex items-center justify-center">
            <Clapperboard size={28} className="text-ink-600" />
          </div>
          <div>
            <p className="text-sm font-medium text-ink-400">No storyboards yet</p>
            <p className="text-xs text-ink-600 mt-1">Create a storyboard to start visual storytelling</p>
          </div>
          <button
            onClick={() => setShowCreateModal(true)}
            className="flex items-center gap-2 px-5 py-2.5 text-sm font-medium rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-colors mt-2"
          >
            <Plus size={15} />
            Create First Storyboard
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-5">
          {items.map((sb) => {
            const modified = sb.updated_at ? new Date(sb.updated_at).toLocaleDateString() : '';
            const created = sb.created_at ? new Date(sb.created_at).toLocaleDateString() : '';
            return (
              <button
                key={sb.id}
                onClick={() => handleOpen(sb.id)}
                className="w-[280px] text-left group flex flex-col rounded-xl border border-ink-800/60 bg-ink-900/50 p-5 transition-all duration-200 hover:border-ink-600 hover:bg-ink-800/40 hover:-translate-y-0.5 hover:shadow-lg cursor-pointer"
              >
                <div className="flex items-start gap-3">
                  <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-indigo-500/15">
                    <Layers size={18} className="text-indigo-400" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <h4 className="truncate text-sm font-semibold text-ink-100">{sb.name}</h4>
                  </div>
                </div>
                <div className="mt-3">
                  <span className="rounded-full border border-indigo-800/50 bg-indigo-900/30 px-2 py-0.5 text-[10px] text-indigo-400 font-medium">
                    Storyboard
                  </span>
                  <span className="ml-2 text-[10px] text-ink-600">{sb.frame_count ?? 0} frames</span>
                </div>
                <div className="mt-3 space-y-0.5 text-[11px] text-ink-500">
                  {modified && <div>Modified: {modified}</div>}
                  {created && <div>Created: {created}</div>}
                </div>
              </button>
            );
          })}
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
      <div className="w-full max-w-sm rounded-xl bg-ink-900 border border-ink-700 shadow-2xl p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-ink-100">New Storyboard</h3>
          <button onClick={onClose} className="text-ink-500 hover:text-ink-300 transition-colors">
            <X size={16} />
          </button>
        </div>

        <label className="block text-xs text-ink-400 mb-1.5">Name</label>
        <input
          ref={inputRef}
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Enter storyboard name..."
          className="w-full rounded-lg bg-ink-800 border border-ink-700 px-3 py-2 text-sm text-ink-100 placeholder-ink-500 focus:outline-none focus:border-indigo-500 transition-colors"
        />

        {error && (
          <p className="mt-2 text-xs text-red-400">{error}</p>
        )}

        <div className="flex justify-end gap-2 mt-4">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs font-medium text-ink-400 hover:text-ink-200 rounded-lg hover:bg-ink-800 transition-colors"
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
