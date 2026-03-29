import { useState, useEffect, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Plus, FileText, ScrollText } from 'lucide-react';
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

  const handleCreate = async () => {
    const name = window.prompt('Script name:');
    if (!name?.trim()) return;
    try {
      const script = await createScriptProject({
        project_id: projectId,
        name: name.trim(),
      });
      navigate(`/team/${teamId}/projects/${projectId}/scripts/${script.id}`);
    } catch (err) {
      console.error('Failed to create script:', err);
    }
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
          onClick={handleCreate}
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
            onClick={handleCreate}
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
    </div>
  );
}
