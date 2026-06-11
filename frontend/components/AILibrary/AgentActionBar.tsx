import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Copy, GitFork, MoreHorizontal, Pause, Play, Plus } from 'lucide-react';
import type { AILibraryAgent } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { createIssue } from '../../services/issuesService';
import { NewIssueDialog } from '../Todolist/NewIssueDialog';
import { useToast } from '../Toast';

// Paperclip-style agent header action bar: [Assign Task] [Pause/Resume]
// [status chip]. "Run Heartbeat" from paperclip is intentionally NOT ported —
// mediahub agents are service/chat agents without a per-agent heartbeat
// worker loop, so there is no equivalent on-demand wakeup to invoke.

const STATUS_POLL_MS = 10_000;

type ChipStatus = 'idle' | 'running' | 'paused';

const StatusChip: React.FC<{ status: ChipStatus }> = ({ status }) => {
  const { t } = useTranslation();
  const styles: Record<ChipStatus, string> = {
    idle: 'border-zinc-700 bg-zinc-800/80 text-zinc-400',
    running: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
    paused: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
  };
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium ${styles[status]}`}
    >
      {status === 'running' && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
        </span>
      )}
      {t(`aiLibrary.agents.statusChip.${status}`, status)}
    </span>
  );
};

interface AgentActionBarProps {
  agent: AILibraryAgent;
  /** Presets are read-only — pause/resume hidden for them. */
  readOnly: boolean;
  /** Called after pause/resume succeeds so the parent refreshes the agent row. */
  onAgentUpdated: (agent: AILibraryAgent) => void;
  /** Opens the fork modal (Duplicate in the overflow menu). */
  onDuplicate?: () => void;
}

export const AgentActionBar: React.FC<AgentActionBarProps> = ({
  agent, readOnly, onAgentUpdated, onDuplicate,
}) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [status, setStatus] = useState<ChipStatus>(
    agent.paused_reason ? 'paused' : 'idle',
  );
  const [busy, setBusy] = useState(false);
  const [assignOpen, setAssignOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  // Close the overflow menu on outside click.
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [menuOpen]);

  const refreshStatus = useCallback(async () => {
    try {
      const s = await aiLibraryService.getAgentStatus(agent.slug);
      setStatus(s.status);
    } catch (err) {
      // Status chip is decorative — log, keep last value.
      console.error('[AgentActionBar] getAgentStatus failed:', err);
    }
  }, [agent.slug]);

  useEffect(() => {
    void refreshStatus();
    const id = window.setInterval(() => void refreshStatus(), STATUS_POLL_MS);
    return () => window.clearInterval(id);
  }, [refreshStatus]);

  const paused = status === 'paused' || !!agent.paused_reason;

  const togglePause = async (): Promise<void> => {
    setBusy(true);
    try {
      const updated = paused
        ? await aiLibraryService.resumeAgent(agent.slug)
        : await aiLibraryService.pauseAgent(agent.slug);
      onAgentUpdated(updated);
      setStatus(updated.paused_reason ? 'paused' : 'idle');
      addToast(
        paused
          ? t('aiLibrary.agents.resumed', 'Agent resumed')
          : t('aiLibrary.agents.pausedToast', 'Agent paused'),
        'success',
      );
    } catch (err) {
      console.error('[AgentActionBar] pause/resume failed:', err);
      addToast(err instanceof Error ? err.message : String(err), 'error');
    } finally {
      setBusy(false);
    }
  };

  const handleCreateIssue = async (payload: Parameters<typeof createIssue>[0]) => {
    await createIssue(payload);
    addToast(t('aiLibrary.agents.taskAssigned', 'Task assigned'), 'success');
    setAssignOpen(false);
  };

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => setAssignOpen(true)}
        className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm font-medium text-zinc-200 hover:bg-zinc-700 transition-colors whitespace-nowrap"
      >
        <Plus size={14} />
        {t('aiLibrary.agents.assignTask', 'Assign Task')}
      </button>

      {!readOnly && (
        <button
          type="button"
          onClick={() => void togglePause()}
          disabled={busy}
          className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm font-medium text-zinc-200 hover:bg-zinc-700 disabled:opacity-50 transition-colors whitespace-nowrap"
        >
          {paused ? <Play size={14} /> : <Pause size={14} />}
          {paused
            ? t('aiLibrary.agents.resume', 'Resume')
            : t('aiLibrary.agents.pause', 'Pause')}
        </button>
      )}

      <StatusChip status={status} />

      {/* Overflow menu (paperclip R5): Copy agent ID / Duplicate. */}
      <div className="relative" ref={menuRef}>
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          className="rounded-lg border border-zinc-700 bg-zinc-800 p-2 text-zinc-300 hover:bg-zinc-700 transition-colors"
          aria-label={t('common.more', 'More')}
        >
          <MoreHorizontal size={14} />
        </button>
        {menuOpen && (
          <div className="absolute right-0 top-full z-20 mt-1 w-44 overflow-hidden rounded-lg border border-zinc-700 bg-zinc-900 shadow-xl">
            <button
              type="button"
              onClick={() => {
                navigator.clipboard.writeText(agent.id);
                addToast(t('aiLibrary.agents.idCopied', 'Agent ID copied'), 'success');
                setMenuOpen(false);
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-zinc-300 hover:bg-zinc-800"
            >
              <Copy size={12} />
              {t('aiLibrary.agents.copyId', 'Copy agent ID')}
            </button>
            {onDuplicate && (
              <button
                type="button"
                onClick={() => {
                  setMenuOpen(false);
                  onDuplicate();
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-zinc-300 hover:bg-zinc-800"
              >
                <GitFork size={12} />
                {t('aiLibrary.agents.duplicate', 'Duplicate')}
              </button>
            )}
          </div>
        )}
      </div>

      {assignOpen && (
        <NewIssueDialog
          agents={[{ id: agent.id, slug: agent.slug, name: agent.name }]}
          teamId={null}
          defaultAgentId={agent.id}
          onClose={() => setAssignOpen(false)}
          onSubmit={handleCreateIssue}
        />
      )}
    </div>
  );
};

export default AgentActionBar;
