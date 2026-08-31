// frontend/components/resources/assets/sheet/GenerateMissingDialog.tsx
//
// "Generate" on an empty slot: read what a paid run would ask for, then pay
// for it. Three phases in one dialog - preview, run, result.
//
// What this dialog exists to prevent, in order:
//
//  * SPENDING BLIND. `GET /generate-slot/preview` is the dry run, built by the
//    same service code as the run itself, so what is on screen IS what will be
//    sent. It is fetched before any button can be pressed.
//  * A SLOT THAT CANNOT BE DRAWN LOOKING LIKE A FAILURE. `audio` / `prompt`
//    assets, the `unsorted` bucket and a character's `worn` slot have no
//    template by design (`SlotNotGeneratable`). The 422 is rendered as the
//    typed sentence it is, with Generate off - not as "something went wrong".
//  * A PARTIAL RUN READ AS A CLEAN ONE. The 202 carries `generation_ids`,
//    `failed` (per unit) and `skipped_references` (references that could not
//    be materialized). All three are rendered. Reading only the first would
//    report "4 images" for a run that produced two and silently dropped the
//    asset's own reference image.
//
// Two things the fields are NOT:
//
//  * `negative` is NOT sent to any provider - no image adapter in this repo
//    takes one. It is recorded on the generation as provenance, and is
//    labelled that way here rather than implied to shape the picture.
//  * `reference_resource_ids` is INTENT. What actually went is the run's
//    `skipped_references`, which is why the result panel lists them.
//
// The products do NOT join the asset: they land in the Generated inbox as
// `unreviewed`. `Attach Now` is the shortcut for a user who has looked at the
// result and wants it on the slot; it goes through `save-as-asset` per id,
// which is the same path the inbox itself uses.

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Loader2, Sparkles } from 'lucide-react';

import { UiModal } from '../../../ui/primitives';
import { useToast } from '../../../Toast';
import { generateSlot, previewGenerateSlot } from '../../../../services/assetsService';
import type {
  AssetRowDetail,
  GenerateSlotFailure,
  GenerateSlotPreview,
  GenerateSlotResult,
  SkippedReference,
} from '../../../../services/assetsService';
import { saveGenerationAsAsset } from '../../../../services/generatedService';
import { useGenerationModels } from '../../../../features/canvas-core/smart/nodes/useGenerationModels';
import { slotLabelKey } from '../assetTypeMeta';
import { loadoutForSlot } from './assetSheetModel';

/** The server's own bound (`GenerateSlotRequest.count`, 1..4). Mirrored so a
 *  larger ask is unreachable here instead of arriving as a 422. */
export const MIN_COUNT = 1;
export const MAX_COUNT = 4;

/** What `Attach Now` did, per generation. */
interface AttachOutcome {
  ok: number;
  failed: { id: string; message: string }[];
}

// ─── The per-unit ledger, rendered by BOTH outcomes ─────────────────────────
//
// The 202 (some units landed) and the 503 (none did) carry the SAME two lists:
// the 202 puts them in the body, the 503 puts them in `error.extra`. Rendering
// them through one pair of components is what stops the worse outcome from
// reporting less than the better one - the shape recorded as
// `reference-backend-returns-frontend-never-reads`, here on a path that costs
// money.

const FailedUnits: React.FC<{ units: GenerateSlotFailure[]; testId: string }> = ({
  units,
  testId,
}) => {
  const { t } = useTranslation();
  if (units.length === 0) return null;
  return (
    <ul data-testid={testId} className="flex flex-col gap-0.5">
      {units.map((unit) => (
        <li
          key={unit.index}
          className="flex items-start gap-1 text-[11px] text-danger"
          data-unit-index={unit.index}
        >
          <AlertTriangle size={11} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>
            {t('assets.gen.unitFailed', {
              n: unit.index + 1,
              defaultValue: 'Image {{n}} failed',
            })}
            {' — '}
            {t(`assets.err.${unit.code}`, unit.detail)}
          </span>
        </li>
      ))}
    </ul>
  );
};

const SkippedRefs: React.FC<{ refs: SkippedReference[]; testId: string }> = ({
  refs,
  testId,
}) => {
  const { t } = useTranslation();
  if (refs.length === 0) return null;
  return (
    <ul data-testid={testId} className="flex flex-col gap-0.5">
      {refs.map((ref) => (
        <li
          key={ref.resource_id}
          data-resource-id={ref.resource_id}
          className="flex items-start gap-1 text-[11px] text-warn"
        >
          <AlertTriangle size={11} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>
            {t('assets.gen.skippedRef', {
              id: ref.resource_id,
              defaultValue: 'Reference {{id}} was not sent',
            })}
            {' — '}
            {/* `reason` is DELIBERATELY un-localized machine text
                (`materialize_failed: [Errno 2] …`, `resource_not_found`). It
                is the diagnosable half of this line and there is no honest
                translation of an errno; the sentence around it is translated.
                Do not wrap it in `t()` - that would render the raw key. */}
            {ref.reason}
          </span>
        </li>
      ))}
    </ul>
  );
};

/** Narrow one of the 503's `extra` lists without trusting its shape. */
function failuresFrom(value: unknown): GenerateSlotFailure[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (unit): unit is GenerateSlotFailure =>
      typeof unit === 'object' &&
      unit !== null &&
      typeof (unit as GenerateSlotFailure).index === 'number' &&
      typeof (unit as GenerateSlotFailure).code === 'string',
  );
}

function skippedFrom(value: unknown): SkippedReference[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (ref): ref is SkippedReference =>
      typeof ref === 'object' &&
      ref !== null &&
      typeof (ref as SkippedReference).resource_id === 'string',
  );
}

export interface GenerateMissingDialogProps {
  open: boolean;
  scopeId: string;
  detail: AssetRowDetail;
  slot: string;
  /** The sheet's selected loadout. It changes the PROMPT (loadout extra +
   *  linked costume prompts), so it rides on the preview and on the run. */
  loadoutId: string | null;
  onClose: () => void;
  /** Something landed on the asset - the page refetches so the pins move. */
  onAttached: () => void;
  /** Navigate to the Generated inbox (the page owns `resPath`). */
  onOpenInbox: () => void;
  onError: (err: unknown) => void;
}

export const GenerateMissingDialog: React.FC<GenerateMissingDialogProps> = ({
  open,
  scopeId,
  detail,
  slot,
  loadoutId,
  onClose,
  onAttached,
  onOpenInbox,
  onError,
}) => {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const [preview, setPreview] = useState<GenerateSlotPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(true);
  /**
   * The TYPED refusal from the preview, as a CODE. A slot with no template is
   * a refusal the user can act on, not an error to retry.
   *
   * The code is stored and translated at render rather than translated into
   * state, so the fetch effect below does not have to depend on `t` -
   * react-i18next does not promise a stable `t` identity, and an effect that
   * depends on one re-runs on every render. Here that would be a preview
   * request loop that also reset the count picker under the user's hands
   * (same reasoning as `useAssetFailureReporter`'s ref).
   */
  const [refusal, setRefusal] = useState<{
    /**
     * WHICH request was refused, because the two render in different places:
     * a refused PREVIEW replaces the preview (there is nothing to configure),
     * a refused RUN sits under it (the settings are still there to change and
     * retry). `phase` is a single value, so the two can never be on screen at
     * once and they can share one `data-testid`.
     */
    phase: 'preview' | 'run';
    /** Null when the failure carried no typed code at all. */
    code: string | null;
    /**
     * The provider's OWN sentence (`error.detail`). The backend deliberately
     * sets the 503's detail to `failed[0]["detail"]` - "insufficient balance",
     * a content-policy refusal - and the canned `assets.err.generation_failed`
     * says none of it. Kept as text, not a code: it is not ours to translate.
     */
    detail: string | null;
    /** The 503's `error.extra` ledger. Empty for a failure that carried none
     *  (a network error, a non-envelope body). */
    failed: GenerateSlotFailure[];
    skipped: SkippedReference[];
  } | null>(null);
  const [model, setModel] = useState('');
  const [count, setCount] = useState(1);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<GenerateSlotResult | null>(null);
  const [attaching, setAttaching] = useState(false);
  const [attached, setAttached] = useState<AttachOutcome | null>(null);

  // Image models only: `generate_slot` calls `ImageGenerationService`, so a
  // video row in the picker would be a model the run cannot use.
  const models = useGenerationModels('image');

  const slotLabel = t(slotLabelKey(slot), slot);

  // Preview on open, and again whenever the loadout changes under it - the
  // loadout is part of the composed prompt, so a stale preview would be a
  // preview of a different request.
  useEffect(() => {
    if (!open) return;
    let alive = true;
    setPreview(null);
    setRefusal(null);
    setPreviewLoading(true);
    setResult(null);
    setAttached(null);
    setCount(1);
    previewGenerateSlot(scopeId, detail.id, slot, loadoutId ?? undefined)
      .then((row) => {
        if (!alive) return;
        setPreview(row);
        // The catalog default travels back as `model`; seeding the picker with
        // it means the dropdown states what WILL run rather than an empty box.
        setModel(row.model ?? '');
      })
      .catch((err: unknown) => {
        if (!alive) return;
        console.error('[GenerateMissingDialog] preview failed:', err);
        setRefusal({
          phase: 'preview',
          code: (err as { code?: string } | null)?.code ?? null,
          // A refused PREVIEW replaces the whole panel with its typed sentence
          // and spends nothing; there is no per-unit ledger to show.
          detail: null,
          failed: [],
          skipped: [],
        });
      })
      .finally(() => {
        if (alive) setPreviewLoading(false);
      });
    return () => {
      alive = false;
    };
    // No `t` in this list, and none in the body either: the refusal is stored
    // as a CODE and translated at render. react-i18next does not promise a
    // stable `t`, so translating in here would make the dep list complete AND
    // make the effect re-run every render - a preview request loop that also
    // reset the count picker under the user's hands.
  }, [open, scopeId, detail.id, slot, loadoutId]);

  const run = useCallback(async () => {
    if (running || !preview) return;
    setRunning(true);
    setResult(null);
    setAttached(null);
    setRefusal(null);
    try {
      const out = await generateSlot(scopeId, detail.id, {
        slot,
        loadout_id: loadoutId,
        // '' means "let the catalog decide", which is what omitting it does.
        model: model === '' ? null : model,
        count,
      });
      setResult(out);
      addToast(
        t('assets.gen.done', {
          n: out.generation_ids.length,
          defaultValue: 'Sent {{n}} To The Generated Inbox',
        }),
        'success',
      );
    } catch (err) {
      // BOTH: the shared reporter logs and toasts, and the dialog says it
      // where the user is standing. A run that failed wholly is a 503 over an
      // empty list - the settings that produced it are still on screen, and a
      // toast that has already faded leaves them with no reason to change any
      // of them. Same standard `EquipDialog` holds itself to.
      onError(err);
      // Everything the 503 carried, not just its code. `count=1` makes EVERY
      // total failure a 503, so storing only the code told the user LESS about
      // the worst outcome than the 202 tells them about a partial one - and
      // the provider's reason ("insufficient balance", a policy refusal) lived
      // only in the console.
      const typed = err as
        | { code?: string; detail?: string; extra?: Record<string, unknown> }
        | null;
      setRefusal({
        phase: 'run',
        code: typed?.code ?? null,
        detail: typeof typed?.detail === 'string' && typed.detail ? typed.detail : null,
        failed: failuresFrom(typed?.extra?.failed),
        skipped: skippedFrom(typed?.extra?.skipped_references),
      });
    } finally {
      setRunning(false);
    }
  }, [
    running,
    preview,
    scopeId,
    detail.id,
    slot,
    loadoutId,
    model,
    count,
    addToast,
    t,
    onError,
  ]);

  const attachNow = useCallback(async () => {
    if (attaching || !result || result.generation_ids.length === 0) return;
    setAttaching(true);
    const outcome: AttachOutcome = { ok: 0, failed: [] };
    // Sequential and per-id: `save-as-asset` is one call per generation, and a
    // rejection on the third must not hide that the first two landed. This is
    // the opposite contract to the Equip batch, which is atomic server-side.
    for (const id of result.generation_ids) {
      try {
        await saveGenerationAsAsset(scopeId, id, {
          asset_id: detail.id,
          slot,
          loadout_id: loadoutForSlot(slot, loadoutId) ?? undefined,
        });
        outcome.ok += 1;
      } catch (err) {
        console.error('[GenerateMissingDialog] save-as-asset failed:', id, err);
        const code = (err as { code?: string } | null)?.code;
        outcome.failed.push({
          id,
          message: code
            ? t(`assets.err.${code}`, t('assets.err.generic'))
            : t('assets.err.generic'),
        });
      }
    }
    setAttached(outcome);
    setAttaching(false);
    if (outcome.ok > 0) {
      addToast(
        t('assets.gen.attached', {
          n: outcome.ok,
          slot: slotLabel,
          defaultValue: 'Attached {{n}} To {{slot}}',
        }),
        outcome.failed.length === 0 ? 'success' : 'info',
      );
      onAttached();
    }
    if (outcome.failed.length > 0) {
      addToast(
        t('assets.gen.attachFailed', {
          n: outcome.failed.length,
          defaultValue: '{{n}} Could Not Be Attached',
        }),
        'error',
      );
    }
  }, [attaching, result, scopeId, detail.id, slot, loadoutId, slotLabel, addToast, t, onAttached]);

  if (!open) return null;

  return (
    <UiModal
      isOpen
      onClose={onClose}
      title={t('assets.gen.title', { slot: slotLabel, defaultValue: 'Generate {{slot}}' })}
      widthClassName="max-w-lg"
    >
      <div
        className="flex flex-col gap-3"
        data-testid="generate-missing-dialog"
        data-slot={slot}
      >
        {previewLoading ? (
          <p className="flex items-center gap-2 py-6 text-xs text-content-3">
            <Loader2 size={14} className="animate-spin" aria-hidden="true" />
            {t('common.loading', 'Loading...')}
          </p>
        ) : refusal?.phase === 'preview' ? (
          <p
            role="alert"
            data-testid="generate-refused"
            data-code={refusal.code ?? ''}
            className="py-4 text-xs text-danger"
          >
            {refusal.code
              ? t(`assets.err.${refusal.code}`, t('assets.err.generic'))
              : t('assets.err.generic')}
          </p>
        ) : preview ? (
          <>
            <section className="flex flex-col gap-1" data-testid="generate-preview">
              <h4 className="text-[11px] font-medium uppercase tracking-wide text-content-4">
                {t('assets.gen.positive', 'Prompt')}
              </h4>
              <p
                data-testid="preview-positive"
                className="max-h-32 overflow-y-auto whitespace-pre-wrap rounded-lg border border-line bg-island-2 px-2.5 py-1.5 text-[12px] text-content-2"
              >
                {preview.positive}
              </p>
              {preview.negative !== '' && (
                <>
                  <h4 className="mt-1 text-[11px] font-medium uppercase tracking-wide text-content-4">
                    {t('assets.gen.negative', 'Negative (Recorded)')}
                  </h4>
                  <p
                    data-testid="preview-negative"
                    className="max-h-20 overflow-y-auto whitespace-pre-wrap rounded-lg border border-line bg-island-2 px-2.5 py-1.5 text-[12px] text-content-3"
                  >
                    {preview.negative}
                  </p>
                  <p className="text-[10px] text-content-4">
                    {t(
                      'assets.gen.negativeNote',
                      'Stored with the generation. No image model here accepts one.',
                    )}
                  </p>
                </>
              )}
              <div className="mt-1 flex flex-wrap gap-3 text-[11px] text-content-4">
                <span data-testid="preview-aspect">
                  {t('assets.gen.aspect', 'Aspect')} {preview.aspect_ratio}
                </span>
                <span data-testid="preview-references">
                  {t('assets.gen.references', {
                    n: preview.reference_resource_ids.length,
                    defaultValue: '{{n}} Reference Images',
                  })}
                </span>
              </div>
            </section>

            <div className="flex flex-wrap items-end gap-3">
              <label className="flex min-w-40 flex-1 flex-col gap-1">
                <span className="text-[11px] text-content-4">
                  {t('assets.gen.model', 'Model')}
                </span>
                <select
                  data-testid="generate-model"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  className="rounded-lg border border-line-strong bg-card px-2 py-1 text-[12px] text-content focus:border-accent focus:outline-none"
                >
                  {/* The catalog default is a real choice, not a blank: the
                      run omits `model` and the server resolves it. */}
                  <option value="">{t('assets.gen.modelDefault', 'Catalog Default')}</option>
                  {models.map((m) => (
                    <option key={m.name} value={m.name}>
                      {m.display_name}
                    </option>
                  ))}
                  {/* The preview's model may not be in the enabled catalog
                      (an admin can disable a row after it became the default).
                      Keeping it as an option means the picker shows what will
                      actually run instead of silently snapping to blank. */}
                  {preview.model && !models.some((m) => m.name === preview.model) && (
                    <option value={preview.model}>{preview.model}</option>
                  )}
                </select>
              </label>

              <fieldset className="flex flex-col gap-1">
                <legend className="text-[11px] text-content-4">
                  {t('assets.gen.count', 'How Many')}
                </legend>
                <div className="flex gap-1" data-testid="generate-count">
                  {Array.from({ length: MAX_COUNT - MIN_COUNT + 1 }, (_, i) => i + MIN_COUNT).map(
                    (n) => (
                      <button
                        key={n}
                        type="button"
                        data-testid="generate-count-option"
                        data-count={n}
                        aria-pressed={count === n}
                        onClick={() => setCount(n)}
                        className={`h-7 w-7 rounded-lg border text-[12px] font-medium tabular-nums ${
                          count === n
                            ? 'border-accent bg-[var(--accent-soft)] text-accent'
                            : 'border-line-strong text-content-2 hover:bg-island-2'
                        }`}
                      >
                        {n}
                      </button>
                    ),
                  )}
                </div>
              </fieldset>
            </div>
          </>
        ) : null}

        {result && (
          <section
            data-testid="generate-result"
            className="flex flex-col gap-1.5 rounded-lg border border-line bg-island-2 px-2.5 py-2"
          >
            <p data-testid="result-count" className="text-[12px] text-content-2">
              {t('assets.gen.produced', {
                n: result.generation_ids.length,
                defaultValue: '{{n}} Images Are In The Generated Inbox',
              })}
            </p>

            <FailedUnits units={result.failed} testId="result-failed" />

            <SkippedRefs refs={result.skipped_references} testId="result-skipped" />

            {attached && (
              <p data-testid="result-attached" className="text-[11px] text-content-3">
                {t('assets.gen.attachOutcome', {
                  ok: attached.ok,
                  bad: attached.failed.length,
                  defaultValue: '{{ok}} attached, {{bad}} refused',
                })}
              </p>
            )}

            <div className="flex flex-wrap items-center gap-2 pt-0.5">
              <button
                type="button"
                data-testid="open-generated-inbox"
                onClick={onOpenInbox}
                className="text-[11px] font-medium text-accent hover:underline"
              >
                {t('assets.gen.openInbox', 'Review In Generated')}
              </button>
              <button
                type="button"
                data-testid="attach-now"
                disabled={attaching || result.generation_ids.length === 0}
                onClick={() => void attachNow()}
                className="inline-flex items-center gap-1 rounded-lg border border-line-strong px-2 py-0.5 text-[11px] font-medium text-content-2 hover:bg-island-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {attaching && <Loader2 size={11} className="animate-spin" aria-hidden="true" />}
                {t('assets.gen.attachNow', 'Attach Now')}
              </button>
            </div>
          </section>
        )}

        {refusal?.phase === 'run' && (
          <section
            role="alert"
            data-testid="generate-refused"
            data-code={refusal.code ?? ''}
            className="flex flex-col gap-1 rounded-lg border border-danger-line bg-danger-soft px-2.5 py-1.5 text-[11px] text-danger"
          >
            <p>
              {t('assets.gen.nothingGenerated', 'Nothing Was Generated')}{' '}
              {refusal.code
                ? t(`assets.err.${refusal.code}`, t('assets.err.generic'))
                : t('assets.err.generic')}
            </p>

            {/* The provider's own sentence, ALONGSIDE the mapped one rather
                than as its fallback: `generation_failed` IS mapped, so a
                fallback would never render and "insufficient balance" would
                stay in the console - which is exactly how this shipped. */}
            {refusal.detail && (
              <p data-testid="refused-detail" className="text-content-2">
                {refusal.detail}
              </p>
            )}

            {/* Same two renderers the 202 uses. The outcome that spent the
                most must not explain the least. */}
            <FailedUnits units={refusal.failed} testId="refused-failed" />
            <SkippedRefs refs={refusal.skipped} testId="refused-skipped" />
          </section>
        )}

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-line-strong px-2.5 py-1 text-xs font-medium text-content-2 hover:bg-island-2"
          >
            {t('common.close', 'Close')}
          </button>
          <button
            type="button"
            data-testid="generate-submit"
            disabled={running || preview === null}
            onClick={() => void run()}
            className="inline-flex items-center gap-1 rounded-lg border border-accent bg-accent px-2.5 py-1 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {running ? (
              <Loader2 size={12} className="animate-spin" aria-hidden="true" />
            ) : (
              <Sparkles size={12} aria-hidden="true" />
            )}
            {t('assets.gen.run', 'Generate')}
          </button>
        </div>
      </div>
    </UiModal>
  );
};
