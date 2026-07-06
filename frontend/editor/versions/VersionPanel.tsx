/**
 * VersionPanel — the "Version History" section at the top of the Writing panel
 * (Phase B P4, laper baseline). Lists a script's commits over the append-only op
 * ledger and drives the three lifecycle actions:
 *
 *   * Save Version — an inline message input (not a modal) → createCommit → the
 *     list reloads with the new tag on top.
 *   * Compare — hands the commit up to the shell, which swaps the centre pane to
 *     the element-level diff view (against the live 'current' state).
 *   * Roll Back / Delete — two-click inline confirm (G1): the first click arms a
 *     short window, the second executes. A rollback that partially fails is NOT
 *     hidden — its per-scene error codes render inline under the row and a toast
 *     announces the partial result; a clean rollback reloads the editor scenes.
 *
 * Ids are strings end-to-end (Snowflake bigint — never Number()-coerced); every
 * id comparison coerces both sides with String() (#1006). Zero emoji — glyphs
 * follow the editor island's monochrome text convention.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  createCommit,
  deleteCommit,
  listCommits,
  rollbackCommit,
  type RollbackResult,
  type ScriptCommit,
} from '../sceneService';
import { useToast } from '../../components/Toast';

/** How long an armed inline "Confirm?" stays live before auto-disarming. */
const CONFIRM_WINDOW_MS = 3000;

type TranslateFn = (key: string, options?: Record<string, unknown>) => string;

/**
 * Coarse relative time reusing the shared `common.time.*` keys. Inlined (rather
 * than the `utils/relativeTime` helper) so this panel does not pull the i18n
 * bootstrap chain (relativeTime → formatDate → i18n.ts) into the editor bundle,
 * which the editor tests deliberately avoid loading. Falls back to a plain
 * locale date once the timestamp is a week old.
 */
function relativeTime(dateStr: string, t: TranslateFn): string {
  const diffMs = Date.now() - new Date(dateStr).getTime();
  const minutes = Math.floor(diffMs / 60000);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  if (minutes < 1) return t('common.time.justNow');
  if (minutes < 60) return t('common.time.minutesAgo', { count: minutes });
  if (hours < 24) return t('common.time.hoursAgo', { count: hours });
  if (days < 7) return t('common.time.daysAgo', { count: days });
  return new Date(dateStr).toLocaleDateString();
}

export interface VersionPanelProps {
  scriptId: string;
  /** Open the centre-pane diff for this commit (against the live 'current'). */
  onCompare: (commit: ScriptCommit) => void;
  /** A clean rollback landed — the shell should reload its scenes. */
  onRolledBack: () => void;
}

export function VersionPanel({ scriptId, onCompare, onRolledBack }: VersionPanelProps) {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const [commits, setCommits] = useState<ScriptCommit[]>([]);
  const [showInput, setShowInput] = useState(false);
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  // Two-click inline confirm state, keyed by commit id + action so only one row
  // is armed at a time.
  const [confirmRollback, setConfirmRollback] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  // The last rollback that partially failed, rendered inline under its row.
  const [partialDetail, setPartialDetail] = useState<RollbackResult | null>(null);
  const confirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const reload = useCallback(async () => {
    try {
      setCommits(await listCommits(scriptId));
    } catch (err) {
      console.error('[VersionPanel] failed to load commits', err);
    }
  }, [scriptId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(
    () => () => {
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
    },
    [],
  );

  const disarm = useCallback(() => {
    if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
    confirmTimerRef.current = null;
    setConfirmRollback(null);
    setConfirmDelete(null);
  }, []);

  const arm = useCallback(
    (setter: (id: string) => void, id: string) => {
      disarm();
      setter(id);
      confirmTimerRef.current = setTimeout(() => {
        confirmTimerRef.current = null;
        setConfirmRollback(null);
        setConfirmDelete(null);
      }, CONFIRM_WINDOW_MS);
    },
    [disarm],
  );

  const handleSave = useCallback(async () => {
    const trimmed = message.trim();
    if (!trimmed || saving) return;
    setSaving(true);
    try {
      await createCommit(scriptId, trimmed);
      setMessage('');
      setShowInput(false);
      await reload();
    } catch (err) {
      console.error('[VersionPanel] failed to save version', err);
      addToast(t('editor.versionSaveFailed'), 'error');
    } finally {
      setSaving(false);
    }
  }, [message, saving, scriptId, reload, addToast, t]);

  const handleRollback = useCallback(
    async (commit: ScriptCommit) => {
      const id = String(commit.id);
      if (confirmRollback !== id) {
        arm(setConfirmRollback, id);
        return;
      }
      disarm();
      setBusyId(id);
      setPartialDetail(null);
      try {
        const result = await rollbackCommit(scriptId, commit.id);
        if (result.partial_failure) {
          setPartialDetail(result);
          addToast(t('editor.versionRollbackPartial'), 'error');
        } else {
          addToast(t('editor.versionRolledBack'), 'success');
        }
        // Even a partial rollback changed some scenes — reload either way.
        onRolledBack();
        await reload();
      } catch (err) {
        console.error('[VersionPanel] rollback failed', err);
        addToast(t('editor.versionRollbackFailed'), 'error');
      } finally {
        setBusyId(null);
      }
    },
    [confirmRollback, arm, disarm, scriptId, addToast, t, onRolledBack, reload],
  );

  const handleDelete = useCallback(
    async (commit: ScriptCommit) => {
      const id = String(commit.id);
      if (confirmDelete !== id) {
        arm(setConfirmDelete, id);
        return;
      }
      disarm();
      setBusyId(id);
      try {
        await deleteCommit(commit.id);
        await reload();
      } catch (err) {
        console.error('[VersionPanel] delete failed', err);
        addToast(t('editor.versionDeleteFailed'), 'error');
      } finally {
        setBusyId(null);
      }
    },
    [confirmDelete, arm, disarm, addToast, t, reload],
  );

  const failedScenes = partialDetail
    ? partialDetail.results.filter((r) => r.status === 'failed')
    : [];

  return (
    <section className="mh-version-panel" aria-label={t('editor.versionHistory')}>
      <div className="mh-version-head">
        <span className="mh-field-label">{t('editor.versionHistory')}</span>
        {!showInput && (
          <button
            type="button"
            className="mh-version-save-btn"
            onClick={() => {
              setShowInput(true);
              setMessage('');
            }}
          >
            {t('editor.saveVersion')}
          </button>
        )}
      </div>

      {showInput && (
        <div className="mh-version-input-row">
          <input
            className="mh-version-input"
            type="text"
            value={message}
            maxLength={200}
            autoFocus
            placeholder={t('editor.versionMessagePlaceholder')}
            aria-label={t('editor.versionMessagePlaceholder')}
            disabled={saving}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void handleSave();
              if (e.key === 'Escape') {
                setShowInput(false);
                setMessage('');
              }
            }}
          />
          <button
            type="button"
            className="mh-version-input-save"
            disabled={saving || message.trim().length === 0}
            onClick={() => void handleSave()}
          >
            {saving ? t('editor.versionSaving') : t('editor.versionSaveConfirm')}
          </button>
          <button
            type="button"
            className="mh-version-input-cancel"
            disabled={saving}
            aria-label={t('editor.versionCancel')}
            onClick={() => {
              setShowInput(false);
              setMessage('');
            }}
          >
            ×
          </button>
        </div>
      )}

      {commits.length === 0 ? (
        <div className="mh-panel-hint">{t('editor.versionEmpty')}</div>
      ) : (
        <ul className="mh-version-list">
          {commits.map((commit) => {
            const id = String(commit.id);
            const busy = busyId === id;
            return (
              <li key={id} className="mh-version-item" data-testid="version-item">
                <div className="mh-version-item-main">
                  <div className="mh-version-msg">{commit.message}</div>
                  <div className="mh-version-time">
                    {relativeTime(commit.created_at, t)}
                  </div>
                </div>
                <div className="mh-version-actions">
                  <button
                    type="button"
                    className="mh-version-action"
                    disabled={busy}
                    onClick={() => onCompare(commit)}
                  >
                    {t('editor.versionCompare')}
                  </button>
                  <button
                    type="button"
                    className={`mh-version-action${confirmRollback === id ? ' confirming' : ''}`}
                    disabled={busy}
                    aria-busy={busy || undefined}
                    onClick={() => void handleRollback(commit)}
                  >
                    {confirmRollback === id
                      ? t('editor.versionRollbackConfirm')
                      : t('editor.versionRollback')}
                  </button>
                  <button
                    type="button"
                    className={`mh-version-action danger${confirmDelete === id ? ' confirming' : ''}`}
                    disabled={busy}
                    aria-label={t('editor.versionDelete')}
                    onClick={() => void handleDelete(commit)}
                  >
                    {confirmDelete === id
                      ? t('editor.versionDeleteConfirm')
                      : t('editor.versionDelete')}
                  </button>
                </div>

                {partialDetail && String(partialDetail.commit_id) === id && (
                  <div className="mh-version-partial" role="alert" data-testid="rollback-partial">
                    <div className="mh-version-partial-head">
                      {t('editor.versionRollbackPartial')}
                    </div>
                    <ul className="mh-version-partial-list">
                      {failedScenes.map((r) => (
                        <li key={String(r.scene_id)}>
                          {t('editor.versionRollbackSceneFailed', {
                            code: r.error_code ?? 'error',
                          })}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
