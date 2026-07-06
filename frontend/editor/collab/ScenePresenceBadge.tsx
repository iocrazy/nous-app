/**
 * ScenePresenceBadge — the soft "who else is here" marker for one scene, shared
 * by the script sheet (SceneBlock head) and the node view (SceneFlowNode). Given
 * the other participants focused on this scene, it shows an "editing" pill when
 * anyone is actively editing, otherwise a viewer count. Purely informational — it
 * never blocks any interaction. Renders nothing when no one else is present.
 */
import { useTranslation } from 'react-i18next';
import type { PresenceUser } from './useScriptPresence';

export function ScenePresenceBadge({ users }: { users: PresenceUser[] }) {
  const { t } = useTranslation();
  if (users.length === 0) return null;

  const editing = users.some((u) => u.mode === 'editing');

  return (
    <span
      className={`mh-scene-presence-badge${editing ? ' editing' : ''}`}
      data-testid="scene-presence-badge"
    >
      {editing
        ? t('editor.collab.editingBadge')
        : t('editor.collab.viewersCount', { count: users.length })}
    </span>
  );
}
