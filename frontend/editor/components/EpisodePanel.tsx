/**
 * EpisodePanel — multi-episode management for the left rail (Phase B P2, Task 5).
 *
 * A controlled panel: the shell owns the project's episode list and the current
 * script's episode; this component renders them and performs the mutations,
 * then calls `onChanged` so the shell re-fetches. It offers:
 *  - reassigning the current script to another episode (updateScriptProject);
 *  - inline rename of an episode title (double-click → updateEpisode);
 *  - New Episode (createEpisode);
 *  - delete, disabled for a non-empty episode — the episode_id FK is ON DELETE
 *    RESTRICT, so `script_count > 0` cannot be removed (the button explains why).
 *
 * #1006: episode ids are native numbers at runtime though typed string, so the
 * current-episode comparison coerces both sides with String().
 */
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  createEpisode,
  deleteEpisode,
  updateEpisode,
  type Episode,
} from '../sceneService';
import { updateScriptProject } from '../../services/scriptService';
import { useToast } from '../../components/Toast';

export interface EpisodePanelProps {
  scriptId: string;
  projectId: string;
  episodes: Episode[];
  currentEpisodeId: string | null;
  /** Re-fetch project + episodes after any mutation settles. */
  onChanged: () => void | Promise<void>;
  onClose: () => void;
}

/** Same coercion the rest of the editor uses for native-int ids (#1006). */
function sameId(a: unknown, b: unknown): boolean {
  return a != null && b != null && String(a) === String(b);
}

export function EpisodePanel({
  scriptId,
  projectId,
  episodes,
  currentEpisodeId,
  onChanged,
  onClose,
}: EpisodePanelProps) {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);

  const run = useCallback(
    async (op: () => Promise<unknown>) => {
      setBusy(true);
      try {
        await op();
        await onChanged();
      } catch (err) {
        // Surface the failure — this is exactly where a DELETE RESTRICT race
        // (episode filled between list + delete) would otherwise vanish.
        console.error('[EpisodePanel] episode mutation failed', err);
        addToast(t('editor.episodeActionFailed'), 'error');
      } finally {
        setBusy(false);
      }
    },
    [onChanged, addToast, t],
  );

  const handleReassign = useCallback(
    (episodeId: string) => {
      if (sameId(episodeId, currentEpisodeId)) return;
      void run(() => updateScriptProject(scriptId, { episode_id: episodeId }));
    },
    [run, scriptId, currentEpisodeId],
  );

  const handleCreate = useCallback(() => {
    void run(() => createEpisode(projectId));
  }, [run, projectId]);

  const startRename = useCallback((ep: Episode) => {
    setEditingId(ep.id);
    setDraft(ep.title);
  }, []);

  const commitRename = useCallback(
    (episodeId: string) => {
      const title = draft.trim();
      setEditingId(null);
      const original = episodes.find((e) => e.id === episodeId);
      if (!title || (original && original.title === title)) return;
      void run(() => updateEpisode(episodeId, { title }));
    },
    [draft, episodes, run],
  );

  const handleDelete = useCallback(
    (ep: Episode) => {
      if ((ep.script_count ?? 0) > 0) return;
      void run(() => deleteEpisode(ep.id));
    },
    [run],
  );

  return (
    <div className="mh-ep-panel" data-testid="episode-panel">
      <div className="mh-ep-panel-head">
        <span className="mh-rail-section-label">{t('editor.episodesTitle')}</span>
        <button
          type="button"
          className="mh-ep-new-btn"
          onClick={handleCreate}
          disabled={busy}
        >
          {t('editor.newEpisode')}
        </button>
        <button
          type="button"
          className="mh-icon-btn"
          aria-label={t('editor.epClose')}
          onClick={onClose}
        >
          ×
        </button>
      </div>

      <ul className="mh-ep-list">
        {episodes.map((ep) => {
          const isCurrent = sameId(ep.id, currentEpisodeId);
          const count = ep.script_count ?? 0;
          const deletable = count === 0;
          return (
            <li key={ep.id} className="mh-ep-item" data-testid="episode-item">
              {editingId === ep.id ? (
                <input
                  className="mh-ep-rename-input"
                  aria-label={t('editor.epRename')}
                  value={draft}
                  autoFocus
                  onChange={(e) => setDraft(e.target.value)}
                  onBlur={() => commitRename(ep.id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      commitRename(ep.id);
                    } else if (e.key === 'Escape') {
                      e.preventDefault();
                      setEditingId(null);
                    }
                  }}
                />
              ) : (
                <button
                  type="button"
                  className={`mh-ep-item-main${isCurrent ? ' current' : ''}`}
                  aria-current={isCurrent ? 'true' : undefined}
                  disabled={isCurrent || busy}
                  onClick={() => handleReassign(ep.id)}
                  onDoubleClick={() => startRename(ep)}
                >
                  <span className="mh-ep-item-title">{ep.title}</span>
                  <span
                    className="mh-ep-item-count"
                    title={t('editor.epScriptsTitle')}
                  >
                    {count}
                  </span>
                  {isCurrent && (
                    <span className="mh-ep-current-badge">{t('editor.epCurrent')}</span>
                  )}
                </button>
              )}
              <button
                type="button"
                className="mh-ep-delete-btn"
                aria-label={t('editor.epDelete')}
                title={deletable ? t('editor.epDelete') : t('editor.epDeleteBlocked')}
                disabled={!deletable || busy}
                onClick={() => handleDelete(ep)}
              >
                ×
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
