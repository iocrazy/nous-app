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
      <div className="mh-coldstart-title">{t('editor.coldStartTitle')}</div>
      <p className="mh-coldstart-sub">{t('editor.coldStartSub')}</p>
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
    </div>
  );
}

export function EmptySceneHint() {
  const { t } = useTranslation();
  return (
    <div className="mh-el-line mh-placeholder-line" data-testid="empty-scene-hint">
      {t('editor.emptyScene')}
    </div>
  );
}
