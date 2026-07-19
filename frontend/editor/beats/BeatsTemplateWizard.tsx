/**
 * BeatsTemplateWizard — the "Apply methodology template" flow (M3).
 *
 * Three steps: pick a template (Save the Cat / Five Beats / Kishōtenketsu) →
 * choose a target total length → (only when beats already exist) append vs
 * replace → generate. Generation is pure: `instantiateTemplate` turns the
 * template's percentage anchors into `BeatInput` rows against the chosen total,
 * on the grid the arrangement drag would snap to. The wizard only produces the
 * rows + the intent; BeatsView does the REST writes and the target PATCH.
 */
import { useCallback, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { BeatInput } from '../sceneService';
import { chooseTickUnit, snapGranularity } from './arrangementGeometry';
import { DURATION_PRESETS, formatTargetLength, parseDurationInput } from './beatsDuration';
import { BEAT_TEMPLATES, getTemplate, instantiateTemplate } from './templates';

type ApplyMode = 'append' | 'replace';
type Step = 'template' | 'length' | 'mode';

interface Props {
  initialTemplateKey?: string | null;
  currentTargetSec: number | null;
  hasExistingBeats: boolean;
  onApply: (beats: BeatInput[], mode: ApplyMode, targetSec: number) => void;
  onClose: () => void;
}

/** Default target when the script has none yet: 3 minutes (short-form). */
const DEFAULT_TARGET_SEC = 180;

export function BeatsTemplateWizard({
  initialTemplateKey,
  currentTargetSec,
  hasExistingBeats,
  onApply,
  onClose,
}: Props) {
  const { t } = useTranslation();

  const [step, setStep] = useState<Step>('template');
  const [templateKey, setTemplateKey] = useState<string | null>(initialTemplateKey ?? null);
  const [lengthSec, setLengthSec] = useState<number>(currentTargetSec ?? DEFAULT_TARGET_SEC);
  const [custom, setCustom] = useState('');
  const [mode, setMode] = useState<ApplyMode>('append');

  const template = templateKey ? getTemplate(templateKey) : undefined;

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
    const beats = instantiateTemplate(template, lengthSec, granularity, t);
    onApply(beats, hasExistingBeats ? mode : 'append', lengthSec);
    onClose();
  }, [template, lengthSec, mode, hasExistingBeats, onApply, onClose, t]);

  const goNext = useCallback(() => {
    if (step === 'template' && template) setStep('length');
    else if (step === 'length') {
      if (hasExistingBeats) setStep('mode');
      else generate();
    } else if (step === 'mode') generate();
  }, [step, template, hasExistingBeats, generate]);

  const previewCount = useMemo(() => template?.beats.length ?? 0, [template]);

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
            {BEAT_TEMPLATES.map((tpl) => (
              <button
                key={tpl.key}
                type="button"
                className={`mh-tpl-card${templateKey === tpl.key ? ' selected' : ''}`}
                data-testid="beats-template-card"
                data-key={tpl.key}
                aria-pressed={templateKey === tpl.key}
                onClick={() => setTemplateKey(tpl.key)}
              >
                <span className="mh-tpl-card-name">{t(tpl.nameKey)}</span>
                <span className="mh-tpl-card-desc">{t(tpl.descKey)}</span>
                <span className="mh-tpl-card-count">
                  {t('editor.beatTplBeatCount', { count: tpl.beats.length })}
                </span>
              </button>
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
                  if (e.key === 'Enter') {
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
