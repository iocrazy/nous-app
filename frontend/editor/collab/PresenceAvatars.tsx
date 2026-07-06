/**
 * PresenceAvatars — the overlapping avatar stack shown in the editor header
 * (Phase B P5 / C1). Renders one initial-bubble per other participant, themed
 * from the shell's CSS variables (light + dark). The local user is already
 * excluded upstream (useScriptPresence), so this component draws exactly what
 * it is handed. Above `max` participants it collapses the tail into a `+N` chip.
 * Renders nothing when there is no one else present.
 */
import { useTranslation } from 'react-i18next';
import type { PresenceUser } from './useScriptPresence';

/** Max distinct bubbles before collapsing the tail to a `+N` chip. */
const MAX_AVATARS = 4;

/** First character of a display name, uppercased; falls back to '?'. */
function initial(name: string): string {
  const trimmed = name.trim();
  return trimmed.length > 0 ? trimmed[0].toUpperCase() : '?';
}

export function PresenceAvatars({ users, max = MAX_AVATARS }: {
  users: PresenceUser[];
  max?: number;
}) {
  const { t } = useTranslation();
  if (users.length === 0) return null;

  const shown = users.slice(0, max);
  const overflow = users.length - shown.length;

  return (
    <div
      className="mh-presence-avatars"
      data-testid="presence-avatars"
      aria-label={t('editor.collab.presenceLabel', { num: users.length })}
    >
      {shown.map((u) => (
        <span
          key={u.user_id}
          className={`mh-presence-avatar${u.mode === 'editing' ? ' editing' : ''}`}
          title={u.name}
        >
          {initial(u.name)}
        </span>
      ))}
      {overflow > 0 && (
        <span className="mh-presence-avatar overflow" title={t('editor.collab.presenceMore', { num: overflow })}>
          +{overflow}
        </span>
      )}
    </div>
  );
}
