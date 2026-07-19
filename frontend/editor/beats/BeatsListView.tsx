/**
 * BeatsListView — the Beats "List" sub-view (extracted from BeatsView in M2).
 *
 * A vertical list of ordered beat cards with an Add Beat header. Beats are
 * drag-reordered (the G2 Outline drag pattern: drag handle + before/after drop
 * indicator) which calls `onMove` (`after_beat_id` — null = front). The parent
 * (BeatsView) owns the REST + reload; this component owns only the transient
 * drag UI state. The empty state mirrors the cold-start affordance.
 *
 * This markup is intentionally unchanged from the pre-M2 BeatsView so the M1
 * list regressions keep passing — only the data plumbing moved up a level.
 */
import { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { Beat, BeatInput } from '../sceneService';
import type { SceneDoc } from '../types';
import { BeatCard } from './BeatCard';

interface Props {
  beats: Beat[];
  scenes: SceneDoc[];
  onOpenScene: (sceneId: string) => void;
  onAdd: () => void;
  onUpdate: (beatId: string, data: BeatInput) => void;
  onDelete: (beatId: string) => void;
  onMove: (draggedId: string, afterBeatId: string | null) => void;
}

type DropTarget = { beatId: string; edge: 'before' | 'after' };

function edgeFromPointer(el: HTMLElement, clientY: number): 'before' | 'after' {
  const rect = el.getBoundingClientRect();
  return clientY < rect.top + rect.height / 2 ? 'before' : 'after';
}

export function BeatsListView({
  beats,
  scenes,
  onOpenScene,
  onAdd,
  onUpdate,
  onDelete,
  onMove,
}: Props) {
  const { t } = useTranslation();

  const [dragging, setDragging] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<DropTarget | null>(null);
  const draggingRef = useRef<string | null>(null);

  const endDrag = useCallback(() => {
    draggingRef.current = null;
    setDragging(null);
    setDropTarget(null);
  }, []);

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
      onMove(draggedId, afterBeatId);
    },
    [beats, endDrag, onMove],
  );

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
            onClick={onAdd}
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
          onClick={onAdd}
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
                onUpdate={(data) => onUpdate(beat.id, data)}
                onDelete={() => onDelete(beat.id)}
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
