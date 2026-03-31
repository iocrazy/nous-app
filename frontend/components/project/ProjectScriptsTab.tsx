import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Plus, FileText, ScrollText, X, Loader2 } from 'lucide-react';
import {
  fetchScriptProjects,
  createScriptProject,
} from '../../services/scriptService';
import { ScriptProjectSummary } from '../../types';

interface Props {
  projectId: string;
}

export function ProjectScriptsTab({ projectId }: Props) {
  const navigate = useNavigate();
  const { teamId } = useParams<{ teamId: string }>();
  const [items, setItems] = useState<ScriptProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreateModal, setShowCreateModal] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await fetchScriptProjects(projectId);
      setItems(result.data ?? []);
    } catch (err) {
      console.error('Failed to load scripts:', err);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  const handleCreated = (scriptId: string) => {
    navigate(`/team/${teamId}/projects/${projectId}/scripts/${scriptId}`);
  };

  const handleOpen = (scriptId: string) => {
    navigate(`/team/${teamId}/projects/${projectId}/scripts/${scriptId}`);
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
          {items.length} script{items.length !== 1 ? 's' : ''}
        </h3>
        <button
          onClick={() => setShowCreateModal(true)}
          className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg px-3 py-1.5 text-xs font-medium transition-colors"
        >
          <Plus size={14} />
          New Script
        </button>
      </div>

      {items.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-48 gap-3 text-center">
          <FileText size={40} className="text-zinc-700" />
          <p className="text-sm text-zinc-500">No scripts yet</p>
          <button
            onClick={() => setShowCreateModal(true)}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-colors"
          >
            <Plus size={14} />
            Create First Script
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-4">
          {items.map((script) => (
            <button
              key={script.id}
              onClick={() => handleOpen(script.id)}
              className="w-[280px] text-left group relative flex flex-col rounded-xl border border-zinc-800 bg-zinc-900 p-4 transition-colors hover:border-zinc-600 cursor-pointer"
            >
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-violet-600/20">
                  <ScrollText size={18} className="text-violet-400" />
                </div>
                <div className="min-w-0 flex-1">
                  <h4 className="truncate text-sm font-semibold text-zinc-100">{script.name}</h4>
                  {script.display_code && (
                    <p className="text-[11px] font-mono text-zinc-500 mt-0.5">{script.display_code}</p>
                  )}
                </div>
              </div>
              <div className="mt-2.5">
                <span className="rounded-full border border-violet-800/50 bg-violet-900/40 px-2 py-0.5 text-[11px] text-violet-400">
                  Script
                </span>
              </div>
            </button>
          ))}
        </div>
      )}

      {showCreateModal && (
        <CreateScriptModal
          projectId={projectId}
          onClose={() => setShowCreateModal(false)}
          onCreated={handleCreated}
        />
      )}
    </div>
  );
}

// ─── Create Modal ────────────────────────────────────────────────────────────

function CreateScriptModal({
  projectId,
  onClose,
  onCreated,
}: {
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
      const script = await createScriptProject({
        project_id: projectId,
        name: trimmed,
      });
      onCreated(script.id);
    } catch (err) {
      console.error('Failed to create script:', err);
      setError(err instanceof Error ? err.message : 'Failed to create script');
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
          <h3 className="text-sm font-semibold text-zinc-100">New Script</h3>
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
          placeholder="Enter script name..."
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
