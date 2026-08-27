// components/Distribution/CoverStudio/CoverStudioOverlay.tsx
//
// Cover Studio — the v4 layout: a centred dialog over the publish page with
// two cover tabs (vertical 3:4 / horizontal 4:3) and three columns.
//
//   left    what goes INTO the model: the prompt box, the reference pool, the
//           generate button, and the rounds already generated (click to go back)
//   stage   what the user is looking at right now: the video frame with a
//           crop guide, then the 2x2 drafts, then the final cover
//   side    the cover as it will be seen (preview), model + style, and the
//           actions for the current step
//
// Both tabs go through the model (4:3 has its own composition sentence in
// cover_prompt.py), and both can also take a dragged frame crop or an upload.
// The style list is GET /covers/styles — the built-in one plus every
// `category: cover` skill the user can see.
//
// Every generated round is kept (`rounds`) so the user can go back to an
// earlier grid or final without paying for it again.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Sparkles, X } from 'lucide-react';

import { getApiUrl } from '../../../utils/apiConfig';
import {
  listGenerationModels,
  type GenerationModel,
} from '../../../features/canvas-core/services/canvasGenerationService';
import {
  BUILTIN_COVER_STYLE,
  awaitCoverGeneration,
  generateCoverDrafts,
  listCoverStyles,
  refineCoverDraft,
  type CoverAspect,
  type CoverStyle,
} from '../../../services/coverStudioService';
import { promoteGeneration } from '../../../services/generatedMediaService';
import {
  markCoverTemplatesUsed,
  resolveCoverTemplateReference,
  saveGeneratedCoverAsTemplate,
  type CoverTemplate,
} from '../../../services/coverTemplateService';
import { selectCoverFrame } from '../../../services/distributionService';
import { uploadResource } from '../../../services/resourceService';
import { importCanvasMedia } from '../../../features/canvas-core/smart/mediaImport';
import type { LibraryVideo } from '../../../types';
import type { CoverPair } from '../CoverPicker';
import {
  addReference,
  personReference,
  removeReference,
  sourceUrls,
  templateIds,
  type CoverReference,
} from './coverReferences';
import { CoverReferencePool } from './CoverReferencePool';
import { CoverTemplateGrid } from './CoverTemplateGrid';
import { CoverFrameGrabber, type CropFocus, type GrabbedFrame } from './CoverFrameGrabber';
import { CoverDrafts, type CoverStage } from './CoverDrafts';
import './cover-studio.css';

type Orientation = 'vertical' | 'horizontal';

/** One generation round: the grid, and the final it produced, if any. */
interface Round {
  id: number;
  /** Which cover this round was for; decides the slot "Done" fills. */
  aspect: CoverAspect;
  gridUrl: string;
  prompt: string;
  selected: number | null;
  finalUrl?: string;
  finalGenId?: string;
}

interface Props {
  open: boolean;
  scopeId: string;
  /** The videos picked on the publish page. */
  sources: LibraryVideo[];
  /** The topic — the publish title, which is what the cover is about. */
  topic: string;
  onClose: () => void;
  /**
   * The cover as a RESOURCE id, in the slot the active tab was setting. One
   * slot per apply: the vertical tab gives `{ vertical }` (an AI cover or a
   * 3:4 crop), the horizontal tab `{ horizontal }` (a 4:3 crop or upload).
   */
  onApply: (patch: CoverPair) => void;
}

export function CoverStudioOverlay({
  open,
  scopeId,
  sources,
  topic,
  onClose,
  onApply,
}: Props): React.JSX.Element | null {
  const { t } = useTranslation();

  const [orientation, setOrientation] = useState<Orientation>('vertical');
  const [instructions, setInstructions] = useState('');
  const [refs, setRefs] = useState<CoverReference[]>([]);
  const [refusal, setRefusal] = useState<'full' | 'duplicate' | null>(null);
  const [grabAsPerson, setGrabAsPerson] = useState(false);
  const [allowSmallLabels, setAllowSmallLabels] = useState(false);
  const [models, setModels] = useState<GenerationModel[]>([]);
  const [model, setModel] = useState('');
  const [styles, setStyles] = useState<CoverStyle[]>([]);
  const [styleSlug, setStyleSlug] = useState(BUILTIN_COVER_STYLE);
  const [poolError, setPoolError] = useState<string | null>(null);

  const [stage, setStage] = useState<CoverStage>('idle');
  const [rounds, setRounds] = useState<Round[]>([]);
  const [activeRound, setActiveRound] = useState<number | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [savedAsTemplate, setSavedAsTemplate] = useState(false);
  // The final cover's resource id once it has been promoted — by "Done" OR by
  // "Save as template" — so the other button never promotes the same picture
  // a second time.
  const [promotedId, setPromotedId] = useState<string | undefined>(undefined);
  // Which template tile is being turned into a reference right now (the
  // generated-media import is a round-trip), and why the last one failed.
  const [templateBusyId, setTemplateBusyId] = useState<string | null>(null);
  const [templateError, setTemplateError] = useState<string | null>(null);
  // The last frame grabbed, so the preview has something to show before any
  // generation has happened.
  const [lastFrameUrl, setLastFrameUrl] = useState<string | undefined>(undefined);

  const aspect: CoverAspect = orientation === 'vertical' ? '3:4' : '4:3';
  const round = activeRound === null ? null : rounds.find((r) => r.id === activeRound) ?? null;
  const style = styles.find((st) => st.slug === styleSlug);
  // Unknown until the list arrives; the built-in style needs a person, so
  // that is the safe default — blocking is recoverable, a faceless draft is
  // a wasted generation.
  const requiresPerson = style ? style.requires_person : true;
  const gridUrl = round?.gridUrl;
  const prompt = round?.prompt;
  const selected = round?.selected ?? null;
  const finalUrl = round?.finalUrl;
  const finalGenId = round?.finalGenId;

  useEffect(() => {
    if (!open) return;
    let alive = true;
    void listGenerationModels()
      .then((all) => {
        if (!alive) return;
        setModels(all.filter((m) => m.type === 'image'));
      })
      .catch((err) => {
        // Degraded, not broken: '' lets the catalog pick its default.
        console.error('[CoverStudio] listGenerationModels failed:', err);
      });
    void listCoverStyles()
      .then((all) => {
        if (alive) setStyles(all);
      })
      .catch((err) => {
        // The built-in style still works without the list — the select just
        // shows that one entry.
        console.error('[CoverStudio] listCoverStyles failed:', err);
      });
    return () => {
      alive = false;
    };
  }, [open]);

  const person = personReference(refs);
  const frameTimestamps = useMemo(
    () =>
      refs
        .filter((r) => typeof r.timestampSeconds === 'number')
        .map((r) => r.timestampSeconds as number),
    [refs],
  );

  const patchRound = useCallback((id: number, patch: Partial<Round>) => {
    setRounds((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }, []);

  const addRef = useCallback((next: CoverReference) => {
    setRefs((prev) => {
      const result = addReference(prev, next);
      // `=== false`, not `!result.ok`: this repo does not enable strict, and
      // without strictNullChecks `null` inhabits every type, so a truthiness
      // check cannot exclude the `ok: true` member in its else branch — the
      // discriminant only narrows through a literal comparison here.
      if (result.ok === false) setRefusal(result.reason);
      else setRefusal(null);
      return result.refs;
    });
  }, []);

  const onGrabbed = useCallback(
    (frame: GrabbedFrame) => {
      setLastFrameUrl(frame.url);
      addRef({
        kind: grabAsPerson ? 'person' : 'frame',
        genId: frame.generatedMediaId,
        url: frame.url,
        timestampSeconds: frame.timestampSeconds,
      });
      // One-shot: the next grab is an ordinary frame again unless re-armed.
      setGrabAsPerson(false);
    },
    [addRef, grabAsPerson],
  );

  const uploadReference = useCallback(
    async (file: File) => {
      setPoolError(null);
      try {
        const ref = await importCanvasMedia(file, null, null);
        if (ref.kind !== 'image' || !ref.id) {
          throw new Error('uploaded reference is not an image');
        }
        addRef({ kind: 'template', genId: ref.id, url: ref.url, label: file.name });
      } catch (err) {
        console.error('[CoverStudio] upload reference failed:', err);
        setPoolError(
          t(
            'distribution.coverStudio.refUploadFailed',
            'That picture could not be added as a reference. Try again.',
          ),
        );
      }
    },
    [addRef, t],
  );

  const canGenerate =
    stage !== 'drafting' &&
    stage !== 'refining' &&
    !!topic.trim() &&
    (!requiresPerson || !!person);

  const runDrafts = useCallback(async () => {
    if (!canGenerate) return;
    setStage('drafting');
    setRunError(null);
    setSavedAsTemplate(false);
    setPromotedId(undefined);
    setActiveRound(null);
    try {
      const dispatched = await generateCoverDrafts({
        topic: topic.trim(),
        sourceUrls: sourceUrls(refs),
        model,
        allowSmallLabels,
        instructions: instructions.trim(),
        aspect,
        style: styleSlug,
      });
      // Usage ticks AFTER a successful dispatch, once per run — and it can
      // never fail the generation (the service swallows its own errors).
      void markCoverTemplatesUsed(templateIds(refs));
      const outcome = await awaitCoverGeneration(dispatched.task_id);
      if (!outcome.ok || !outcome.url) {
        setRunError(outcome.error ?? 'generation failed');
        setStage('idle');
        return;
      }
      const id = Date.now();
      setRounds((prev) => [
        ...prev,
        { id, aspect, gridUrl: outcome.url as string, prompt: dispatched.prompt, selected: null },
      ]);
      setActiveRound(id);
      setStage('picking');
    } catch (err) {
      console.error('[CoverStudio] stage 1 failed:', err);
      setRunError((err as Error).message);
      setStage('idle');
    }
  }, [canGenerate, topic, refs, model, allowSmallLabels, instructions, aspect, styleSlug]);

  const runRefine = useCallback(async () => {
    if (!round || !gridUrl || selected === null) return;
    const id = round.id;
    setStage('refining');
    setRunError(null);
    try {
      const dispatched = await refineCoverDraft({
        topic: topic.trim(),
        // ★ Stage 2's references are the person + the GRID. The prompt says
        // "the supplied 2x2 grid image" and "the supplied character image" —
        // those two, in that speech, are what it needs. Re-sending the frames
        // and templates would spend reference slots on pictures the prompt
        // never mentions.
        sourceUrls: [...(person ? [person.url] : []), gridUrl],
        model,
        allowSmallLabels,
        instructions: instructions.trim(),
        aspect: round.aspect,
        style: styleSlug,
        selectedDraft: selected,
      });
      patchRound(id, { prompt: dispatched.prompt });
      const outcome = await awaitCoverGeneration(dispatched.task_id);
      if (!outcome.ok || !outcome.url) {
        setRunError(outcome.error ?? 'generation failed');
        setStage('picking');
        return;
      }
      patchRound(id, { finalUrl: outcome.url, finalGenId: outcome.generatedMediaId });
      setSavedAsTemplate(false);
      setPromotedId(undefined);
      setStage('done');
    } catch (err) {
      console.error('[CoverStudio] stage 2 failed:', err);
      setRunError((err as Error).message);
      setStage('picking');
    }
  }, [round, gridUrl, selected, topic, person, model, allowSmallLabels, instructions, styleSlug, patchRound]);

  const apply = useCallback(async () => {
    if (!finalGenId || applying) return;
    setApplying(true);
    setApplyError(null);
    try {
      // Promote = the "saved to your library" half; the returned resource id
      // is the "filled in on the publish page" half. One call, both effects —
      // and both are NAMED on the card.
      const resourceId =
        promotedId ?? (await promoteGeneration(finalGenId)).promoted_resource_id;
      setPromotedId(resourceId);
      onApply(round?.aspect === '4:3' ? { horizontal: resourceId } : { vertical: resourceId });
      onClose();
    } catch (err) {
      console.error('[CoverStudio] apply failed:', err);
      setApplyError(
        t(
          'distribution.coverStudio.applyFailed',
          'The cover could not be saved to your library. It is still here — try again.',
        ),
      );
    } finally {
      setApplying(false);
    }
  }, [finalGenId, applying, promotedId, round, onApply, onClose, t]);

  const saveAsTemplate = useCallback(async () => {
    if (!finalGenId || savedAsTemplate) return;
    setApplyError(null);
    try {
      const { resourceId } = await saveGeneratedCoverAsTemplate(
        finalGenId,
        scopeId,
        promotedId,
      );
      setPromotedId(resourceId);
      setSavedAsTemplate(true);
    } catch (err) {
      console.error('[CoverStudio] save as template failed:', err);
      setApplyError(
        t(
          'distribution.coverStudio.saveTemplateFailed',
          'The cover could not be saved as a template. It is still here — try again.',
        ),
      );
    }
  }, [finalGenId, savedAsTemplate, scopeId, promotedId, t]);

  const toggleTemplate = useCallback(
    async (tpl: CoverTemplate) => {
      const existing = refs.find((r) => r.templateId === tpl.resource_id);
      if (existing) {
        setRefs((prev) => removeReference(prev, existing.genId));
        setRefusal(null);
        return;
      }
      if (templateBusyId) return;
      setTemplateBusyId(tpl.resource_id);
      setTemplateError(null);
      try {
        // The model only sees generated-media URLs, so the folder picture is
        // imported into one here, at the moment it enters the pool.
        const ref = await resolveCoverTemplateReference(tpl.resource_id);
        addRef({
          kind: 'template',
          genId: ref.genId,
          url: ref.url,
          label: tpl.name,
          templateId: tpl.resource_id,
        });
      } catch (err) {
        console.error('[CoverStudio] template reference failed:', err);
        setTemplateError(
          t(
            'distribution.coverStudio.templateRefFailed',
            'That template could not be prepared as a reference. Try again.',
          ),
        );
      } finally {
        setTemplateBusyId(null);
      }
    },
    [refs, templateBusyId, addRef, t],
  );

  // ── The no-AI paths: a centre-cropped frame, or an upload ───────────────
  const slot = orientation === 'vertical' ? 'vertical' : 'horizontal';

  const useFrameAsCover = useCallback(
    async (sourceId: string, timestampSeconds: number, focus: CropFocus) => {
      const res = await selectCoverFrame({
        source_resource_id: sourceId,
        timestamp_seconds: timestampSeconds,
        focus_x: focus.x,
        focus_y: focus.y,
      });
      // The server derives BOTH crops from one frame; only the slot this tab
      // is setting is applied. The other keeps whatever it had.
      onApply({
        [slot]:
          slot === 'vertical'
            ? res.cover_vertical_resource_id
            : res.cover_horizontal_resource_id,
      });
      onClose();
    },
    [slot, onApply, onClose],
  );

  const uploadAsCover = useCallback(
    async (file: File) => {
      const res = await uploadResource(file, scopeId);
      onApply({ [slot]: String(res.id) });
      onClose();
    },
    [slot, scopeId, onApply, onClose],
  );

  const visibleRounds = rounds.filter((r) => r.aspect === aspect);

  const restoreRound = useCallback(
    (r: Round) => {
      setActiveRound(r.id);
      setRunError(null);
      setSavedAsTemplate(false);
      setPromotedId(undefined);
      setStage(r.finalUrl ? 'done' : 'picking');
    },
    [],
  );

  if (!open) return null;

  const busy = stage === 'drafting' || stage === 'refining';
  const previewUrl = finalUrl ?? lastFrameUrl;
  const generateHint = !topic.trim()
    ? t('distribution.coverStudio.needsTopic', 'Blocked: give the publish a title first — the cover is about it.')
    : requiresPerson && !person
      ? t('distribution.coverStudio.needsPerson', 'Blocked: add the person picture first. Without it every draft is a different face.')
      : null;

  return (
    // Same chrome as SettingsModal: dimmed, blurred backdrop that closes on
    // click, and a centred rounded panel — the platform's own cover editor is
    // a dialog over the publish form, and that is the mental model the user
    // already has.
    <div
      className="cover-studio cs-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={t('distribution.coverStudio.title', 'Cover Studio')}
      data-testid="cover-studio-overlay"
    >
      <div className="cs-scrim" onClick={onClose} data-testid="cover-studio-scrim" />
      <div className="cs-modal cs-modal-v4">
        <div className="cs-top">
          <div className="cs-top-title">
            <h1>
              <Sparkles size={16} />
              {t('distribution.coverStudio.title', 'Cover Studio')}
            </h1>
            <div className="ctx">
              {sources[0]
                ? t('distribution.coverStudio.forVideo', {
                    defaultValue: 'For {{name}}',
                    name: sources[0].filename,
                  })
                : null}
              {topic.trim() ? ` · ${topic.trim()}` : null}
            </div>
          </div>

          <div className="cs-tabs" role="tablist" aria-label={t('distribution.coverStudio.coverTabs', 'Cover')}>
            <button
              type="button"
              role="tab"
              aria-selected={orientation === 'vertical'}
              className={orientation === 'vertical' ? 'on' : ''}
              onClick={() => {
                setOrientation('vertical');
                setActiveRound(null);
                setStage('idle');
              }}
              data-testid="cover-tab-vertical"
            >
              {t('distribution.coverStudio.tabVertical', 'Vertical cover 3:4')}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={orientation === 'horizontal'}
              className={orientation === 'horizontal' ? 'on' : ''}
              onClick={() => {
                setOrientation('horizontal');
                setActiveRound(null);
                setStage('idle');
              }}
              data-testid="cover-tab-horizontal"
            >
              {t('distribution.coverStudio.tabHorizontal', 'Horizontal cover 4:3')}
            </button>
          </div>

          <button
            type="button"
            className="cs-close"
            onClick={onClose}
            aria-label={t('common.close', 'Close')}
            data-testid="cover-studio-back"
          >
            <X size={20} />
          </button>
        </div>

        <div className="cs-cols cs-cols-v4">
          {/* ── left: what goes into the model ─────────────────────────── */}
          <div className="cs-col-left">
            {(
              <>
                <div className="cs-card">
                  <h4>
                    {t('distribution.coverStudio.prompt', 'Prompt')}
                    <span className="aux">
                      {t('distribution.coverStudio.promptAux', 'one line for the model')}
                    </span>
                  </h4>
                  <div className="cs-body">
                    <div className="cs-topicline" data-testid="cover-topic">
                      {topic.trim() ||
                        t('distribution.coverStudio.needsTopicShort', 'No title yet')}
                    </div>
                    <textarea
                      className="cs-instructions"
                      rows={3}
                      maxLength={500}
                      value={instructions}
                      onChange={(e) => setInstructions(e.target.value)}
                      placeholder={t(
                        'distribution.coverStudio.promptPlaceholder',
                        'e.g. headline “内容差的真相”, the person points at a big screen on the right',
                      )}
                      data-testid="cover-instructions"
                    />
                  </div>
                </div>

                <CoverReferencePool
                  refs={refs}
                  requiresPerson={requiresPerson}
                  onRemove={(genId) => setRefs((prev) => removeReference(prev, genId))}
                  onAddFromTemplates={() => {
                    document
                      .querySelector('[data-testid="cover-template-grid"]')
                      ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
                  }}
                  onUpload={(file) => void uploadReference(file)}
                  refusal={refusal}
                />
                {poolError && (
                  <div className="cs-error" data-testid="cover-ref-upload-error">
                    {poolError}
                  </div>
                )}

                <label className="cs-toggle cs-person-toggle">
                  <input
                    type="checkbox"
                    checked={grabAsPerson}
                    onChange={(e) => setGrabAsPerson(e.target.checked)}
                    data-testid="cover-grab-as-person"
                  />
                  <span>
                    {t('distribution.coverStudio.grabAsPerson', 'Next grab is the person reference')}
                    <em>
                      {person
                        ? t('distribution.coverStudio.personSetAt', {
                            defaultValue: 'Person = the frame at {{at}}; grabbing again replaces it.',
                            at: person.label ?? (typeof person.timestampSeconds === 'number' ? `${person.timestampSeconds.toFixed(1)}s` : ''),
                          })
                        : t('distribution.coverStudio.grabAsPersonWhy', 'The style keeps the same face across every draft. Grab a frame of yourself; it replaces the previous person.')}
                    </em>
                  </span>
                </label>

                <div className="cs-card">
                  <div className="cs-body">
                    <button
                      type="button"
                      className="cs-primary"
                      disabled={!canGenerate}
                      onClick={() => void runDrafts()}
                      data-testid="cover-generate"
                    >
                      {stage === 'drafting'
                        ? t('distribution.coverStudio.drafting', 'Drawing four drafts…')
                        : visibleRounds.length > 0
                          ? t('distribution.coverStudio.regenerateDrafts', 'Generate 4 new drafts')
                          : t('distribution.coverStudio.generateDrafts', 'Generate 4 drafts')}
                    </button>
                    <p className="cs-hint" data-testid="cover-generate-hint">
                      {generateHint ??
                        t('distribution.coverStudio.costNote', 'One image, four drafts — a round of ideas costs one generation.')}
                    </p>
                    {runError && stage === 'idle' && (
                      <div className="cs-error" data-testid="cover-drafts-error">
                        {runError}
                      </div>
                    )}
                  </div>
                </div>

                <div className="cs-card">
                  <h4>
                    {t('distribution.coverStudio.generatedCovers', 'AI covers')}
                    <span className="aux">{visibleRounds.length}</span>
                  </h4>
                  <div className="cs-body">
                    {visibleRounds.length === 0 ? (
                      <div className="cs-empty" data-testid="cover-history-empty">
                        {t('distribution.coverStudio.historyEmpty', 'Generated covers appear here; you can go back to any earlier round.')}
                      </div>
                    ) : (
                      <div className="cs-history" data-testid="cover-history">
                        {visibleRounds.map((r, i) => (
                          <button
                            key={r.id}
                            type="button"
                            className={`cs-round ${r.id === activeRound ? 'on' : ''}`}
                            onClick={() => restoreRound(r)}
                            disabled={busy}
                            data-testid={`cover-history-round-${i + 1}`}
                            title={t('distribution.coverStudio.roundN', { defaultValue: 'Round {{n}}', n: i + 1 })}
                          >
                            <img
                              className={r.aspect === '4:3' ? 'h' : ''}
                              src={`${getApiUrl()}${r.finalUrl ?? r.gridUrl}`}
                              alt={t('distribution.coverStudio.roundN', { defaultValue: 'Round {{n}}', n: i + 1 })}
                            />
                            <span>
                              {r.finalUrl
                                ? t('distribution.coverStudio.roundFinal', 'final')
                                : t('distribution.coverStudio.roundDrafts', 'drafts')}
                            </span>
                          </button>
                        ))}
                      </div>
                    )}
                    <p className="cs-hint" style={{ marginTop: 8 }}>
                      {t('distribution.coverStudio.historyHint', 'Click a thumbnail to go back to that round.')}
                    </p>
                  </div>
                </div>
              </>
            )}
          </div>

          {/* ── stage: what the user is looking at ─────────────────────── */}
          <div className="cs-col-stage">
            <div className="cs-steps cs-steps-v4">
              <span className={`cs-step ${stage !== 'idle' ? 'done' : 'now'}`}>
                <span className="num">1</span>
                {t('distribution.coverStudio.stepFrame', 'Frame · person')}
              </span>
              <span className="bar" />
              <span className={`cs-step ${stage === 'done' ? 'done' : stage === 'drafting' || stage === 'picking' || stage === 'refining' ? 'now' : ''}`}>
                <span className="num">2</span>
                {t('distribution.coverStudio.stepDrafts', 'Four drafts')}
              </span>
              <span className="bar" />
              <span className={`cs-step ${stage === 'done' ? 'now' : ''}`}>
                <span className="num">3</span>
                {t('distribution.coverStudio.stepFinal', 'Final cover')}
              </span>
            </div>

            {stage === 'idle' ? (
              <div className="cs-card cs-stagecard">
                <CoverFrameGrabber
                  embedded
                  aspect={aspect}
                  sources={sources}
                  grabbedAt={frameTimestamps}
                  onGrabbed={onGrabbed}
                  poolFull={refs.length >= 9 && !grabAsPerson}
                  onUseAsCover={useFrameAsCover}
                  onUploadCover={uploadAsCover}
                />
              </div>
            ) : stage === 'done' && finalUrl ? (
              <div className="cs-card">
                <h4>
                  {t('distribution.coverStudio.finalCover', 'Final cover')}
                  <span className="aux">
                    {t('distribution.coverStudio.finalFrom', {
                      defaultValue: '{{aspect}} · redrawn from draft #{{n}}',
                      aspect: round?.aspect ?? aspect,
                      n: selected ?? 1,
                    })}
                  </span>
                </h4>
                <div className="cs-body cs-finalstage">
                  <div className={`cs-final ${round?.aspect === '4:3' ? 'h' : ''}`}>
                    <img src={`${getApiUrl()}${finalUrl}`} alt={t('distribution.coverStudio.finalCover', 'Final cover')} data-testid="cover-final-image" />
                  </div>
                  {prompt && (
                    <details className="cs-prompt">
                      <summary>{t('distribution.coverStudio.whatWasSent', 'What was sent to the model')}</summary>
                      <div className="cs-promptbox">
                        <pre data-testid="cover-prompt-text">{prompt}</pre>
                      </div>
                    </details>
                  )}
                </div>
              </div>
            ) : (
              <CoverDrafts
                stage={stage}
                gridUrl={gridUrl}
                selected={selected}
                onSelect={(n) => {
                  if (round) patchRound(round.id, { selected: n });
                }}
                onRefine={() => void runRefine()}
                prompt={prompt}
                error={runError}
                aspect={round?.aspect ?? aspect}
              />
            )}

            {templateError && (
              <div className="cs-error" data-testid="cover-template-ref-error">
                {templateError}
              </div>
            )}
            <CoverTemplateGrid
              scopeId={scopeId}
              selectedIds={templateIds(refs)}
              busyId={templateBusyId}
              onToggle={(tpl) => void toggleTemplate(tpl)}
            />
          </div>

          {/* ── side: preview, model + style, actions ─────────────────── */}
          <div className="cs-col-side">
            <div className="cs-card">
              <h4>
                {orientation === 'vertical'
                  ? t('distribution.coverStudio.previewV', 'Vertical preview 3:4')
                  : t('distribution.coverStudio.previewH', 'Horizontal preview 4:3')}
              </h4>
              <div className="cs-body">
                <div className={`cs-preview ${aspect === '4:3' ? 'h' : 'v'}`} data-testid="cover-preview">
                  {previewUrl ? (
                    <img src={`${getApiUrl()}${previewUrl}`} alt="" />
                  ) : (
                    <span>{t('distribution.coverStudio.previewEmpty', 'How it will look in the feed.')}</span>
                  )}
                </div>
                <p className="cs-hint" style={{ marginTop: 8 }}>
                  {stage === 'done'
                    ? t('distribution.coverStudio.previewFinal', 'The finished cover as it will appear.')
                    : t('distribution.coverStudio.previewHint', 'Switch between vertical and horizontal at the top.')}
                </p>
              </div>
            </div>

            <div className="cs-card">
              <h4>{t('distribution.coverStudio.modelAndStyle', 'Model & style')}</h4>
              <div className="cs-body">
                <label className="cs-field">
                  <span>{t('distribution.coverStudio.model', 'Model')}</span>
                  <select
                    className="cs-source"
                    style={{ marginBottom: 0 }}
                    aria-label={t('distribution.coverStudio.model', 'Model')}
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                  >
                    <option value="">{t('distribution.coverStudio.modelDefault', 'Catalog default')}</option>
                    {models.map((m) => (
                      <option key={m.name} value={m.name}>
                        {m.display_name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="cs-field">
                  <span>{t('distribution.coverStudio.styleSkill', 'Style (cover skill)')}</span>
                  <select
                    className="cs-source"
                    style={{ marginBottom: 0 }}
                    aria-label={t('distribution.coverStudio.styleSkill', 'Style (cover skill)')}
                    value={styleSlug}
                    onChange={(e) => setStyleSlug(e.target.value)}
                    data-testid="cover-style"
                  >
                    {(styles.length > 0
                      ? styles
                      : [{ slug: BUILTIN_COVER_STYLE, name: 'Viral Video Cover' }]
                    ).map((st) => (
                      <option key={st.slug} value={st.slug}>
                        {st.name}
                      </option>
                    ))}
                  </select>
                </label>
                <p className="cs-hint" data-testid="cover-style-note">
                  {style?.description ||
                    t('distribution.coverStudio.styleNote', 'The list is the built-in style plus every cover skill you have imported (category: cover).')}
                </p>
                <label className="cs-toggle">
                  <input
                    type="checkbox"
                    checked={allowSmallLabels}
                    onChange={(e) => setAllowSmallLabels(e.target.checked)}
                    data-testid="cover-small-labels"
                  />
                  <span>
                    {t('distribution.coverStudio.smallLabels', 'Platform-style labels — "REC", a search bar, corner tags.')}
                    <em>
                      {t('distribution.coverStudio.smallLabelsWhy', 'Your two files disagree: SKILL.md forbids them, cover-grammar.md allows them. Off follows SKILL.md.')}
                    </em>
                  </span>
                </label>
              </div>
            </div>

            <div className="cs-card">
              <div className="cs-body">
                {stage === 'idle' ? (
                  <p className="cs-hint" data-testid="cover-side-hint">
                    {t('distribution.coverStudio.noAiHint', 'No AI needed: the frame on the stage can be the cover as it is — use the button under it.')}
                  </p>
                ) : stage !== 'done' ? (
                  <p className="cs-hint" data-testid="cover-side-hint">
                    {t('distribution.coverStudio.refineNote', 'A second pass redraws it full size and drops the number.')}
                  </p>
                ) : (
                  <>
                    <div className="cs-chosen">
                      <b>{t('distribution.coverStudio.thisIsYourCover', 'This is your cover.')}</b>
                      <ul>
                        <li>
                          {round?.aspect === '4:3'
                            ? t('distribution.coverStudio.willFillPublishH', 'Fills the horizontal cover slot on the publish page')
                            : t('distribution.coverStudio.willFillPublish', 'Fills the vertical cover slot on the publish page')}
                        </li>
                        <li>{t('distribution.coverStudio.willSaveLibrary', 'Saves the file to your library')}</li>
                      </ul>
                    </div>
                    {applyError && <div className="cs-error" style={{ margin: '8px 0' }}>{applyError}</div>}
                    <div className="cs-actions cs-actions-col">
                      <button
                        type="button"
                        className="cs-primary"
                        disabled={applying}
                        onClick={() => void apply()}
                        data-testid="cover-apply"
                      >
                        {applying
                          ? t('distribution.coverStudio.applying', 'Saving…')
                          : t('distribution.coverStudio.done', 'Done')}
                      </button>
                      <button
                        type="button"
                        className="cs-ghost"
                        disabled={savedAsTemplate}
                        onClick={() => void saveAsTemplate()}
                        data-testid="cover-save-template"
                      >
                        {savedAsTemplate
                          ? t('distribution.coverStudio.savedAsTemplate', 'Saved as template')
                          : t('distribution.coverStudio.saveAsTemplate', 'Save as template')}
                      </button>
                      <button
                        type="button"
                        className="cs-ghost"
                        onClick={() => setStage('picking')}
                        data-testid="cover-try-another"
                      >
                        {t('distribution.coverStudio.tryAnother', 'Try another draft')}
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
