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
import { BeatsListView } from './BeatsListView';
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

  const selectSubview = useCallback(
    (next: BeatsSubview) => {
      setSubview(next);
      persistBeatsSubview(scriptId, next);
    },
    [scriptId],
  );

  const handleAdd = useCallback(async () => {
    try {
      await createBeat(scriptId, { title: t('editor.beatDefaultTitle') });
      await reload();
    } catch (err) {
      console.error('[BeatsView] create failed', err);
      addToast(t('editor.beatCreateFailed'), 'error');
    }
  }, [scriptId, reload, addToast, t]);

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
        <span className="mh-beats-count" data-testid="beats-count">
          {t('editor.moduleBeats')} <b>{beats.length}</b>
        </span>
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
          onAdd={() => void handleAdd()}
          onUpdate={handleUpdate}
          onCreate={(data) => void handleCreate(data)}
          onOpenScene={onOpenScene}
        />
      )}
    </div>
  );
}
