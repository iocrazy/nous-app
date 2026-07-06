/**
 * ConflictBar — the non-blocking 409 resolution strip (spec v3 §3.4 D6).
 *
 * When a scene's optimistic edit diverges from the server (a real content
 * conflict, not an identical concurrent edit), useSceneSync freezes that scene
 * and the shell floats this bar at the top of the paper. Phase 1 offers two
 * choices — Keep mine / Take theirs — wired to the scene's resolveConflict. The
 * third spec option (Compare / diff view) is cut for Phase 1 (it depends on a
 * version-diff UI); recorded as a deviation in the plan.
 */
import { useTranslation } from 'react-i18next';

export interface ConflictBarProps {
  onKeepMine: () => void;
  onTakeTheirs: () => void;
}

export function ConflictBar({ onKeepMine, onTakeTheirs }: ConflictBarProps) {
  const { t } = useTranslation();
  return (
    <div className="mh-conflict-bar" data-testid="conflict-bar" role="alert">
      <span className="mh-conflict-msg">{t('editor.conflictMessage')}</span>
      <div className="mh-conflict-actions">
        <button type="button" className="mh-conflict-btn" onClick={onKeepMine}>
          {t('editor.keepMine')}
        </button>
        <button type="button" className="mh-conflict-btn ghost" onClick={onTakeTheirs}>
          {t('editor.takeTheirs')}
        </button>
      </div>
    </div>
  );
}
