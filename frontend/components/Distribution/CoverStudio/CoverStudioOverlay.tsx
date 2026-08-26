// components/Distribution/CoverStudio/CoverStudioOverlay.tsx
//
// Cover Studio itself: a full-screen layer over the publish page, NOT a route.
//
// ★ Why a layer: PublishPage holds ~75 useState with zero persistence — no URL
// params, no sessionStorage. Navigating away and back would wipe the whole
// form the user just filled (title, topics, accounts, music, schedule). A
// layer keeps that state alive underneath, and "Back to publish" is honest:
// the page is literally still there.
//
// The pieces are the container-agnostic cards built earlier; this file owns
// only the assembly: the reference pool's state, the two-stage run, and the
// apply step (fill the publish page's vertical slot + the cover is already in
// the library via promote — both named on the card, because a file appearing
// in the library unasked is a surprise this app has caused before).

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Sparkles, X } from 'lucide-react';

import { getApiUrl } from '../../../utils/apiConfig';
import {
  listGenerationModels,
  type GenerationModel,
} from '../../../features/canvas-core/services/canvasGenerationService';
import {
  awaitCoverGeneration,
  generateCoverDrafts,
  refineCoverDraft,
} from '../../../services/coverStudioService';
import { promoteGeneration } from '../../../services/generatedMediaService';
import {
  markCoverTemplatesUsed,
  resolveCoverTemplateReference,
  saveGeneratedCoverAsTemplate,
  type CoverTemplate,
} from '../../../services/coverTemplateService';
import type { LibraryVideo } from '../../../types';
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
import { CoverFrameGrabber, type GrabbedFrame } from './CoverFrameGrabber';
import { CoverDrafts, type CoverStage } from './CoverDrafts';
import './cover-studio.css';

interface Props {
  open: boolean;
  scopeId: string;
  /** The videos picked on the publish page. */
  sources: LibraryVideo[];
  /** The topic — the publish title, which is what the cover is about. */
  topic: string;
  onClose: () => void;
  /**
   * The finished cover as a RESOURCE id (already promoted). The parent puts it
   * into the vertical cover slot; there is no horizontal — the style is 3:4
   * only and cropping a portrait to 4:3 would cut the face out.
   */
  onApply: (verticalResourceId: string) => void;
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

  const [refs, setRefs] = useState<CoverReference[]>([]);
  const [refusal, setRefusal] = useState<'full' | 'duplicate' | null>(null);
  const [grabAsPerson, setGrabAsPerson] = useState(false);
  const [allowSmallLabels, setAllowSmallLabels] = useState(false);
  const [models, setModels] = useState<GenerationModel[]>([]);
  const [model, setModel] = useState('');

  const [stage, setStage] = useState<CoverStage>('idle');
  const [gridUrl, setGridUrl] = useState<string | undefined>(undefined);
  const [prompt, setPrompt] = useState<string | undefined>(undefined);
  const [selected, setSelected] = useState<number | null>(null);
  const [finalUrl, setFinalUrl] = useState<string | undefined>(undefined);
  const [finalGenId, setFinalGenId] = useState<string | undefined>(undefined);
  const [runError, setRunError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [savedAsTemplate, setSavedAsTemplate] = useState(false);
  // The final cover's resource id once it has been promoted — by "Use this
  // cover" OR by "Save as template" — so the other button never promotes the
  // same picture a second time.
  const [promotedId, setPromotedId] = useState<string | undefined>(undefined);
  // Which template tile is being turned into a reference right now (the
  // generated-media import is a round-trip), and why the last one failed.
  const [templateBusyId, setTemplateBusyId] = useState<string | null>(null);
  const [templateError, setTemplateError] = useState<string | null>(null);

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
      addRef({
        kind: grabAsPerson ? 'person' : 'frame',
        genId: frame.generatedMediaId,
        url: frame.url,
        timestampSeconds: frame.timestampSeconds,
      });
      // One person is the point; leaving the box ticked would turn every next
      // grab into a person swap the user did not ask for.
      if (grabAsPerson) setGrabAsPerson(false);
    },
    [addRef, grabAsPerson],
  );

  const canGenerate =
    stage !== 'drafting' && stage !== 'refining' && !!topic.trim() && !!person;

  const runDrafts = useCallback(async () => {
    if (!canGenerate) return;
    setStage('drafting');
    setRunError(null);
    setSelected(null);
    setFinalUrl(undefined);
    setFinalGenId(undefined);
    setSavedAsTemplate(false);
    setPromotedId(undefined);
    try {
      const dispatched = await generateCoverDrafts({
        topic: topic.trim(),
        sourceUrls: sourceUrls(refs),
        model,
        allowSmallLabels,
      });
      setPrompt(dispatched.prompt);
      // Usage ticks AFTER a successful dispatch, once per run — and it can
      // never fail the generation (the service swallows its own errors).
      void markCoverTemplatesUsed(templateIds(refs));
      const outcome = await awaitCoverGeneration(dispatched.task_id);
      if (!outcome.ok || !outcome.url) {
        setRunError(outcome.error ?? 'generation failed');
        setStage('idle');
        return;
      }
      setGridUrl(outcome.url);
      setStage('picking');
    } catch (err) {
      console.error('[CoverStudio] stage 1 failed:', err);
      setRunError((err as Error).message);
      setStage('idle');
    }
  }, [canGenerate, topic, refs, model, allowSmallLabels]);

  const runRefine = useCallback(async () => {
    if (!gridUrl || selected === null) return;
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
        selectedDraft: selected,
      });
      setPrompt(dispatched.prompt);
      const outcome = await awaitCoverGeneration(dispatched.task_id);
      if (!outcome.ok || !outcome.url) {
        setRunError(outcome.error ?? 'generation failed');
        setStage('picking');
        return;
      }
      setFinalUrl(outcome.url);
      setFinalGenId(outcome.generatedMediaId);
      setSavedAsTemplate(false);
      setPromotedId(undefined);
      setStage('done');
    } catch (err) {
      console.error('[CoverStudio] stage 2 failed:', err);
      setRunError((err as Error).message);
      setStage('picking');
    }
  }, [gridUrl, selected, topic, person, model, allowSmallLabels]);

  const apply = useCallback(async () => {
    if (!finalGenId || applying) return;
    setApplying(true);
    setApplyError(null);
    try {
      // Promote = the "saved to your library" half; the returned resource id
      // is the "filled in on the publish page" half. One call, both effects —
      // and both are NAMED on the card below.
      const resourceId =
        promotedId ?? (await promoteGeneration(finalGenId)).promoted_resource_id;
      setPromotedId(resourceId);
      onApply(resourceId);
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
  }, [finalGenId, applying, promotedId, onApply, onClose, t]);

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

  if (!open) return null;

  return (
    // Same chrome as SettingsModal: dimmed, blurred backdrop that closes on
    // click, and a centred rounded panel. Cover Studio used to take the whole
    // viewport, which read as "I navigated to another page" — the platform's
    // own cover editor is a dialog over the publish form, and that is the
    // mental model the user already has.
    <div
      className="cover-studio cs-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={t('distribution.coverStudio.title', 'Cover Studio')}
      data-testid="cover-studio-overlay"
    >
      <div className="cs-scrim" onClick={onClose} data-testid="cover-studio-scrim" />
      <div className="cs-modal">
      <div className="cs-top">
        <div>
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

      <div className="cs-cols">
        <div className="cs-col-controls">
          <div className="cs-card">
            <h4>{t('distribution.coverStudio.model', 'Model')}</h4>
            <div className="cs-body">
              <select
                className="cs-source"
                style={{ marginBottom: 0 }}
                aria-label={t('distribution.coverStudio.model', 'Model')}
                value={model}
                onChange={(e) => setModel(e.target.value)}
              >
                <option value="">
                  {t('distribution.coverStudio.modelDefault', 'Catalog default')}
                </option>
                {models.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.display_name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="cs-card">
            <h4>{t('distribution.coverStudio.style', 'Style')}</h4>
            <div className="cs-body">
              {/* Single-select by decision — and there is one style. A radio
                  group of one would be theatre; the card states what the style
                  is and what it demands. */}
              <div className="cs-style-name">Viral Video Cover</div>
              <p className="cs-hint">
                {t(
                  'distribution.coverStudio.styleDesc',
                  'Dark background, one huge headline, one strong face. 3:4 only — this grammar is written for the vertical phone feed; a horizontal cover needs a different style, not a crop.',
                )}
              </p>
              <label className="cs-toggle">
                <input
                  type="checkbox"
                  checked={allowSmallLabels}
                  onChange={(e) => setAllowSmallLabels(e.target.checked)}
                  data-testid="cover-small-labels"
                />
                <span>
                  {t(
                    'distribution.coverStudio.smallLabels',
                    'Platform-style labels — "REC", a search bar, corner tags.',
                  )}
                  <em>
                    {/* ⚠️ NOT the design mock's wording — that said "Off follows
                        the newer file", which is backwards: the newer file
                        (cover-grammar.md) is the one that ALLOWS them. */}
                    {t(
                      'distribution.coverStudio.smallLabelsWhy',
                      'Your two files disagree: SKILL.md forbids them, cover-grammar.md allows them. Off follows SKILL.md.',
                    )}
                  </em>
                </span>
              </label>
            </div>
          </div>

          <CoverFrameGrabber
            sources={sources}
            grabbedAt={frameTimestamps}
            onGrabbed={onGrabbed}
            poolFull={refs.length >= 9 && !grabAsPerson}
          />

          <label className="cs-toggle cs-person-toggle">
            <input
              type="checkbox"
              checked={grabAsPerson}
              onChange={(e) => setGrabAsPerson(e.target.checked)}
              data-testid="cover-grab-as-person"
            />
            <span>
              {t(
                'distribution.coverStudio.grabAsPerson',
                'Next grab is the person reference',
              )}
              <em>
                {t(
                  'distribution.coverStudio.grabAsPersonWhy',
                  'The style keeps the same face across every draft. Grab a frame of yourself; it replaces the previous person.',
                )}
              </em>
            </span>
          </label>

          <CoverReferencePool
            refs={refs}
            requiresPerson
            onRemove={(genId) => setRefs((prev) => removeReference(prev, genId))}
            onAddFromTemplates={() => {
              /* templates are on the right column; the tile scrolls there */
              document
                .querySelector('[data-testid="cover-template-grid"]')
                ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }}
            refusal={refusal}
          />

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
                  : t('distribution.coverStudio.generateDrafts', 'Generate 4 drafts')}
              </button>
              <p className="cs-hint" style={{ marginTop: 8 }} data-testid="cover-generate-hint">
                {!topic.trim()
                  ? t(
                      'distribution.coverStudio.needsTopic',
                      'Blocked: give the publish a title first — the cover is about it.',
                    )
                  : !person
                    ? t(
                        'distribution.coverStudio.needsPerson',
                        'Blocked: add the person picture first. Without it every draft is a different face.',
                      )
                    : t(
                        'distribution.coverStudio.costNote',
                        'One image, four drafts — a round of ideas costs one generation.',
                      )}
              </p>
            </div>
          </div>
        </div>

        <div className="cs-col-results">
          <CoverDrafts
            stage={stage}
            gridUrl={gridUrl}
            selected={selected}
            onSelect={setSelected}
            onRefine={() => void runRefine()}
            prompt={prompt}
            error={runError}
          />

          {stage === 'done' && finalUrl && (
            <div className="cs-card">
              <h4>{t('distribution.coverStudio.finalCover', 'Final cover')}</h4>
              <div className="cs-finalwrap">
                <div className="cs-final">
                  <img src={`${getApiUrl()}${finalUrl}`} alt="" />
                </div>
                <div className="cs-chosen">
                  <b>{t('distribution.coverStudio.thisIsYourCover', 'This is your cover.')}</b>
                  {/* Both effects of Apply, named up front: a file appearing in
                      the library unasked is a surprise this app has caused
                      before. */}
                  <ul>
                    <li>
                      {t(
                        'distribution.coverStudio.willFillSlot',
                        'Fills the vertical cover slot on the publish page',
                      )}
                    </li>
                    <li>
                      {t(
                        'distribution.coverStudio.willSaveLibrary',
                        'Saves the file to your library',
                      )}
                    </li>
                  </ul>
                  {applyError && <div className="cs-error" style={{ margin: '8px 0' }}>{applyError}</div>}
                  <div className="cs-actions">
                    <button
                      type="button"
                      className="cs-primary"
                      style={{ width: 'auto' }}
                      disabled={applying}
                      onClick={() => void apply()}
                      data-testid="cover-apply"
                    >
                      {applying
                        ? t('distribution.coverStudio.applying', 'Saving…')
                        : t('distribution.coverStudio.useThisCover', 'Use this cover')}
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
                    >
                      {t('distribution.coverStudio.tryAnother', 'Try another draft')}
                    </button>
                  </div>
                </div>
              </div>
            </div>
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
      </div>
      </div>
    </div>
  );
}
