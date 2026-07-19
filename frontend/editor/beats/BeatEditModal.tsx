/**
 * BeatEditModal — the "Edit Beat" dialog opened from an Arrangement card (M2).
 *
 * Edits Title / Summary / Duration / color-strip / linked scenes as local
 * drafts and commits the CHANGED fields in a single `onSave` (BeatInput) on
 * Save — mirroring the exclude_unset PATCH semantics (untouched fields are
 * omitted; an emptied Summary/Duration commits null to clear). A centred fixed
 * overlay styled with the editor-shell ink tokens so it reads as part of the
 *墨色 chrome, not the app-level Tailwind modals.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { Beat, BeatInput } from '../sceneService';
import type { SceneDoc } from '../types';
import { BEAT_COLORS } from './beatColors';
import { BeatSceneLinks } from './BeatSceneLinks';

interface Props {
  beat: Beat;
  scenes: SceneDoc[];
  onSave: (data: BeatInput) => void;
  onClose: () => void;
  onOpenScene?: (sceneId: string) => void;
}

const PG_INT_MAX = 2_147_483_647;

export function BeatEditModal({ beat, scenes, onSave, onClose, onOpenScene }: Props) {
  const { t } = useTranslation();

  const [title, setTitle] = useState(beat.title);
  const [summary, setSummary] = useState(beat.summary ?? '');
  const [duration, setDuration] = useState(
    beat.duration_sec == null ? '' : String(beat.duration_sec),
  );
  const [color, setColor] = useState<string | null>(beat.color);
  const [sceneIds, setSceneIds] = useState<string[]>(beat.scene_ids);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const arraysEqual = (a: string[], b: string[]) =>
    a.length === b.length && a.every((v, i) => v === b[i]);

  const handleSave = useCallback(() => {
    const patch: BeatInput = {};
    const nextTitle = title.trim();
    if (nextTitle && nextTitle !== beat.title) patch.title = nextTitle;

    const nextSummary = summary.trim();
    if (nextSummary !== (beat.summary ?? '')) patch.summary = nextSummary || null;

    const rawDuration = duration.trim();
    if (rawDuration === '') {
      if (beat.duration_sec != null) patch.duration_sec = null;
    } else {
      const parsed = Number.parseInt(rawDuration, 10);
      if (!Number.isNaN(parsed) && parsed >= 0 && parsed <= PG_INT_MAX && parsed !== beat.duration_sec) {
        patch.duration_sec = parsed;
      }
    }

    if (color !== beat.color) patch.color = color;
    if (!arraysEqual(sceneIds, beat.scene_ids)) patch.scene_ids = sceneIds;

    if (Object.keys(patch).length > 0) onSave(patch);
    onClose();
  }, [title, summary, duration, color, sceneIds, beat, onSave, onClose]);

  return (
    <div
      className="mh-arr-modal-overlay"
      data-testid="beat-edit-modal"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="mh-arr-modal"
        role="dialog"
        aria-modal="true"
        aria-label={t('editor.beatEditTitle')}
      >
        <div className="mh-arr-modal-head">
          <span className="mh-arr-modal-title">{t('editor.beatEditTitle')}</span>
          <button
            type="button"
            className="mh-arr-modal-x"
            data-testid="beat-edit-cancel"
            aria-label={t('common.cancel')}
            onClick={onClose}
          >
            ×
          </button>
        </div>

        <label className="mh-arr-field">
          <span className="mh-arr-field-label">{t('editor.beatTitle')}</span>
          <input
            className="mh-arr-input"
            data-testid="beat-edit-title"
            value={title}
            placeholder={t('editor.beatTitlePlaceholder')}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>

        <label className="mh-arr-field">
          <span className="mh-arr-field-label">{t('editor.beatSummary')}</span>
          <textarea
            className="mh-arr-textarea"
            data-testid="beat-edit-summary"
            value={summary}
            rows={3}
            placeholder={t('editor.beatSummaryPlaceholder')}
            onChange={(e) => setSummary(e.target.value)}
          />
        </label>

        <label className="mh-arr-field">
          <span className="mh-arr-field-label">{t('editor.beatDurationLabel')}</span>
          <input
            type="number"
            min={0}
            className="mh-arr-input narrow"
            data-testid="beat-edit-duration"
            value={duration}
            placeholder={t('editor.beatDurationPlaceholder')}
            onChange={(e) => setDuration(e.target.value)}
          />
        </label>

        <div className="mh-arr-field">
          <span className="mh-arr-field-label">{t('editor.beatColor')}</span>
          <div className="mh-beat-color-swatches" role="group" aria-label={t('editor.beatColor')}>
            {BEAT_COLORS.map((hex) => (
              <button
                key={hex}
                type="button"
                className={`mh-beat-color-swatch${color === hex ? ' selected' : ''}`}
                data-testid="beat-edit-color-swatch"
                style={{ background: hex }}
                aria-label={hex}
                aria-pressed={color === hex}
                onClick={() => setColor(color === hex ? null : hex)}
              />
            ))}
            {color && (
              <button
                type="button"
                className="mh-beat-color-clear"
                aria-label={t('editor.beatColorClear')}
                onClick={() => setColor(null)}
              >
                ×
              </button>
            )}
          </div>
        </div>

        <div className="mh-arr-field">
          <span className="mh-arr-field-label">{t('editor.beatLinkScene')}</span>
          <BeatSceneLinks
            linkedIds={sceneIds}
            scenes={scenes}
            onChange={setSceneIds}
            onOpenScene={onOpenScene}
          />
        </div>

        <div className="mh-arr-modal-foot">
          <button
            type="button"
            className="mh-arr-modal-btn ghost"
            onClick={onClose}
          >
            {t('common.cancel')}
          </button>
          <button
            type="button"
            className="mh-arr-modal-btn primary"
            data-testid="beat-edit-save"
            onClick={handleSave}
          >
            {t('common.save')}
          </button>
        </div>
      </div>
    </div>
  );
}
