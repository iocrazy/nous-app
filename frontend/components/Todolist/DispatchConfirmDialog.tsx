/**
 * Run-confirm gate for dispatching an issue to its agent (multica's
 * run-confirm pattern).
 *
 * The dialog never decides what will happen — it renders the server's
 * dispatch-preview verdict, so what it promises and what POST /dispatch does
 * come from the same predicate. Nothing is written until "Start working" is
 * pressed; Esc / Cancel / backdrop close with zero side effects.
 */

import React, { useEffect } from 'react';
import { Bot, TriangleAlert, X } from 'lucide-react';
import type { DispatchBlockedReason, DispatchPreview } from '../../services/issuesService';

const BLOCKED_LABEL: Record<DispatchBlockedReason, string> = {
  no_assignee: 'No agent is assigned to this issue — assign one first.',
  dbos_disabled: 'The workflow engine is unavailable right now. Try again later.',
  terminal_status: 'This issue is already done or cancelled.',
  already_running: 'An agent is already working on this issue.',
};

interface DispatchConfirmDialogProps {
  /** null while the preview is still loading. */
  preview: DispatchPreview | null;
  /** Resolved from the preview's agent_id by the caller (which holds agentsById). */
  agentName?: string;
  confirming?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

export const DispatchConfirmDialog: React.FC<DispatchConfirmDialogProps> = ({
  preview, agentName, confirming = false, onConfirm, onClose,
}) => {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const blocked = preview && !preview.will_start ? preview.blocked_reason : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 backdrop-blur-sm pt-32"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-label="Confirm dispatch"
        className="w-full max-w-md bg-island border border-line-strong rounded-xl shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between px-4 py-2.5 border-b border-line">
          <h2 className="text-sm font-semibold text-ink-100">Start working?</h2>
          <button onClick={onClose} className="p-1 text-ink-500 hover:text-ink-300 rounded hover:bg-ink-800">
            <X size={14} />
          </button>
        </header>

        <div className="px-4 py-4 text-[13px]">
          {preview === null ? (
            <p className="text-ink-500">Checking what this would start…</p>
          ) : blocked ? (
            <p className="flex items-start gap-2 text-ink-300">
              <TriangleAlert size={15} className="text-amber-400 shrink-0 mt-px" />
              {BLOCKED_LABEL[blocked]}
            </p>
          ) : (
            <p className="flex items-center gap-2 text-ink-300">
              <Bot size={15} className="text-ink-500 shrink-0" />
              <span>
                <span className="font-medium text-ink-100">{agentName ?? 'The assigned agent'}</span>
                {' '}will start working on this issue.
              </span>
            </p>
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 px-4 py-2.5 border-t border-line bg-island-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-[13px] rounded text-ink-400 hover:text-ink-100 hover:bg-ink-800"
          >
            {blocked ? 'Close' : 'Cancel'}
          </button>
          {preview?.will_start && (
            <button
              onClick={onConfirm}
              disabled={confirming}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-[13px] rounded border transition disabled:opacity-50"
              style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)', borderColor: 'var(--accent-border)' }}
            >
              <Bot size={13} /> {confirming ? 'Starting…' : 'Start working'}
            </button>
          )}
        </footer>
      </div>
    </div>
  );
};
