import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import type { LiveAgentRun } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { getAgentIcon } from './agentIcons';
import { useToast } from '../Toast';

// "Running now" strip for the Workforce board (paperclip live-runs port, R4).
// Polls /ai-library/runs/live every 5s; renders one card per live run with a
// ticking elapsed timer, token count so far, and a Cancel button. Hidden when
// nothing is running.

const POLL_MS = 5_000;

function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

const Elapsed: React.FC<{ since: string }> = ({ since }) => {
  const [, force] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => force((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, []);
  const secs = Math.max(0, Math.round((Date.now() - new Date(since).getTime()) / 1000));
  const text = secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`;
  return <span className="tabular-nums">{text}</span>;
};

export const LiveRunsStrip: React.FC = () => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [runs, setRuns] = useState<LiveAgentRun[]>([]);

  const refresh = useCallback(async () => {
    try {
      const resp = await aiLibraryService.getLiveRuns();
      setRuns(resp.items);
    } catch (err) {
      // The strip is decorative — log, keep last value.
      console.error('[LiveRunsStrip] getLiveRuns failed:', err);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  const handleCancel = async (runId: string): Promise<void> => {
    try {
      await aiLibraryService.cancelRun(runId);
      addToast(t('aiLibrary.agents.runs.cancelRequested', 'Cancel requested'), 'success');
      await refresh();
    } catch (err) {
      console.error('[LiveRunsStrip] cancelRun failed:', err);
      addToast(err instanceof Error ? err.message : String(err), 'error');
    }
  };

  if (runs.length === 0) return null;

  return (
    <section className="mb-5">
      <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold text-ink-200">
        <span className="relative flex h-2 w-2">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
        </span>
        {t('aiLibrary.workforce.runningNow', 'Running now')} ({runs.length})
      </h2>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {runs.map((run) => {
          const Icon = getAgentIcon(run.agent_icon ?? undefined);
          return (
            <div
              key={run.id}
              className="flex items-center gap-3 rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2.5"
            >
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-ink-800 text-ink-300">
                <Icon size={16} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium text-ink-100">
                    {run.agent_name || run.agent_slug || run.agent_id.slice(0, 8)}
                  </span>
                  <span className="rounded border border-ink-700 bg-ink-800 px-1.5 py-px font-mono text-[10px] text-ink-400">
                    {run.trigger}
                  </span>
                </div>
                <div className="mt-0.5 flex items-center gap-2 text-[11px] text-ink-500">
                  <Loader2 size={11} className="animate-spin text-emerald-400" />
                  <Elapsed since={run.started_at} />
                  <span>·</span>
                  <span className="tabular-nums">
                    {formatTokens(run.prompt_tokens + run.completion_tokens)} tok
                  </span>
                </div>
              </div>
              <button
                type="button"
                onClick={() => void handleCancel(run.id)}
                className="shrink-0 rounded-md border border-red-500/30 bg-red-500/10 px-2 py-1 text-[11px] font-medium text-red-300 hover:bg-red-500/20"
              >
                {t('aiLibrary.agents.runs.cancel', 'Cancel')}
              </button>
            </div>
          );
        })}
      </div>
    </section>
  );
};

export default LiveRunsStrip;
