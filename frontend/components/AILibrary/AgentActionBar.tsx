import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Copy, GitFork, MoreHorizontal, Pause, Play, Plus,
  RotateCcw, Trash2,
} from 'lucide-react';
import type { AILibraryAgent } from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { hasPersonalOverride } from './agentOverride';
import { createIssue } from '../../services/issuesService';
import { NewIssueDialog } from '../Todolist/NewIssueDialog';
import { useToast } from '../Toast';

// Agent header action bar, in two groups: what the agent IS ([status chip]
// ["..." overflow]) then what you can DO to it ([Assign Task] [Pause/Resume]).
// The caller adds the primary action to the right of both, so the row reads
// state → secondary → primary instead of six equal-weight controls in a line.
//
// "Run Heartbeat" from paperclip is intentionally NOT ported — mediahub agents
// are service/chat agents without a per-agent heartbeat worker loop, so there
// is no equivalent on-demand wakeup to invoke.

const STATUS_POLL_MS = 10_000;

const CHIP_STATUSES = ['idle', 'running', 'paused'] as const;
type ChipStatus = (typeof CHIP_STATUSES)[number];

/**
 * Narrow whatever `/status` returned to a status we can render.
 *
 * The chip is built entirely out of the value: both its palette lookup and
 * its i18n key. An unexpected value therefore does not degrade — it indexes
 * `styles` with `undefined` (unstyled span) and asks i18next for
 * `aiLibrary.agents.statusChip.undefined`, which has no translation, so the
 * chip renders that raw key path as its label. Observed in the header as a
 * literal "aiLibrary.agents.statusChip.undefined". Treat anything unknown as
 * idle: this is a decorative chip, and a wrong-but-plausible state beats a
 * key path sitting in the page furniture.
 */
function toChipStatus(raw: unknown): ChipStatus {
  return CHIP_STATUSES.includes(raw as ChipStatus) ? (raw as ChipStatus) : 'idle';
}

const StatusChip: React.FC<{ status: ChipStatus }> = ({ status }) => {
  const { t } = useTranslation();
  const styles: Record<ChipStatus, string> = {
    idle: 'border-ink-700 bg-ink-800/80 text-ink-400',
    running: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
    paused: 'border-amber-500/40 bg-amber-500/10 text-warn',
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
  /** Deletes this user-owned agent (hidden for system presets). */
  onDelete?: () => void;
  /**
   * Drops the caller's personal override layer on a system preset (mig 341).
   * The item only renders when the agent actually carries one — see
   * `hasPersonalOverride`.
   */
  onResetOverride?: () => void;
}

export const AgentActionBar: React.FC<AgentActionBarProps> = ({
  agent, readOnly, onAgentUpdated, onDuplicate, onDelete, onResetOverride,
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
      setStatus(toChipStatus(s?.status));
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
    <div className="flex items-center gap-4" data-testid="agent-action-bar">
      {/* ── State: what the agent IS, plus the rarely-used menu ──────────── */}
      <div className="flex items-center gap-2" data-testid="agent-state-group">
        <StatusChip status={status} />

        {/* Overflow menu (paperclip R5): Copy agent ID / Duplicate. */}
        <div className="relative" ref={menuRef}>
          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            className="rounded-lg border border-ink-700 bg-ink-800 p-2 text-ink-300 hover:bg-ink-700 transition-colors"
            aria-label={t('common.more', 'More')}
          >
            <MoreHorizontal size={14} />
          </button>
          {menuOpen && (
            <div className="absolute right-0 top-full z-20 mt-1 w-44 overflow-hidden rounded-lg border border-ink-700 bg-ink-900 shadow-xl">
              <button
                type="button"
                onClick={() => {
                  navigator.clipboard.writeText(agent.id);
                  addToast(t('aiLibrary.agents.idCopied', 'Agent ID copied'), 'success');
                  setMenuOpen(false);
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-ink-300 hover:bg-ink-800"
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
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-ink-300 hover:bg-ink-800"
                >
                  <GitFork size={12} />
                  {t('aiLibrary.agents.duplicate', 'Duplicate')}
                </button>
              )}
              {onResetOverride && hasPersonalOverride(agent) && (
                <button
                  type="button"
                  onClick={() => {
                    setMenuOpen(false);
                    onResetOverride();
                  }}
                  data-testid="agent-reset-override"
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-warn hover:bg-warn-soft"
                >
                  <RotateCcw size={12} />
                  {t('aiLibrary.agents.resetToDefaults', 'Reset to defaults')}
                </button>
              )}
              {onDelete && !agent.is_system_preset && (
                <button
                  type="button"
                  onClick={() => {
                    setMenuOpen(false);
                    onDelete();
                  }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-red-400 hover:bg-red-500/10"
                >
                  <Trash2 size={12} />
                  {t('aiLibrary.agents.delete', 'Delete agent')}
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ── Secondary actions: real work, but not why you opened the page ── */}
      <div className="flex items-center gap-2" data-testid="agent-secondary-group">
        <button
          type="button"
          onClick={() => setAssignOpen(true)}
          data-testid="agent-assign-task"
          className="inline-flex items-center gap-1.5 rounded-lg border border-ink-700 bg-ink-800 px-3 py-2 text-sm font-medium text-ink-200 hover:bg-ink-700 transition-colors whitespace-nowrap"
        >
          <Plus size={14} />
          {t('aiLibrary.agents.assignTask', 'Assign Task')}
        </button>

        {!readOnly && (
          <button
            type="button"
            onClick={() => void togglePause()}
            disabled={busy}
            data-testid="agent-toggle-pause"
            className="inline-flex items-center gap-1.5 rounded-lg border border-ink-700 bg-ink-800 px-3 py-2 text-sm font-medium text-ink-200 hover:bg-ink-700 disabled:opacity-50 transition-colors whitespace-nowrap"
          >
            {paused ? <Play size={14} /> : <Pause size={14} />}
            {paused
              ? t('aiLibrary.agents.resume', 'Resume')
              : t('aiLibrary.agents.pause', 'Pause')}
          </button>
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
