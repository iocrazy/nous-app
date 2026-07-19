/**
 * BeatsTemplateWizard — the "Apply template" flow (M3, custom templates M3.5).
 *
 * Three steps: pick a template (the built-in Save the Cat / Five Beats /
 * Kishōtenketsu, plus the user's own saved templates) → choose a target total
 * length → (only when beats already exist) append vs replace → generate.
 * Generation is pure: a built-in template maps its i18n percentage anchors via
 * `instantiateTemplate`; a custom template replays its stored anchors verbatim
 * via `instantiateCustomAnchors`. The wizard only produces the rows + the intent;
 * BeatsView does the REST writes and the target PATCH. Custom cards carry an
 * inline (two-click) delete affordance.
 */
import { useCallback, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { BeatInput } from '../sceneService';
import { chooseTickUnit, snapGranularity } from './arrangementGeometry';
import type { CustomTemplate } from './beatTemplateService';
import { DURATION_PRESETS, formatTargetLength, parseDurationInput } from './beatsDuration';
import {
  BEAT_TEMPLATES,
  instantiateCustomAnchors,
  instantiateTemplate,
} from './templates';

type ApplyMode = 'append' | 'replace';
type Step = 'template' | 'length' | 'mode';

/** A built-in or custom template, unified for the card list + generation. */
interface WizardTemplate {
  /** Selection id: a built-in key, or `custom:<snowflake>`. */
  id: string;
  name: string;
  /** One-line blurb (built-in only); custom templates show just the count. */
  desc: string | null;
  beatCount: number;
  isCustom: boolean;
  /** Owning row id for the delete affordance (custom only). */
  customId?: string;
  instantiate: (totalSec: number, granularity: number) => BeatInput[];
}

interface Props {
  initialTemplateKey?: string | null;
  currentTargetSec: number | null;
  hasExistingBeats: boolean;
  customTemplates?: CustomTemplate[];
  onApply: (beats: BeatInput[], mode: ApplyMode, targetSec: number) => void;
  onDeleteCustom?: (id: string) => void;
  onClose: () => void;
}

/** Default target when the script has none yet: 3 minutes (short-form). */
const DEFAULT_TARGET_SEC = 180;

export function BeatsTemplateWizard({
  initialTemplateKey,
  currentTargetSec,
  hasExistingBeats,
  customTemplates = [],
  onApply,
  onDeleteCustom,
  onClose,
}: Props) {
  const { t } = useTranslation();

  const [step, setStep] = useState<Step>('template');
  const [selectedId, setSelectedId] = useState<string | null>(initialTemplateKey ?? null);
  const [lengthSec, setLengthSec] = useState<number>(currentTargetSec ?? DEFAULT_TARGET_SEC);
  const [custom, setCustom] = useState('');
  const [mode, setMode] = useState<ApplyMode>('append');
  // Inline two-click delete confirm, keyed by the custom template id.
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const templates = useMemo<WizardTemplate[]>(
    () => [
      ...BEAT_TEMPLATES.map((tpl) => ({
        id: tpl.key,
        name: t(tpl.nameKey),
        desc: t(tpl.descKey),
        beatCount: tpl.beats.length,
        isCustom: false,
        instantiate: (totalSec: number, granularity: number) =>
          instantiateTemplate(tpl, totalSec, granularity, t),
      })),
      ...customTemplates.map((ct) => ({
        id: `custom:${ct.id}`,
        name: ct.name,
        desc: null,
        beatCount: ct.anchors.length,
        isCustom: true,
        customId: ct.id,
        instantiate: (totalSec: number, granularity: number) =>
          instantiateCustomAnchors(ct.anchors, totalSec, granularity),
      })),
    ],
    [customTemplates, t],
  );

  const template = selectedId ? templates.find((x) => x.id === selectedId) : undefined;

  const applyCustom = useCallback(() => {
    const parsed = parseDurationInput(custom);
    if (parsed != null) {
      setLengthSec(parsed);
      setCustom('');
    }
  }, [custom]);

  const appliedRef = useRef(false);
  const generate = useCallback(() => {
    if (!template) return;
    // One shot: a double-click must not double-create (or double-delete in
    // replace mode) — the modal unmounts async after onClose.
    if (appliedRef.current) return;
    appliedRef.current = true;
    const granularity = snapGranularity(chooseTickUnit(lengthSec));
    const beats = template.instantiate(lengthSec, granularity);
    onApply(beats, hasExistingBeats ? mode : 'append', lengthSec);
    onClose();
  }, [template, lengthSec, mode, hasExistingBeats, onApply, onClose]);

  const goNext = useCallback(() => {
    if (step === 'template' && template) setStep('length');
    else if (step === 'length') {
      if (hasExistingBeats) setStep('mode');
      else generate();
    } else if (step === 'mode') generate();
  }, [step, template, hasExistingBeats, generate]);

  const handleDelete = useCallback(
    (tpl: WizardTemplate) => {
      if (!tpl.customId) return;
      if (pendingDelete !== tpl.id) {
        setPendingDelete(tpl.id);
        return;
      }
      onDeleteCustom?.(tpl.customId);
      setPendingDelete(null);
      if (selectedId === tpl.id) setSelectedId(null);
    },
    [pendingDelete, onDeleteCustom, selectedId],
  );

  const previewCount = template?.beatCount ?? 0;

  return (
    <div
      className="mh-arr-modal-overlay"
      data-testid="beats-template-wizard"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="mh-arr-modal mh-tpl-modal" role="dialog" aria-modal="true" aria-label={t('editor.beatTplWizardTitle')}>
        <div className="mh-arr-modal-head">
          <span className="mh-arr-modal-title">{t('editor.beatTplWizardTitle')}</span>
          <button
            type="button"
            className="mh-arr-modal-x"
            data-testid="beats-template-cancel"
            aria-label={t('common.cancel')}
            onClick={onClose}
          >
            ×
          </button>
        </div>

        {step === 'template' && (
          <div className="mh-tpl-cards" data-testid="beats-template-cards">
            {templates.map((tpl) => (
              <div key={tpl.id} className="mh-tpl-card-wrap">
                <button
                  type="button"
                  className={`mh-tpl-card${selectedId === tpl.id ? ' selected' : ''}`}
                  data-testid="beats-template-card"
                  data-key={tpl.id}
                  data-custom={tpl.isCustom ? 'true' : undefined}
                  aria-pressed={selectedId === tpl.id}
                  onClick={() => setSelectedId(tpl.id)}
                >
                  <span className="mh-tpl-card-name">{tpl.name}</span>
                  {tpl.desc && <span className="mh-tpl-card-desc">{tpl.desc}</span>}
                  <span className="mh-tpl-card-count">
                    {t('editor.beatTplBeatCount', { count: tpl.beatCount })}
                  </span>
                </button>
                {tpl.isCustom && onDeleteCustom && (
                  <button
                    type="button"
                    className={`mh-tpl-card-delete${pendingDelete === tpl.id ? ' confirm' : ''}`}
                    data-testid={
                      pendingDelete === tpl.id
                        ? 'beats-template-custom-delete-confirm'
                        : 'beats-template-custom-delete'
                    }
                    data-id={tpl.customId}
                    aria-label={
                      pendingDelete === tpl.id
                        ? t('editor.beatTplCustomDeleteConfirm')
                        : t('editor.beatTplCustomDelete')
                    }
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDelete(tpl);
                    }}
                  >
                    {pendingDelete === tpl.id
                      ? t('editor.beatTplCustomDeleteConfirm')
                      : '×'}
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        {step === 'length' && (
          <div className="mh-arr-field" data-testid="beats-template-length">
            <span className="mh-arr-field-label">{t('editor.beatTplTargetLength')}</span>
            <div className="mh-arr-len-presets">
              {DURATION_PRESETS.map((sec) => (
                <button
                  key={sec}
                  type="button"
                  className={`mh-arr-len-preset${lengthSec === sec ? ' selected' : ''}`}
                  data-testid="beats-template-preset"
                  data-sec={sec}
                  onClick={() => setLengthSec(sec)}
                >
                  {formatTargetLength(sec)}
                </button>
              ))}
            </div>
            <div className="mh-arr-len-custom">
              <input
                className="mh-arr-input narrow"
                data-testid="beats-template-custom"
                value={custom}
                placeholder={t('editor.arrLengthCustomPlaceholder')}
                aria-label={t('editor.arrLengthCustom')}
                onChange={(e) => setCustom(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    applyCustom();
                  }
                }}
              />
              <button
                type="button"
                className="mh-arr-modal-btn ghost"
                data-testid="beats-template-custom-apply"
                disabled={parseDurationInput(custom) == null}
                onClick={applyCustom}
              >
                {t('editor.arrLengthSet')}
              </button>
            </div>
            <p className="mh-tpl-hint">
              {t('editor.beatTplLengthHint', {
                count: previewCount,
                length: formatTargetLength(lengthSec),
              })}
            </p>
          </div>
        )}

        {step === 'mode' && (
          <div className="mh-arr-field" data-testid="beats-template-mode">
            <span className="mh-arr-field-label">{t('editor.beatTplModeLabel')}</span>
            <label className={`mh-tpl-mode${mode === 'append' ? ' selected' : ''}`}>
              <input
                type="radio"
                name="tpl-mode"
                data-testid="beats-template-mode-append"
                checked={mode === 'append'}
                onChange={() => setMode('append')}
              />
              <span className="mh-tpl-mode-body">
                <span className="mh-tpl-mode-name">{t('editor.beatTplModeAppend')}</span>
                <span className="mh-tpl-mode-desc">{t('editor.beatTplModeAppendDesc')}</span>
              </span>
            </label>
            <label className={`mh-tpl-mode danger${mode === 'replace' ? ' selected' : ''}`}>
              <input
                type="radio"
                name="tpl-mode"
                data-testid="beats-template-mode-replace"
                checked={mode === 'replace'}
                onChange={() => setMode('replace')}
              />
              <span className="mh-tpl-mode-body">
                <span className="mh-tpl-mode-name">{t('editor.beatTplModeReplace')}</span>
                <span className="mh-tpl-mode-desc">{t('editor.beatTplModeReplaceDesc')}</span>
              </span>
            </label>
          </div>
        )}

        <div className="mh-arr-modal-foot">
          {step !== 'template' && (
            <button
              type="button"
              className="mh-arr-modal-btn ghost"
              data-testid="beats-template-back"
              onClick={() => setStep(step === 'mode' ? 'length' : 'template')}
            >
              {t('editor.beatTplBack')}
            </button>
          )}
          <button
            type="button"
            className="mh-arr-modal-btn primary"
            data-testid="beats-template-next"
            disabled={step === 'template' && !template}
            onClick={goNext}
          >
            {step === 'mode' || (step === 'length' && !hasExistingBeats)
              ? t('editor.beatTplGenerate')
              : t('editor.beatTplNext')}
          </button>
        </div>
      </div>
    </div>
  );
}
