/**
 * BeatCard — one card in the beat sheet (Beats view, PR-BT2).
 *
 * A number badge + inline-editable title (blur / Enter commits), a collapsible
 * summary textarea, a row of linked-scene chips (click → jump to the scene in
 * the script; × unlinks) with a "link scene" picker, a drag handle for reorder,
 * and a two-click-confirm delete. All edits route up through `onUpdate` /
 * `onDelete`; the parent owns the REST + reload.
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type DragEventHandler,
} from 'react';
import { useTranslation } from 'react-i18next';

import type { Beat, BeatInput } from '../sceneService';
import type { SceneDoc } from '../types';
import { BEAT_COLORS, formatBeatDuration } from './beatColors';
import { BeatSceneLinks } from './BeatSceneLinks';

/** Auto-disarm window for the two-click delete confirm (ms). */
const CONFIRM_WINDOW_MS = 3000;

export interface BeatCardHandleProps {
  draggable: true;
  onDragStart: DragEventHandler;
  onDragEnd: DragEventHandler;
}

interface Props {
  beat: Beat;
  index: number;
  scenes: SceneDoc[];
  isDragging: boolean;
  onOpenScene: (sceneId: string) => void;
  onUpdate: (data: BeatInput) => void;
  onDelete: () => void;
  handleProps: BeatCardHandleProps;
}

export function BeatCard({
  beat,
  index,
  scenes,
  isDragging,
  onOpenScene,
  onUpdate,
  onDelete,
  handleProps,
}: Props) {
  const { t } = useTranslation();

  const [titleDraft, setTitleDraft] = useState(beat.title);
  useEffect(() => setTitleDraft(beat.title), [beat.title]);

  const [notesOpen, setNotesOpen] = useState(
    !!beat.summary || beat.duration_sec != null || !!beat.color,
  );
  const [summaryDraft, setSummaryDraft] = useState(beat.summary ?? '');
  useEffect(() => setSummaryDraft(beat.summary ?? ''), [beat.summary]);

  const [durationDraft, setDurationDraft] = useState(
    beat.duration_sec == null ? '' : String(beat.duration_sec),
  );
  useEffect(
    () => setDurationDraft(beat.duration_sec == null ? '' : String(beat.duration_sec)),
    [beat.duration_sec],
  );

  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const confirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
    },
    [],
  );

  const commitTitle = useCallback(() => {
    const next = titleDraft.trim();
    if (!next) {
      setTitleDraft(beat.title); // title is required; revert an empty edit.
      return;
    }
    if (next !== beat.title) onUpdate({ title: next });
  }, [titleDraft, beat.title, onUpdate]);

  const commitSummary = useCallback(() => {
    const next = summaryDraft.trim();
    if (next !== (beat.summary ?? '')) onUpdate({ summary: next || null });
  }, [summaryDraft, beat.summary, onUpdate]);

  const commitDuration = useCallback(() => {
    const raw = durationDraft.trim();
    if (raw === '') {
      if (beat.duration_sec != null) onUpdate({ duration_sec: null });
      return;
    }
    const parsed = Number.parseInt(raw, 10);
    // Upper bound mirrors the server's PG INTEGER ceiling — out-of-range input
    // reverts to the saved value instead of round-tripping into a 422/500.
    if (Number.isNaN(parsed) || parsed < 0 || parsed > 2_147_483_647) {
      setDurationDraft(beat.duration_sec == null ? '' : String(beat.duration_sec));
      return;
    }
    if (parsed !== beat.duration_sec) onUpdate({ duration_sec: parsed });
  }, [durationDraft, beat.duration_sec, onUpdate]);

  const handleDeleteClick = useCallback(() => {
    if (confirmingDelete) {
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
      confirmTimerRef.current = null;
      setConfirmingDelete(false);
      onDelete();
      return;
    }
    setConfirmingDelete(true);
    confirmTimerRef.current = setTimeout(() => {
      confirmTimerRef.current = null;
      setConfirmingDelete(false);
    }, CONFIRM_WINDOW_MS);
  }, [confirmingDelete, onDelete]);

  const linkedIds = beat.scene_ids;
  const durationLabel = formatBeatDuration(beat.duration_sec);

  return (
    <div
      className={`mh-beat-card${isDragging ? ' dragging' : ''}`}
      data-testid="beat-card"
      data-beat-id={beat.id}
    >
      {beat.color && (
        <span
          className="mh-beat-color-bar"
          data-testid="beat-color-bar"
          aria-hidden="true"
          style={{ background: beat.color }}
        />
      )}
      <div className="mh-beat-card-head">
        <span
          className="mh-beat-drag-handle"
          data-testid="beat-drag-handle"
          aria-label={t('editor.beatDragHandle')}
          {...handleProps}
        >
          ⋮⋮
        </span>
        <span className="mh-beat-num" aria-hidden="true">
          {index + 1}
        </span>
        <input
          className="mh-beat-title"
          value={titleDraft}
          aria-label={t('editor.beatTitle')}
          placeholder={t('editor.beatTitlePlaceholder')}
          onChange={(e) => setTitleDraft(e.target.value)}
          onBlur={commitTitle}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              e.currentTarget.blur();
            } else if (e.key === 'Escape') {
              setTitleDraft(beat.title);
              e.currentTarget.blur();
            }
          }}
        />
        {durationLabel && (
          <span className="mh-beat-duration-chip" data-testid="beat-duration-chip">
            {durationLabel}
          </span>
        )}
        <button
          type="button"
          className="mh-beat-notes-toggle"
          aria-expanded={notesOpen}
          onClick={() => setNotesOpen((v) => !v)}
        >
          {t('editor.beatNotes')}
        </button>
        <button
          type="button"
          className={`mh-beat-delete${confirmingDelete ? ' confirming' : ''}`}
          data-testid="beat-delete"
          onClick={handleDeleteClick}
        >
          {confirmingDelete ? t('editor.beatConfirmDelete') : '×'}
        </button>
      </div>

      {notesOpen && (
        <>
          <textarea
            className="mh-beat-summary"
            value={summaryDraft}
            aria-label={t('editor.beatSummary')}
            placeholder={t('editor.beatSummaryPlaceholder')}
            rows={2}
            onChange={(e) => setSummaryDraft(e.target.value)}
            onBlur={commitSummary}
          />
          <div className="mh-beat-arrange">
            <label className="mh-beat-duration-field">
              <span className="mh-beat-field-label">{t('editor.beatDurationLabel')}</span>
              <input
                type="number"
                min={0}
                className="mh-beat-duration-input"
                value={durationDraft}
                aria-label={t('editor.beatDuration')}
                placeholder={t('editor.beatDurationPlaceholder')}
                onChange={(e) => setDurationDraft(e.target.value)}
                onBlur={commitDuration}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    e.currentTarget.blur();
                  }
                }}
              />
            </label>
            <div
              className="mh-beat-color-swatches"
              role="group"
              aria-label={t('editor.beatColor')}
            >
              {BEAT_COLORS.map((hex) => (
                <button
                  key={hex}
                  type="button"
                  className={`mh-beat-color-swatch${beat.color === hex ? ' selected' : ''}`}
                  data-testid="beat-color-swatch"
                  style={{ background: hex }}
                  aria-label={hex}
                  aria-pressed={beat.color === hex}
                  onClick={() => onUpdate({ color: beat.color === hex ? null : hex })}
                />
              ))}
              {beat.color && (
                <button
                  type="button"
                  className="mh-beat-color-clear"
                  aria-label={t('editor.beatColorClear')}
                  onClick={() => onUpdate({ color: null })}
                >
                  ×
                </button>
              )}
            </div>
          </div>
        </>
      )}

      <BeatSceneLinks
        linkedIds={linkedIds}
        scenes={scenes}
        onOpenScene={onOpenScene}
        onChange={(scene_ids) => onUpdate({ scene_ids })}
      />
    </div>
  );
}
