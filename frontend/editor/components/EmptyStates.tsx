/**
 * Empty states for the editor (spec v3 §3.4 D6).
 *
 * - ColdStart: the zero-scene screen. Its primary action, Create Story, seeds
 *   the first scene with an empty action row and drops the cursor in, ready to
 *   type; a secondary Import Script action opens the import modal (paste or
 *   upload an existing screenplay/prose).
 * - EmptySceneHint: the inline nudge shown inside a scene block that has no
 *   elements yet.
 */
import { useTranslation } from 'react-i18next';

export function ColdStart({
  onCreateStory,
  onImport,
}: {
  onCreateStory: () => void;
  onImport?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="mh-coldstart" data-testid="cold-start">
      <div className="mh-coldstart-slug">{t('editor.coldStartSlug')}</div>
      <div className="mh-coldstart-title">{t('editor.coldStartTitle')}</div>
      <p className="mh-coldstart-sub">{t('editor.coldStartSub')}</p>
      <div className="mh-coldstart-ghost" aria-hidden="true">
        <div className="mh-coldstart-gl">
          <span className="mh-coldstart-tick is-action" />
          <span className="mh-coldstart-bar" style={{ width: '78%' }} />
        </div>
        <div className="mh-coldstart-gl is-character">
          <span className="mh-coldstart-tick is-char" />
          <span className="mh-coldstart-bar" style={{ width: '26%' }} />
        </div>
        <div className="mh-coldstart-gl is-dialogue">
          <span className="mh-coldstart-tick is-dlg" />
          <span className="mh-coldstart-bar" style={{ width: '55%' }} />
        </div>
      </div>
      <div className="mh-coldstart-actions">
        <button type="button" className="mh-coldstart-btn" onClick={onCreateStory}>
          {t('editor.createStory')}
        </button>
        {onImport && (
          <button
            type="button"
            className="mh-coldstart-btn mh-coldstart-btn-secondary"
            onClick={onImport}
          >
            {t('editor.importScript')}
          </button>
        )}
      </div>
      <div className="mh-coldstart-keys">
        <span>
          <kbd>Tab</kbd> {t('editor.coldStartKeyTab')}
        </span>
        <span>
          <kbd>Enter</kbd> {t('editor.coldStartKeyEnter')}
        </span>
      </div>
    </div>
  );
}

/**
 * The inline nudge shown inside a scene block that has no elements yet. It is a
 * REAL focusable/clickable affordance (not a static hint): clicking it — or
 * pressing Enter/Tab/Space while focused — seeds the scene's first editable
 * element and drops the caret in, the way a Notion doc always gives you an empty
 * first block. Without this an empty scene was a dead-end: the old static <div>
 * had no editable target, so "press Tab" never fired (Tab is a machine key that
 * only triggers from a focused element line).
 */
export function EmptySceneHint({ onSeed }: { onSeed?: () => void }) {
  const { t } = useTranslation();
  return (
    <div
      className="mh-el-line mh-placeholder-line mh-placeholder-seed"
      data-testid="empty-scene-hint"
      role="button"
      tabIndex={0}
      onClick={onSeed}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === 'Tab' || e.key === ' ') {
          e.preventDefault();
          onSeed?.();
        }
      }}
    >
      {t('editor.emptyScene')}
    </div>
  );
}
