/**
 * BeatsView — the beat-sheet pane (Beats view, PR-BT2).
 *
 * A vertical list of ordered beat cards with an Add Beat header. Beats are
 * drag-reordered (the G2 Outline drag pattern: drag handle + before/after drop
 * indicator) which calls the move API (`after_beat_id` — null = front). CRUD is
 * plain REST: mutate then reload; a failure re-pulls from the server + toasts
 * (beats have no concurrency protocol, so last-write-wins is fine). The empty
 * state mirrors the cold-start affordance (No beats yet + Add).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
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
import { BeatCard } from './BeatCard';

interface Props {
  scriptId: string;
  scenes: SceneDoc[];
  onOpenScene: (sceneId: string) => void;
}

type DropTarget = { beatId: string; edge: 'before' | 'after' };

function edgeFromPointer(el: HTMLElement, clientY: number): 'before' | 'after' {
  const rect = el.getBoundingClientRect();
  return clientY < rect.top + rect.height / 2 ? 'before' : 'after';
}

export function BeatsView({ scriptId, scenes, onOpenScene }: Props) {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const [beats, setBeats] = useState<Beat[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [dragging, setDragging] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<DropTarget | null>(null);
  const draggingRef = useRef<string | null>(null);

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

  const handleAdd = useCallback(async () => {
    try {
      await createBeat(scriptId, { title: t('editor.beatDefaultTitle') });
      await reload();
    } catch (err) {
      console.error('[BeatsView] create failed', err);
      addToast(t('editor.beatCreateFailed'), 'error');
    }
  }, [scriptId, reload, addToast, t]);

  const handleUpdate = useCallback(
    (beatId: string, data: BeatInput) => {
      // Optimistic: merge locally so the edit sticks without a refetch flicker.
      setBeats((prev) =>
        prev.map((b) => (b.id === beatId ? { ...b, ...data } : b)),
      );
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

  const endDrag = useCallback(() => {
    draggingRef.current = null;
    setDragging(null);
    setDropTarget(null);
  }, []);

  const runMove = useCallback(
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

  const handleDrop = useCallback(
    (targetId: string, edge: 'before' | 'after') => {
      const draggedId = draggingRef.current;
      endDrag();
      if (!draggedId || draggedId === targetId) return;
      // Translate the before/after-target drop into an `after_beat_id` anchor
      // (null = front) over the sibling list with the dragged beat removed.
      const ordered = beats.filter((b) => b.id !== draggedId);
      const idx = ordered.findIndex((b) => b.id === targetId);
      if (idx < 0) return;
      const afterBeatId =
        edge === 'after' ? targetId : idx > 0 ? ordered[idx - 1].id : null;
      void runMove(draggedId, afterBeatId);
    },
    [beats, endDrag, runMove],
  );

  if (!loaded) {
    return <div className="mh-beats-view" data-testid="beats-view" />;
  }

  if (beats.length === 0) {
    return (
      <div className="mh-beats-view" data-testid="beats-view">
        <div className="mh-beats-empty" data-testid="beats-empty">
          <div className="mh-beats-empty-title">{t('editor.beatsEmptyTitle')}</div>
          <p className="mh-beats-empty-sub">{t('editor.beatsEmptySub')}</p>
          <button
            type="button"
            className="mh-beats-add-btn"
            data-testid="beats-add"
            onClick={() => void handleAdd()}
          >
            {t('editor.beatAdd')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="mh-beats-view" data-testid="beats-view">
      <div className="mh-beats-head">
        <div className="mh-beats-title">{t('editor.moduleBeats')}</div>
        <button
          type="button"
          className="mh-beats-add-btn"
          data-testid="beats-add"
          onClick={() => void handleAdd()}
        >
          {t('editor.beatAdd')}
        </button>
      </div>

      <div className="mh-beats-list">
        {beats.map((beat, index) => {
          const edge = dropTarget?.beatId === beat.id ? dropTarget.edge : null;
          return (
            <div
              key={beat.id}
              className="mh-beat-row"
              onDragOver={
                dragging && dragging !== beat.id
                  ? (e) => {
                      e.preventDefault();
                      const nextEdge = edgeFromPointer(e.currentTarget, e.clientY);
                      setDropTarget((prev) =>
                        prev && prev.beatId === beat.id && prev.edge === nextEdge
                          ? prev
                          : { beatId: beat.id, edge: nextEdge },
                      );
                    }
                  : undefined
              }
              onDrop={
                dragging
                  ? (e) => {
                      e.preventDefault();
                      handleDrop(beat.id, edgeFromPointer(e.currentTarget, e.clientY));
                    }
                  : undefined
              }
            >
              {edge === 'before' && (
                <div className="mh-drop-indicator before" aria-hidden="true" />
              )}
              <BeatCard
                beat={beat}
                index={index}
                scenes={scenes}
                isDragging={dragging === beat.id}
                onOpenScene={onOpenScene}
                onUpdate={(data) => handleUpdate(beat.id, data)}
                onDelete={() => handleDelete(beat.id)}
                handleProps={{
                  draggable: true,
                  onDragStart: (e) => {
                    if (e.dataTransfer) {
                      e.dataTransfer.effectAllowed = 'move';
                      e.dataTransfer.setData('text/plain', beat.id);
                    }
                    draggingRef.current = beat.id;
                    setDragging(beat.id);
                  },
                  onDragEnd: endDrag,
                }}
              />
              {edge === 'after' && (
                <div className="mh-drop-indicator after" aria-hidden="true" />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
