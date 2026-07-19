/**
 * Run-pipeline action (W2b) for the issue detail view.
 *
 * A button that opens a picker of the team's ENABLED pipelines; picking one
 * confirms and POSTs a run against this issue, then notifies the parent to
 * refresh the active-run strip. Kept out of IssueDetailView so that view stays
 * focused.
 */

import React, { useEffect, useState } from 'react';
import { GitBranch, ChevronRight } from 'lucide-react';

import {
  listPipelines,
  runPipeline,
  type Pipeline,
} from '../../services/pipelinesService';
import { useToast } from '../Toast';

interface Props {
  issueId: number | string;
  teamId: string | null;
  onRan?: () => void;
}

export const RunPipelineMenu: React.FC<Props> = ({ issueId, teamId, onRan }) => {
  const { addToast } = useToast();
  const [open, setOpen] = useState(false);
  const [pipelines, setPipelines] = useState<Pipeline[] | null>(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!open || pipelines !== null || !teamId) return;
    (async () => {
      try {
        const all = await listPipelines(teamId);
        setPipelines(all.filter((p) => p.enabled));
      } catch (err) {
        console.error('[RunPipelineMenu] load failed', err);
        addToast(err instanceof Error ? err.message : 'Failed to load pipelines', 'error');
        setPipelines([]);
      }
    })();
  }, [open, pipelines, teamId, addToast]);

  const handleRun = async (pipeline: Pipeline) => {
    setRunning(true);
    try {
      await runPipeline(pipeline.id, issueId);
      addToast(`Pipeline "${pipeline.name}" started`, 'success');
      setOpen(false);
      onRan?.();
    } catch (err) {
      console.error('[RunPipelineMenu] run failed', err);
      addToast(err instanceof Error ? err.message : 'Failed to start pipeline', 'error');
    } finally {
      setRunning(false);
    }
  };

  if (!teamId) return null;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex w-full items-center justify-center gap-1.5 px-3 py-2 text-[13px] rounded border border-ink-800 bg-ink-900/50 text-ink-300 hover:bg-ink-800/60"
        title="Run a content relay pipeline on this issue"
      >
        <GitBranch size={13} /> Run pipeline
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute z-20 mt-1 w-72 rounded border border-ink-800 bg-ink-900 shadow-xl py-1 text-[13px]">
            {pipelines === null ? (
              <div className="px-3 py-2 text-ink-500">Loading…</div>
            ) : pipelines.length === 0 ? (
              <div className="px-3 py-2 text-ink-500">
                No enabled pipelines for this team.
              </div>
            ) : (
              pipelines.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  disabled={running}
                  onClick={() => handleRun(p)}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-ink-200 hover:bg-ink-800/70 disabled:opacity-50"
                >
                  <GitBranch size={13} className="text-cyan-400 shrink-0" />
                  <span className="flex-1 truncate">{p.name}</span>
                  <span className="text-[12px] text-ink-500">{p.steps.length} steps</span>
                  <ChevronRight size={13} className="text-ink-600" />
                </button>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
};
