/**
 * ShotCard — one storyboard shot inside a scene column (Phase B P3).
 *
 * Anatomy: a shot-number badge, a row of parameter pills (shot_type /
 * camera_angle / camera_movement cycle their vocabulary on click; focal_length
 * is a small free-text input), a lighting line, an inline-editable description
 * (debounced 600ms → updateShot), a status corner (empty dash / generating
 * pulse / done thumbnail / failed retry label), a Generate button, and an
 * inline-confirm delete.
 *
 * The card is a drag handle for within-column reorder (HTML5 DnD, the same
 * drop-indicator pattern as OutlineView); the owning column supplies the drag
 * wiring through `reorder`. `status` and the produced media URLs are read-only
 * here — they flow through the generate workflow, never updateShot.
 */
import { memo, useCallback, useEffect, useRef, useState, type DragEvent } from 'react';
import { useTranslation } from 'react-i18next';
import type { Shot } from '../sceneService';
import { SHOT_PARAM_VOCAB, cycleVocab, type ShotParamField } from './vocab';

/** Debounce window for committing an edited description. */
const DESCRIPTION_DEBOUNCE_MS = 600;
/** How long an armed delete "Confirm?" stays live before auto-disarming. */
const CONFIRM_WINDOW_MS = 3000;

/** Per-card slice of the column's drag state + callbacks. */
export interface ShotReorderApi {
  isDragging: boolean;
  dropEdge: 'before' | 'after' | null;
  onDragStart: () => void;
  onDragEnd: () => void;
  onDragOver: (edge: 'before' | 'after') => void;
  onDrop: (edge: 'before' | 'after') => void;
}

export interface ShotCardProps {
  shot: Shot;
  /** 1-based fallback number when the shot has no explicit shot_number. */
  index: number;
  onUpdate: (shotId: string, data: Partial<Shot>) => void;
  onDelete: (shotId: string) => void;
  /** Generate wiring (Task 5). Undefined → the button renders disabled. */
  onGenerate?: (shotId: string) => void;
  reorder: ShotReorderApi;
}

const PARAM_FIELDS: ShotParamField[] = ['shot_type', 'camera_angle', 'camera_movement'];

/** Which half of the card the pointer is over → the drop edge. */
function edgeFromPointer(el: HTMLElement, clientY: number): 'before' | 'after' {
  const rect = el.getBoundingClientRect();
  return clientY < rect.top + rect.height / 2 ? 'before' : 'after';
}

function ShotCardImpl({ shot, index, onUpdate, onDelete, onGenerate, reorder }: ShotCardProps) {
  const { t } = useTranslation();

  // Description: local state + debounced commit. `editedRef` gates the effect so
  // an inbound prop change (e.g. reload) never echoes back as a spurious PATCH.
  const [desc, setDesc] = useState(shot.description ?? '');
  const editedRef = useRef(false);
  useEffect(() => {
    // Re-sync when the upstream row changes and the user isn't mid-edit.
    if (!editedRef.current) setDesc(shot.description ?? '');
  }, [shot.description]);
  useEffect(() => {
    if (!editedRef.current) return;
    const timer = setTimeout(() => {
      editedRef.current = false;
      if (desc !== (shot.description ?? '')) onUpdate(shot.id, { description: desc });
    }, DESCRIPTION_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [desc, shot.id, shot.description, onUpdate]);

  const handleDescChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    editedRef.current = true;
    setDesc(e.target.value);
  }, []);

  const cyclePill = useCallback(
    (field: ShotParamField) => {
      const next = cycleVocab(SHOT_PARAM_VOCAB[field], shot[field]);
      onUpdate(shot.id, { [field]: next });
    },
    [shot, onUpdate],
  );

  const handleFocalCommit = useCallback(
    (e: React.FocusEvent<HTMLInputElement>) => {
      const value = e.target.value.trim();
      if (value !== (shot.focal_length ?? '')) {
        onUpdate(shot.id, { focal_length: value || null });
      }
    },
    [shot.id, shot.focal_length, onUpdate],
  );

  // Inline-confirm delete: first click arms, second (within the window) deletes.
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const confirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
    },
    [],
  );
  const handleDeleteClick = useCallback(() => {
    if (confirmingDelete) {
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
      confirmTimerRef.current = null;
      setConfirmingDelete(false);
      onDelete(shot.id);
      return;
    }
    setConfirmingDelete(true);
    confirmTimerRef.current = setTimeout(() => {
      confirmTimerRef.current = null;
      setConfirmingDelete(false);
    }, CONFIRM_WINDOW_MS);
  }, [confirmingDelete, onDelete, shot.id]);

  const number = shot.shot_number ?? index;
  const generateReady = typeof onGenerate === 'function';

  return (
    <div className="mh-shot-card-wrap">
      {reorder.dropEdge === 'before' && (
        <div className="mh-drop-indicator before" aria-hidden="true" />
      )}
      <article
        className={`mh-shot-card${reorder.isDragging ? ' dragging' : ''}`}
        data-testid="shot-card"
        data-shot-id={shot.id}
        data-status={shot.status}
        draggable
        onDragStart={(e: DragEvent<HTMLElement>) => {
          if (e.dataTransfer) {
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', shot.id);
          }
          reorder.onDragStart();
        }}
        onDragEnd={reorder.onDragEnd}
        onDragOver={(e: DragEvent<HTMLElement>) => {
          e.preventDefault();
          reorder.onDragOver(edgeFromPointer(e.currentTarget, e.clientY));
        }}
        onDrop={(e: DragEvent<HTMLElement>) => {
          e.preventDefault();
          reorder.onDrop(edgeFromPointer(e.currentTarget, e.clientY));
        }}
      >
        <header className="mh-shot-head">
          <span className="mh-scene-num-badge mh-shot-num" aria-label={t('editor.shotNumber')}>
            {number}
          </span>
          <div className="mh-shot-status-corner" data-testid="shot-status">
            {shot.status === 'empty' && (
              <span className="mh-shot-status empty" aria-label={t('editor.shotStatusEmpty')}>
                —
              </span>
            )}
            {shot.status === 'generating' && (
              <span
                className="mh-shot-status generating"
                role="status"
                aria-label={t('editor.shotStatusGenerating')}
              >
                <span className="mh-shot-pulse" aria-hidden="true" />
                {t('editor.shotStatusGenerating')}
              </span>
            )}
            {shot.status === 'done' && (shot.thumbnail_url || shot.image_url) && (
              <img
                className="mh-shot-thumb"
                src={shot.thumbnail_url || shot.image_url || undefined}
                alt={t('editor.shotThumbAlt', { number })}
                loading="lazy"
              />
            )}
            {shot.status === 'failed' && (
              <span className="mh-shot-status failed" aria-label={t('editor.shotStatusFailed')}>
                {t('editor.shotRetry')}
              </span>
            )}
          </div>
          <button
            type="button"
            className={`mh-shot-delete${confirmingDelete ? ' confirming' : ''}`}
            aria-label={t('editor.shotDelete')}
            onClick={handleDeleteClick}
          >
            {confirmingDelete ? t('editor.nodesConfirm') : '×'}
          </button>
        </header>

        <div className="mh-shot-pills">
          {PARAM_FIELDS.map((field) => (
            <button
              key={field}
              type="button"
              className={`mh-shot-pill${shot[field] ? '' : ' empty'}`}
              data-testid={`shot-pill-${field}`}
              onClick={() => cyclePill(field)}
            >
              {shot[field] || t('editor.shotPillUnset')}
            </button>
          ))}
          <input
            type="text"
            className="mh-shot-focal"
            data-testid="shot-focal"
            defaultValue={shot.focal_length ?? ''}
            placeholder={t('editor.shotFocalPlaceholder')}
            aria-label={t('editor.shotFocalLength')}
            onBlur={handleFocalCommit}
          />
        </div>

        {shot.lighting && (
          <div className="mh-shot-lighting" data-testid="shot-lighting">
            {shot.lighting}
          </div>
        )}

        <textarea
          className="mh-shot-desc"
          data-testid="shot-desc"
          value={desc}
          rows={2}
          placeholder={t('editor.shotDescPlaceholder')}
          aria-label={t('editor.shotDescription')}
          onChange={handleDescChange}
        />

        <footer className="mh-shot-foot">
          <button
            type="button"
            className="mh-shot-generate"
            disabled={!generateReady || shot.status === 'generating'}
            onClick={generateReady ? () => onGenerate?.(shot.id) : undefined}
          >
            {t('editor.shotGenerate')}
          </button>
        </footer>
      </article>
      {reorder.dropEdge === 'after' && (
        <div className="mh-drop-indicator after" aria-hidden="true" />
      )}
    </div>
  );
}

export const ShotCard = memo(ShotCardImpl);
