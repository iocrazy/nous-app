/**
 * BeatsView — the beat-sheet pane (Beats view). Owns the beats data + REST and
 * hosts the two M2 sub-views behind a persisted segmented toggle:
 *
 *   - Arrangement (default) — the timeline editor (ArrangementView)
 *   - List — the ordered card list (BeatsListView), unchanged from M1
 *
 * CRUD is plain REST: mutate then reload; a failure re-pulls from the server +
 * toasts (beats have no concurrency protocol, so last-write-wins is fine). The
 * sub-view choice persists per-script in localStorage (beatsViewStorage).
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useToast } from '../../components/Toast';
import { fetchScriptProject, updateScriptProject } from '../../services/scriptService';
import {
  createBeat,
  deleteBeat,
  listBeats,
  moveBeat,
  updateBeat,
  type Beat,
  type BeatInput,
} from '../sceneService';
import type { SceneDoc } from '../types';
import { ArrangementView } from './ArrangementView';
import {
  createBeatTemplate,
  deleteBeatTemplate,
  listBeatTemplates,
  type CustomTemplate,
} from './beatTemplateService';
import { BeatsListView } from './BeatsListView';
import type { CustomTemplateAnchor } from './templates';
import {
  persistBeatsSubview,
  readStoredBeatsSubview,
  type BeatsSubview,
} from './beatsViewStorage';

interface Props {
  scriptId: string;
  scenes: SceneDoc[];
  onOpenScene: (sceneId: string) => void;
}

export function BeatsView({ scriptId, scenes, onOpenScene }: Props) {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const [beats, setBeats] = useState<Beat[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [subview, setSubview] = useState<BeatsSubview>(() => readStoredBeatsSubview(scriptId));
  // M3 target total runtime; drives the Arrangement ruler + template defaults.
  const [targetDurationSec, setTargetDurationSec] = useState<number | null>(null);
  // M3.5 user custom templates (global to the caller, not per-script).
  const [customTemplates, setCustomTemplates] = useState<CustomTemplate[]>([]);

  const reload = useCallback(async () => {
    try {
      setBeats(await listBeats(scriptId));
    } catch (err) {
      console.error('[BeatsView] load failed', err);
      addToast(t('editor.beatsLoadFailed'), 'error');
    } finally {
      setLoaded(true);
    }
  }, [scriptId, addToast, t]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Custom templates load once (caller-global). A failure is soft — the built-in
  // three still work; log, no toast.
  const reloadTemplates = useCallback(async () => {
    try {
      setCustomTemplates(await listBeatTemplates());
    } catch (err) {
      console.error('[BeatsView] load templates failed', err);
    }
  }, []);

  useEffect(() => {
    void reloadTemplates();
  }, [reloadTemplates]);

  // Pull the target length once per script — a soft dependency: a failure just
  // leaves the ruler on its beat-derived default (no toast, the timeline works).
  useEffect(() => {
    let cancelled = false;
    fetchScriptProject(scriptId)
      .then((project) => {
        if (!cancelled) setTargetDurationSec(project.target_duration_sec ?? null);
      })
      .catch((err) => console.error('[BeatsView] target length load failed', err));
    return () => {
      cancelled = true;
    };
  }, [scriptId]);

  const handleSetTargetDuration = useCallback(
    (sec: number) => {
      setTargetDurationSec(sec); // optimistic
      updateScriptProject(scriptId, { target_duration_sec: sec }).catch((err) => {
        console.error('[BeatsView] set target length failed', err);
        addToast(t('editor.beatTargetLengthFailed'), 'error');
      });
    },
    [scriptId, addToast, t],
  );

  const handleApplyTemplate = useCallback(
    async (templateBeats: BeatInput[], mode: 'append' | 'replace', targetSec: number) => {
      try {
        if (targetSec !== targetDurationSec) handleSetTargetDuration(targetSec);
        // CREATE first, DELETE last: a mid-batch create failure must leave the
        // user's existing sheet intact (replace degrades to a no-op + toast),
        // never "old sheet destroyed + 7 of 15 template rows". Sequential
        // create preserves template order (create auto-assigns sort_order =
        // script MAX + step, so a parallel burst would race it).
        const priorIds = beats.map((b) => b.id);
        for (const data of templateBeats) {
          await createBeat(scriptId, data);
        }
        if (mode === 'replace') {
          await Promise.all(priorIds.map((id) => deleteBeat(id)));
        }
        await reload();
      } catch (err) {
        console.error('[BeatsView] apply template failed', err);
        addToast(t('editor.beatTemplateApplyFailed'), 'error');
        void reload();
      }
    },
    [scriptId, beats, targetDurationSec, handleSetTargetDuration, reload, addToast, t],
  );

  const handleSaveTemplate = useCallback(
    async (name: string, anchors: CustomTemplateAnchor[]) => {
      try {
        await createBeatTemplate(name, anchors);
        addToast(t('editor.beatSaveTemplateSaved'), 'success');
        await reloadTemplates();
      } catch (err) {
        console.error('[BeatsView] save template failed', err);
        addToast(t('editor.beatSaveTemplateFailed'), 'error');
      }
    },
    [reloadTemplates, addToast, t],
  );

  const handleDeleteCustomTemplate = useCallback(
    async (templateId: string) => {
      // Optimistic removal so the card disappears immediately; re-pull on failure.
      setCustomTemplates((prev) => prev.filter((tpl) => tpl.id !== templateId));
      try {
        await deleteBeatTemplate(templateId);
      } catch (err) {
        console.error('[BeatsView] delete template failed', err);
        addToast(t('editor.beatTemplateDeleteFailed'), 'error');
        void reloadTemplates();
      }
    },
    [reloadTemplates, addToast, t],
  );

  const selectSubview = useCallback(
    (next: BeatsSubview) => {
      setSubview(next);
      persistBeatsSubview(scriptId, next);
    },
    [scriptId],
  );

  const handleAdd = useCallback(async () => {
    try {
      // Append ARRANGED at the end of the timeline (laper behaviour) — an
      // unplaced beat lands in the below-the-fold tray where nobody finds it.
      const end = beats.reduce(
        (max, b) =>
          b.start_sec == null ? max : Math.max(max, b.start_sec + (b.duration_sec ?? 0)),
        0,
      );
      await createBeat(scriptId, {
        title: t('editor.beatDefaultTitle'),
        start_sec: end,
        duration_sec: 60,
      });
      await reload();
    } catch (err) {
      console.error('[BeatsView] create failed', err);
      addToast(t('editor.beatCreateFailed'), 'error');
    }
  }, [scriptId, beats, reload, addToast, t]);

  const handleCreate = useCallback(
    async (data: BeatInput) => {
      try {
        await createBeat(scriptId, { title: t('editor.beatDefaultTitle'), ...data });
        await reload();
      } catch (err) {
        console.error('[BeatsView] create failed', err);
        addToast(t('editor.beatCreateFailed'), 'error');
      }
    },
    [scriptId, reload, addToast, t],
  );

  const handleUpdate = useCallback(
    (beatId: string, data: BeatInput) => {
      // Optimistic: merge locally so the edit sticks without a refetch flicker.
      setBeats((prev) => prev.map((b) => (b.id === beatId ? { ...b, ...data } : b)));
      updateBeat(beatId, data).catch((err) => {
        console.error('[BeatsView] update failed', err);
        addToast(t('editor.beatUpdateFailed'), 'error');
        void reload();
      });
    },
    [reload, addToast, t],
  );

  const handleDelete = useCallback(
    (beatId: string) => {
      setBeats((prev) => prev.filter((b) => b.id !== beatId));
      deleteBeat(beatId).catch((err) => {
        console.error('[BeatsView] delete failed', err);
        addToast(t('editor.beatDeleteFailed'), 'error');
        void reload();
      });
    },
    [reload, addToast, t],
  );

  const handleMove = useCallback(
    async (draggedId: string, afterBeatId: string | null) => {
      try {
        await moveBeat(draggedId, { after_beat_id: afterBeatId });
        await reload();
      } catch (err) {
        console.error('[BeatsView] move failed', err);
        addToast(t('editor.beatMoveFailed'), 'error');
        void reload();
      }
    },
    [reload, addToast, t],
  );

  return (
    <div className="mh-beats-pane" data-testid="beats-pane">
      {/* laper topbar: count left · segmented centre · Add right. */}
      <div className="mh-beats-subview" role="group" aria-label={t('editor.beatsViewLabel')}>
        {/* Left spacer keeps the segmented dead-centre against the right Add. */}
        <span aria-hidden="true" />
        <div className="mh-segmented mh-beats-seg">
          <button
            type="button"
            className={`mh-seg${subview === 'arrangement' ? ' active' : ''}`}
            data-testid="beats-subview-arrangement"
            aria-pressed={subview === 'arrangement'}
            onClick={() => selectSubview('arrangement')}
          >
            {t('editor.beatsViewArrangement')}
          </button>
          <button
            type="button"
            className={`mh-seg${subview === 'list' ? ' active' : ''}`}
            data-testid="beats-subview-list"
            aria-pressed={subview === 'list'}
            onClick={() => selectSubview('list')}
          >
            {t('editor.beatsViewList')}
          </button>
        </div>
        <button
          type="button"
          className="mh-beats-add-ink"
          data-testid="beats-topbar-add"
          onClick={() => void handleAdd()}
        >
          + {t('editor.beatAdd')}
        </button>
      </div>

      {!loaded ? (
        <div className="mh-beats-view" data-testid="beats-view" />
      ) : subview === 'list' ? (
        <BeatsListView
          beats={beats}
          scenes={scenes}
          onOpenScene={onOpenScene}
          onAdd={() => void handleAdd()}
          onUpdate={handleUpdate}
          onDelete={handleDelete}
          onMove={(draggedId, afterId) => void handleMove(draggedId, afterId)}
        />
      ) : (
        <ArrangementView
          scriptId={scriptId}
          beats={beats}
          scenes={scenes}
          targetDurationSec={targetDurationSec}
          onAdd={() => void handleAdd()}
          onUpdate={handleUpdate}
          onCreate={(data) => void handleCreate(data)}
          onOpenScene={onOpenScene}
          onSetTargetDuration={handleSetTargetDuration}
          onApplyTemplate={(templateBeats, mode, targetSec) =>
            void handleApplyTemplate(templateBeats, mode, targetSec)
          }
          customTemplates={customTemplates}
          onSaveTemplate={(name, anchors) => void handleSaveTemplate(name, anchors)}
          onDeleteCustomTemplate={(id) => void handleDeleteCustomTemplate(id)}
        />
      )}
    </div>
  );
}
