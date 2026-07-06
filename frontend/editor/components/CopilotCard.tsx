/**
 * CopilotCard — the summoned structured-edit card (spec v3 §3.2 / §3.4).
 *
 * NOT a persistent panel: it appears only while a scene has selected elements
 * (summoned by clicking element gutter ticks) and disappears when the selection
 * clears. It offers "Polish format" (local, no LLM) and a free-text reconciler
 * box (Phase 2): the writer describes a change, the backend returns validated
 * ops, and the card walks the applying → done / proposal / failed lifecycle.
 * "Summarize outline" stays a disabled Phase-2-later placeholder.
 *
 * The card is presentational: SceneBlock owns the selection, the op construction
 * / dispatch, the client-side Undo (inverse replay), and the copilot request. The
 * card reflects the phase it is handed and calls back.
 */
import { useTranslation } from 'react-i18next';

export type CopilotPhase = 'attached' | 'applying' | 'done' | 'failed' | 'proposal';

export interface CopilotCardProps {
  /** 1-based scene number for the "Scene N" attachment label. */
  sceneNumber: number;
  /** How many elements are currently selected. */
  selectedCount: number;
  phase: CopilotPhase;
  /** Edits applied this turn (shown in the done state); null before any run. */
  editsThisTurn: number | null;
  /** Whether an inverse batch is available to undo. */
  canUndo: boolean;
  onPolish: () => void;
  onUndo: () => void;
  /** Free-text instruction box (controlled). */
  instruction?: string;
  onInstructionChange?: (value: string) => void;
  onSubmit?: () => void;
  /** Free-text box disabled + tooltip when the backend flag is off (404). */
  freeTextDisabled?: boolean;
  /** One-line summary of what the reconciler did (shown in the done state). */
  summary?: string | null;
  /** Error detail for the failed state (falls back to the generic copy). */
  failedDetail?: string | null;
  /** Proposal (stale-read) branch: apply or discard the reviewed ops. */
  onApply?: () => void;
  onDiscard?: () => void;
}

export function CopilotCard({
  sceneNumber,
  selectedCount,
  phase,
  editsThisTurn,
  canUndo,
  onPolish,
  onUndo,
  instruction = '',
  onInstructionChange,
  onSubmit,
  freeTextDisabled = false,
  summary,
  failedDetail,
  onApply,
  onDiscard,
}: CopilotCardProps) {
  const { t } = useTranslation();
  const applying = phase === 'applying';
  const submitDisabled = freeTextDisabled || applying || instruction.trim().length === 0;

  return (
    <div className="mh-copilot-card" data-testid="copilot-card" data-phase={phase} role="group"
      aria-label={t('editor.copilot')}>
      <div className="mh-copilot-head">
        <span className="mh-copilot-badge">{t('editor.copilotAttached')}</span>
        <span className="mh-copilot-target" data-testid="copilot-target">
          {t('editor.copilotSceneElements', { scene: sceneNumber, count: selectedCount })}
        </span>
      </div>

      <div className="mh-copilot-actions">
        <button
          type="button"
          className="mh-copilot-btn primary"
          disabled={applying}
          onClick={onPolish}
        >
          {applying ? t('editor.copilotApplying') : t('editor.copilotPolish')}
        </button>
        <button
          type="button"
          className="mh-copilot-btn"
          disabled
          title={t('editor.copilotSummarizeSoon')}
        >
          {t('editor.copilotSummarize')}
        </button>
      </div>

      <form
        className="mh-copilot-request"
        onSubmit={(e) => {
          e.preventDefault();
          if (!submitDisabled) onSubmit?.();
        }}
      >
        <input
          type="text"
          className="mh-copilot-input"
          value={instruction}
          disabled={freeTextDisabled || applying}
          onChange={(e) => onInstructionChange?.(e.target.value)}
          placeholder={
            freeTextDisabled ? t('editor.copilotDisabled') : t('editor.copilotRequestPlaceholder')
          }
          title={freeTextDisabled ? t('editor.copilotDisabled') : undefined}
          aria-label={t('editor.copilotRequest')}
        />
        <button type="submit" className="mh-copilot-btn" disabled={submitDisabled}>
          {t('editor.copilotSend')}
        </button>
      </form>

      {phase === 'applying' && (
        <div className="mh-copilot-result" data-testid="copilot-thinking" role="status">
          {t('editor.copilotThinking')}
        </div>
      )}

      {phase === 'proposal' && (
        <div className="mh-copilot-result proposal" data-testid="copilot-proposal" role="status">
          <span>{t('editor.copilotProposal')}</span>
          <div className="mh-copilot-proposal-actions">
            <button type="button" className="mh-copilot-btn primary" onClick={onApply}>
              {t('editor.copilotApply')}
            </button>
            <button type="button" className="mh-copilot-btn" onClick={onDiscard}>
              {t('editor.copilotDiscard')}
            </button>
          </div>
        </div>
      )}

      {phase === 'done' && editsThisTurn !== null && (
        <div className="mh-copilot-result" data-testid="copilot-result">
          {summary && (
            <span className="mh-copilot-summary" data-testid="copilot-summary">
              {summary}
            </span>
          )}
          <span>{t('editor.copilotEdits', { count: editsThisTurn })}</span>
          {canUndo && (
            <button type="button" className="mh-copilot-undo" onClick={onUndo}>
              {t('editor.copilotUndo')}
            </button>
          )}
        </div>
      )}

      {phase === 'failed' && (
        <div className="mh-copilot-result failed" role="alert" data-testid="copilot-failed">
          {failedDetail || t('editor.copilotFailed')}
        </div>
      )}
    </div>
  );
}
