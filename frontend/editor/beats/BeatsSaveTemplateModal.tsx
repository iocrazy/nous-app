/**
 * BeatsSaveTemplateModal — name-and-save dialog for turning the current
 * arrangement into a reusable custom template (M3.5). It only collects the name;
 * ArrangementView owns the geometry (it reverse-computes the percentage anchors
 * from the arranged beats before calling onSave). The Save button is disabled
 * until a non-blank name is entered; Enter submits (guarded against IME
 * composition — Safari fires a bare Enter mid-composition).
 */
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';

interface Props {
  onSave: (name: string) => void;
  onClose: () => void;
}

export function BeatsSaveTemplateModal({ onSave, onClose }: Props) {
  const { t } = useTranslation();
  const [name, setName] = useState('');

  const trimmed = name.trim();
  const save = useCallback(() => {
    if (!trimmed) return;
    onSave(trimmed);
  }, [trimmed, onSave]);

  return (
    <div
      className="mh-arr-modal-overlay"
      data-testid="beats-save-template-modal"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="mh-arr-modal mh-tpl-save-modal"
        role="dialog"
        aria-modal="true"
        aria-label={t('editor.beatSaveTemplateTitle')}
      >
        <div className="mh-arr-modal-head">
          <span className="mh-arr-modal-title">{t('editor.beatSaveTemplateTitle')}</span>
          <button
            type="button"
            className="mh-arr-modal-x"
            data-testid="beats-save-template-cancel"
            aria-label={t('common.cancel')}
            onClick={onClose}
          >
            ×
          </button>
        </div>

        <div className="mh-arr-field">
          <span className="mh-arr-field-label">{t('editor.beatSaveTemplateNameLabel')}</span>
          <input
            className="mh-arr-input"
            data-testid="beats-save-template-name"
            value={name}
            autoFocus
            maxLength={100}
            placeholder={t('editor.beatSaveTemplateNamePlaceholder')}
            aria-label={t('editor.beatSaveTemplateNameLabel')}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                e.preventDefault();
                save();
              }
            }}
          />
        </div>

        <div className="mh-arr-modal-foot">
          <button
            type="button"
            className="mh-arr-modal-btn ghost"
            data-testid="beats-save-template-close"
            onClick={onClose}
          >
            {t('editor.beatTplBack')}
          </button>
          <button
            type="button"
            className="mh-arr-modal-btn primary"
            data-testid="beats-save-template-save"
            disabled={!trimmed}
            onClick={save}
          >
            {t('editor.beatSaveTemplateSave')}
          </button>
        </div>
      </div>
    </div>
  );
}
