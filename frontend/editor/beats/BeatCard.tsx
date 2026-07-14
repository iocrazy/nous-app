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
import { UiSelect } from '../../components/ui';

/** `INT · Location · TIME`, empty parts dropped (mirrors OutlineView's head). */
function sceneLabel(scene: SceneDoc, fallback: string): string {
  const label = [scene.heading_int_ext, scene.location_text, scene.time_of_day]
    .map((part) => (part ?? '').trim())
    .filter((part) => part.length > 0)
    .join(' · ');
  return label || fallback;
}

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

  const [notesOpen, setNotesOpen] = useState(!!beat.summary);
  const [summaryDraft, setSummaryDraft] = useState(beat.summary ?? '');
  useEffect(() => setSummaryDraft(beat.summary ?? ''), [beat.summary]);

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
  const unlinkedScenes = scenes.filter((s) => !linkedIds.includes(String(s.id)));

  return (
    <div
      className={`mh-beat-card${isDragging ? ' dragging' : ''}`}
      data-testid="beat-card"
      data-beat-id={beat.id}
    >
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
        <textarea
          className="mh-beat-summary"
          value={summaryDraft}
          aria-label={t('editor.beatSummary')}
          placeholder={t('editor.beatSummaryPlaceholder')}
          rows={2}
          onChange={(e) => setSummaryDraft(e.target.value)}
          onBlur={commitSummary}
        />
      )}

      <div className="mh-beat-scenes">
        {linkedIds.map((sceneId) => {
          const scene = scenes.find((s) => String(s.id) === String(sceneId));
          const label = scene
            ? sceneLabel(scene, t('editor.untitledScene'))
            : t('editor.beatSceneMissing');
          return (
            <span className="mh-beat-chip" key={sceneId} data-testid="beat-scene-chip">
              <button
                type="button"
                className="mh-beat-chip-label"
                disabled={!scene}
                onClick={() => scene && onOpenScene(String(sceneId))}
              >
                {label}
              </button>
              <button
                type="button"
                className="mh-beat-chip-x"
                aria-label={t('editor.beatUnlinkScene')}
                onClick={() =>
                  onUpdate({ scene_ids: linkedIds.filter((id) => id !== sceneId) })
                }
              >
                ×
              </button>
            </span>
          );
        })}
        {unlinkedScenes.length > 0 && (
          <UiSelect
            triggerClassName="mh-beat-link-select"
            data-testid="beat-link-scene"
            aria-label={t('editor.beatLinkScene')}
            value=""
            onChange={(e) => {
              const id = e.target.value;
              if (id) onUpdate({ scene_ids: [...linkedIds, id] });
            }}
          >
            <option value="">{t('editor.beatLinkScene')}</option>
            {unlinkedScenes.map((s) => (
              <option key={s.id} value={String(s.id)}>
                {sceneLabel(s, t('editor.untitledScene'))}
              </option>
            ))}
          </UiSelect>
        )}
      </div>
    </div>
  );
}
