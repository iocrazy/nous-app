/**
 * CopilotCard — the summoned structured-edit card (spec v3 §3.2 / §3.4, Task 11).
 *
 * NOT a persistent panel: it appears only while a scene has selected elements
 * (summoned by clicking element gutter ticks) and disappears when the selection
 * clears. Phase 1 offers ONE working action — "Polish format" (local, no LLM) —
 * plus disabled slots for the Phase 2 features (Summarize outline + a free-text
 * request box), which render as visible-but-inert placeholders so the shape of
 * the eventual copilot is legible.
 *
 * The card is presentational: SceneBlock owns the selection, the polish op
 * construction, the op dispatch, and the client-side Undo (inverse replay).
 * The card just reflects the state-machine phase it is handed and calls back.
 */
import { useTranslation } from 'react-i18next';

export type CopilotPhase = 'attached' | 'applying' | 'done' | 'failed';

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
}

export function CopilotCard({
  sceneNumber,
  selectedCount,
  phase,
  editsThisTurn,
  canUndo,
  onPolish,
  onUndo,
}: CopilotCardProps) {
  const { t } = useTranslation();
  const applying = phase === 'applying';

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

      <input
        type="text"
        className="mh-copilot-input"
        disabled
        placeholder={t('editor.copilotComingPhase2')}
        aria-label={t('editor.copilotRequest')}
      />

      {phase === 'done' && editsThisTurn !== null && (
        <div className="mh-copilot-result" data-testid="copilot-result">
          <span>{t('editor.copilotEdits', { count: editsThisTurn })}</span>
          {canUndo && (
            <button type="button" className="mh-copilot-undo" onClick={onUndo}>
              {t('editor.copilotUndo')}
            </button>
          )}
        </div>
      )}

      {phase === 'failed' && (
        <div className="mh-copilot-result failed" role="alert">
          {t('editor.copilotFailed')}
        </div>
      )}
    </div>
  );
}
