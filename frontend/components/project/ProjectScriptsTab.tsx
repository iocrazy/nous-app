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
        <div>
          <h2 className="text-lg font-semibold text-zinc-100">Scripts</h2>
          <p className="text-xs text-zinc-500 mt-0.5">{items.length} item{items.length !== 1 ? 's' : ''}</p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg px-4 py-2 text-sm font-medium transition-colors"
        >
          <Plus size={15} />
          New Script
        </button>
      </div>

      {items.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-64 gap-4 text-center">
          <div className="w-16 h-16 rounded-2xl bg-zinc-800/50 flex items-center justify-center">
            <FileText size={28} className="text-zinc-600" />
          </div>
          <div>
            <p className="text-sm font-medium text-zinc-400">No scripts yet</p>
            <p className="text-xs text-zinc-600 mt-1">Create a script to organize your story</p>
          </div>
          <button
            onClick={() => setShowCreateModal(true)}
            className="flex items-center gap-2 px-5 py-2.5 text-sm font-medium rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-colors mt-2"
          >
            <Plus size={15} />
            Create First Script
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-5">
          {items.map((script) => {
            const modified = script.updated_at ? new Date(script.updated_at).toLocaleDateString() : '';
            const created = script.created_at ? new Date(script.created_at).toLocaleDateString() : '';
            return (
              <button
                key={script.id}
                onClick={() => handleOpen(script.id)}
                className="w-[280px] text-left group flex flex-col rounded-xl border border-zinc-800/60 bg-zinc-900/50 p-5 transition-all duration-200 hover:border-zinc-600 hover:bg-zinc-800/40 hover:-translate-y-0.5 hover:shadow-lg cursor-pointer"
              >
                <div className="flex items-start gap-3">
                  <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-amber-500/15">
                    <ScrollText size={18} className="text-amber-400" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <h4 className="truncate text-sm font-semibold text-zinc-100">{script.name}</h4>
                  </div>
                </div>
                <div className="mt-3">
                  <span className="rounded-full border border-amber-800/50 bg-amber-900/30 px-2 py-0.5 text-[10px] text-amber-400 font-medium">
                    Script
                  </span>
                  {script.chapter_count != null && (
                    <span className="ml-2 text-[10px] text-zinc-600">{script.chapter_count} chapters</span>
                  )}
                </div>
                <div className="mt-3 space-y-0.5 text-[11px] text-zinc-500">
                  {modified && <div>Modified: {modified}</div>}
                  {created && <div>Created: {created}</div>}
                </div>
              </button>
            );
          })}
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
