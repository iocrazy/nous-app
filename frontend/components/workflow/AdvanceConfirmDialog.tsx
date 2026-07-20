/**
 * Advance / retreat confirm gate (Project Workflow M1, PR-C).
 *
 * Mirrors the run-confirm pattern of DispatchConfirmDialog: the dialog never
 * decides — it renders the server's advance-preview ruling, so what it promises
 * and what POST /advance does come from the same predicate (#1400). The three
 * rulings (closing / creating-next / "No agent will start automatically") are
 * exactly the server lists; blocked reasons map to fixed copy.
 */

import React, { useEffect } from 'react';
import { ArrowRight, ArrowLeft, TriangleAlert, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { AdvanceBlockedReason, AdvancePreview } from '../../types';

const BLOCKED_KEY: Record<AdvanceBlockedReason, string> = {
  NOT_MANAGER_OR_EDITOR: 'projects.workflow.confirm.blockedNotManager',
  REVIEW_PENDING: 'projects.workflow.confirm.blockedReviewPending',
  DELIVERABLE_MISSING: 'projects.workflow.confirm.blockedDeliverableMissing',
  NO_NEXT: 'projects.workflow.confirm.blockedNoNext',
};

interface AdvanceConfirmDialogProps {
  /** null while the preview is still loading. */
  preview: AdvancePreview | null;
  direction: 'forward' | 'back';
  confirming?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

export const AdvanceConfirmDialog: React.FC<AdvanceConfirmDialogProps> = ({
  preview,
  direction,
  confirming = false,
  onConfirm,
  onClose,
}) => {
  const { t } = useTranslation();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const blocked = preview && !preview.will_advance ? preview.blocked_reason : null;
  const isBack = direction === 'back';

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 backdrop-blur-sm pt-32"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-label={isBack ? 'Confirm move back' : 'Confirm advance'}
        data-testid="workflow-advance-dialog"
        className="w-full max-w-md overflow-hidden rounded-xl border border-line-strong bg-island shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <h2 className="text-sm font-semibold text-ink-100">
            {t(isBack ? 'projects.workflow.confirm.backTitle' : 'projects.workflow.confirm.advanceTitle')}
          </h2>
          <button
            onClick={onClose}
            className="rounded p-1 text-ink-500 hover:bg-ink-800 hover:text-ink-300"
          >
            <X size={14} />
          </button>
        </header>

        <div className="px-4 py-4 text-[13px]">
          {preview === null ? (
            <p className="text-ink-500">Checking what this would do…</p>
          ) : blocked ? (
            <p className="flex items-start gap-2 text-ink-300">
              <TriangleAlert size={15} className="mt-px shrink-0 text-amber-400" />
              {t(BLOCKED_KEY[blocked])}
            </p>
          ) : (
            <div className="flex flex-col gap-3">
              {preview.closing.length > 0 && (
                <RulingRow
                  label={t('projects.workflow.confirm.closing')}
                  names={preview.closing.map((n) => n.name)}
                />
              )}
              {preview.creating.length > 0 && (
                <RulingRow
                  label={t('projects.workflow.confirm.creating')}
                  names={preview.creating.map((n) =>
                    n.due_date ? `${n.name} · ${n.due_date}` : n.name,
                  )}
                />
              )}
              {preview.warnings.map((w) => (
                <p key={w} className="flex items-start gap-2 text-[12px] text-amber-400">
                  <TriangleAlert size={13} className="mt-px shrink-0" />
                  {w}
                </p>
              ))}
              {!isBack && (
                <p className="text-[12px] text-ink-500">
                  {t('projects.workflow.confirm.noAgentAuto')}
                </p>
              )}
            </div>
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-line bg-island-2 px-4 py-2.5">
          <button
            onClick={onClose}
            className="rounded px-3 py-1.5 text-[13px] text-ink-400 hover:bg-ink-800 hover:text-ink-100"
          >
            {t('projects.workflow.confirm.cancel')}
          </button>
          {preview?.will_advance && (
            <button
              onClick={onConfirm}
              disabled={confirming}
              data-testid="workflow-advance-confirm"
              className="inline-flex items-center gap-1.5 rounded border px-3 py-1.5 text-[13px] transition disabled:opacity-50"
              style={{
                background: 'var(--accent-soft)',
                color: 'var(--accent-text)',
                borderColor: 'var(--accent-border)',
              }}
            >
              {isBack ? <ArrowLeft size={13} /> : <ArrowRight size={13} />}
              {t(isBack ? 'projects.workflow.confirm.back' : 'projects.workflow.confirm.advance')}
            </button>
          )}
        </footer>
      </div>
    </div>
  );
};

const RulingRow: React.FC<{ label: string; names: string[] }> = ({ label, names }) => (
  <div>
    <div className="mb-0.5 text-[11px] uppercase tracking-wider text-ink-600">{label}</div>
    <div className="flex flex-wrap gap-1">
      {names.map((n) => (
        <span key={n} className="rounded-full bg-ink-800 px-2 py-0.5 text-[12px] text-ink-200">
          {n}
        </span>
      ))}
    </div>
  </div>
);
