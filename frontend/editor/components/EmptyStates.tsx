/**
 * Empty states for the editor (spec v3 §3.4 D6).
 *
 * - ColdStart: the zero-scene screen. Its single primary action, Create Story,
 *   seeds the first scene with an empty action row and drops the cursor in,
 *   ready to type. There is deliberately NO Import Script affordance in Phase 1.
 * - EmptySceneHint: the inline nudge shown inside a scene block that has no
 *   elements yet.
 */
import { useTranslation } from 'react-i18next';

export function ColdStart({ onCreateStory }: { onCreateStory: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="mh-coldstart" data-testid="cold-start">
      <div className="mh-coldstart-title">{t('editor.coldStartTitle')}</div>
      <p className="mh-coldstart-sub">{t('editor.coldStartSub')}</p>
      <button type="button" className="mh-coldstart-btn" onClick={onCreateStory}>
        {t('editor.createStory')}
      </button>
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
