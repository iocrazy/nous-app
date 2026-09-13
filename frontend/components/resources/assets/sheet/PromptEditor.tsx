// frontend/components/resources/assets/sheet/PromptEditor.tsx
//
// The prompt half of every sheet: positive / negative, EN and ZH, with the two
// agent actions the API exposes (Translate, Regenerate).
//
// On a `prompt` asset it also REPLACES the board, and grows the three things
// only that type has: the placeholder panel it declares in `attrs`, its
// platform params, and its Examples pins. That is the whole of "screen 4b" -
// the rest of the sheet is unchanged.
//
// Editing rules worth keeping:
//
//  * A FIELD SAVES ONLY WHAT CHANGED. `AssetUpdate` is `exclude_unset`, so a
//    PATCH built from the whole form would turn "untouched" into "rewritten"
//    for three fields the user never opened. Each textarea PATCHes its own
//    key, and only when its value actually moved.
//  * BLANKING A FIELD SENDS `null`, NOT `""`. Null is how the API clears a
//    column; `""` would store an empty string that reads as "there is a
//    prompt, and it is empty".
//  * THE AGENT ACTIONS ANSWER WITH THE WRITTEN ASSET. Both endpoints return
//    the full detail row, so the sheet takes their answer instead of guessing
//    what they wrote - and their typed refusals (503 `translate_unavailable`,
//    422 `nothing_to_translate`, `no_primary_file`, ...) surface through the
//    shared reporter rather than being swallowed into "nothing happened".

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Languages, Loader2, Sparkles } from 'lucide-react';

import {
  regeneratePrompt,
  translatePrompt,
  updateAsset,
} from '../../../../services/assetsService';
import type { AssetRowDetail, AssetUpdateBody } from '../../../../services/assetsService';
import { getResourceCoverUrl } from '../../../../services/resourceService';
import { slotLabelKey } from '../assetTypeMeta';
import {
  deepEqual,
  filesForSlot,
  platformParamRows,
  platformParamsFromRows,
  promptPlaceholders,
  type PlatformParamRow,
} from './assetSheetModel';
import { PinLightbox } from './PinLightbox';

type PromptLang = 'en' | 'zh';

export interface PromptEditorProps {
  scopeId: string;
  detail: AssetRowDetail;
  readOnly: boolean;
  /** `prompt` assets only: placeholders, platform params, Examples pins. */
  showPromptExtras: boolean;
  /** A write answered with a full detail row (the two agent endpoints do). */
  onDetailUpdated: (detail: AssetRowDetail) => void;
  /** A write answered with a plain row - the sheet re-fetches. */
  onChanged: () => void;
  onError: (err: unknown) => void;
}

const TEXTAREA =
  'w-full resize-y rounded-lg border border-line-strong bg-card px-2.5 py-2 text-[13px] ' +
  'leading-relaxed text-content placeholder:text-content-4 focus:border-accent focus:outline-none ' +
  'disabled:cursor-not-allowed disabled:opacity-70';

/**
 * How tall a prompt field may grow on its own, in px (~24 lines).
 *
 * A cap, not a preference: these fields sit above the platform-params table
 * and the Examples row, and one very long preset with no ceiling would push
 * both off the bottom of the page. Past the cap the field scrolls — and the
 * `resize-y` handle still opens it further, because the auto-size writes
 * `min-height`, which a drag can always exceed.
 */
const FIELD_MAX_AUTO_PX = 480;

export const PromptEditor: React.FC<PromptEditorProps> = ({
  scopeId,
  detail,
  readOnly,
  showPromptExtras,
  onDetailUpdated,
  onChanged,
  onError,
}) => {
  const { t } = useTranslation();
  const [lang, setLang] = useState<PromptLang>('en');
  const [busy, setBusy] = useState<'translate' | 'regenerate' | null>(null);
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);

  const positiveKey = lang === 'en' ? 'prompt_positive' : 'prompt_positive_zh';
  const negativeKey = lang === 'en' ? 'prompt_negative' : 'prompt_negative_zh';

  const patch = useCallback(
    async (body: AssetUpdateBody) => {
      try {
        await updateAsset(scopeId, detail.id, body);
        onChanged();
      } catch (err) {
        onError(err);
      }
    },
    [scopeId, detail.id, onChanged, onError],
  );

  const runAgent = useCallback(
    async (action: 'translate' | 'regenerate') => {
      if (busy) return;
      setBusy(action);
      try {
        const updated =
          action === 'translate'
            ? // The target is the language NOT currently shown: the button on
              // the EN tab fills in the Chinese side, which is what "Translate"
              // means from where the user is standing.
              await translatePrompt(scopeId, detail.id, lang === 'en' ? 'zh' : 'en')
            : await regeneratePrompt(scopeId, detail.id);
        onDetailUpdated(updated);
      } catch (err) {
        onError(err);
      } finally {
        setBusy(null);
      }
    },
    [busy, scopeId, detail.id, lang, onDetailUpdated, onError],
  );

  const examples = filesForSlot(detail.files, 'examples', null);
  const placeholders = promptPlaceholders(detail.attrs);

  return (
    <section data-testid="prompt-editor" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-[13px] font-medium text-content-2">
          {t('assets.sheet.prompt', 'Prompt')}
        </h3>

        <div role="tablist" className="flex overflow-hidden rounded-lg border border-line-strong">
          {(['en', 'zh'] as PromptLang[]).map((value) => (
            <button
              key={value}
              role="tab"
              type="button"
              data-testid="prompt-lang"
              data-lang={value}
              aria-selected={lang === value}
              onClick={() => setLang(value)}
              className={`px-2 py-0.5 text-[11px] font-medium transition-colors ${
                lang === value
                  ? 'bg-[var(--accent-soft)] text-accent'
                  : 'bg-card text-content-3 hover:text-content'
              }`}
            >
              {t(`assets.sheet.lang.${value}`, value === 'en' ? 'EN' : 'ZH')}
            </button>
          ))}
        </div>

        <div className="flex-1" />

        {!readOnly && (
          <>
            <button
              type="button"
              data-testid="prompt-translate"
              disabled={busy !== null}
              onClick={() => void runAgent('translate')}
              className="inline-flex items-center gap-1 rounded-lg border border-line-strong px-2 py-0.5 text-xs font-medium text-content-2 hover:bg-island-2 disabled:opacity-50"
            >
              {busy === 'translate' ? (
                <Loader2 size={11} className="animate-spin" aria-hidden="true" />
              ) : (
                <Languages size={11} aria-hidden="true" />
              )}
              {t('assets.sheet.translate', 'Translate')}
            </button>
            <button
              type="button"
              data-testid="prompt-regenerate"
              disabled={busy !== null}
              onClick={() => void runAgent('regenerate')}
              className="inline-flex items-center gap-1 rounded-lg border border-line-strong px-2 py-0.5 text-xs font-medium text-content-2 hover:bg-island-2 disabled:opacity-50"
            >
              {busy === 'regenerate' ? (
                <Loader2 size={11} className="animate-spin" aria-hidden="true" />
              ) : (
                <Sparkles size={11} aria-hidden="true" />
              )}
              {t('assets.sheet.regenerate', 'Regenerate')}
            </button>
          </>
        )}
      </div>

      <PromptField
        testId="prompt-positive"
        label={t('assets.sheet.positive', 'Positive')}
        value={detail[positiveKey]}
        readOnly={readOnly}
        onSave={(next) => void patch({ [positiveKey]: next } as AssetUpdateBody)}
      />
      <PromptField
        testId="prompt-negative"
        label={t('assets.sheet.negative', 'Negative')}
        value={detail[negativeKey]}
        readOnly={readOnly}
        onSave={(next) => void patch({ [negativeKey]: next } as AssetUpdateBody)}
      />

      {showPromptExtras && (
        <>
          {placeholders.length > 0 && (
            <div data-testid="prompt-placeholders" className="flex flex-col gap-1">
              <h4 className="text-[11px] font-medium uppercase tracking-wide text-content-4">
                {t('assets.sheet.placeholders', 'Placeholders')}
              </h4>
              <ul className="flex flex-wrap gap-1.5">
                {placeholders.map(({ name, hint }) => (
                  <li
                    key={name}
                    title={hint}
                    data-placeholder={name}
                    className="rounded-full border border-line-strong bg-island-2 px-2 py-0.5 text-[11px] text-content-2"
                  >
                    {`{{${name}}}`}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <PlatformParams
            params={detail.platform_params}
            readOnly={readOnly}
            onSave={(next) => void patch({ platform_params: next })}
          />

          <div data-testid="prompt-examples" className="flex flex-col gap-1">
            <h4 className="text-[11px] font-medium uppercase tracking-wide text-content-4">
              {t(slotLabelKey('examples'), 'Examples')}
            </h4>
            {examples.length === 0 ? (
              <p className="text-[12px] text-content-4">
                {t('assets.sheet.noExamples', 'No Examples Yet')}
              </p>
            ) : (
              <div className="grid grid-cols-[repeat(auto-fill,minmax(96px,1fr))] gap-2">
                {examples.map((file, index) => (
                  <button
                    key={file.resource_id}
                    type="button"
                    data-testid="example-pin"
                    data-resource-id={file.resource_id}
                    onClick={() => setLightboxIndex(index)}
                    className="aspect-square overflow-hidden rounded-lg border border-line bg-island-2"
                  >
                    <img
                      src={getResourceCoverUrl(file.resource_id)}
                      alt={t(slotLabelKey('examples'), 'Examples')}
                      className="h-full w-full object-cover"
                    />
                  </button>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {lightboxIndex !== null && examples.length > 0 && (
        <PinLightbox
          resourceIds={examples.map((f) => f.resource_id)}
          index={lightboxIndex}
          slotLabel={t(slotLabelKey('examples'), 'Examples')}
          onIndexChange={setLightboxIndex}
          onClose={() => setLightboxIndex(null)}
        />
      )}
    </section>
  );
};

// --- One textarea -----------------------------------------------------------

interface PromptFieldProps {
  testId: string;
  label: string;
  value: string | null;
  readOnly: boolean;
  /** `null` CLEARS the column; `""` would store an empty string. */
  onSave: (next: string | null) => void;
}

const PromptField: React.FC<PromptFieldProps> = ({ testId, label, value, readOnly, onSave }) => {
  const [draft, setDraft] = useState(value ?? '');
  const ref = useRef<HTMLTextAreaElement | null>(null);

  /**
   * Size the box to its own text, up to {@link FIELD_MAX_AUTO_PX}.
   *
   * `min-height` rather than `height` on purpose: the field keeps its
   * `resize-y` handle, and writing `height` would snap a hand-dragged box
   * back to content size on the very next keystroke.
   *
   * The reset to `0px` before measuring is the part that is easy to drop and
   * hard to notice: `scrollHeight` is never smaller than the box itself, so
   * measuring while our own min-height is still applied reads back what we
   * last wrote. Without the reset a field would grow and then never shrink
   * again when its text was deleted.
   */
  const autosize = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    el.style.minHeight = '0px';
    el.style.minHeight = `${Math.min(el.scrollHeight, FIELD_MAX_AUTO_PX)}px`;
  }, []);

  // Layout effect, not effect: this runs before paint, so a long prompt is
  // never shown at the wrong height first and then jumped to the right one.
  useLayoutEffect(autosize, [draft, autosize]);

  // The server is the source of truth: a translate or regenerate that rewrote
  // this field must show its result, not the stale draft the user is not
  // editing. Keyed on the incoming value so a re-render with the same value
  // never clobbers an in-progress edit.
  useEffect(() => {
    setDraft(value ?? '');
  }, [value]);

  const commit = () => {
    const next = draft.trim() === '' ? null : draft;
    if ((value ?? null) === next) return;
    onSave(next);
  };

  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] font-medium uppercase tracking-wide text-content-4">
        {label}
      </span>
      <textarea
        ref={ref}
        data-testid={testId}
        // `rows` is now only what an EMPTY field falls back to; every field
        // with text in it is sized by `autosize` above.
        rows={3}
        disabled={readOnly}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        className={TEXTAREA}
      />
    </label>
  );
};

// --- Platform params --------------------------------------------------------

interface PlatformParamsProps {
  params: Record<string, unknown>;
  readOnly: boolean;
  onSave: (next: Record<string, unknown>) => void;
}

/**
 * A flat key/value editor over `platform_params`.
 *
 * Two rules, both of which this panel got wrong once:
 *
 *  * A ROW THE USER DID NOT EDIT IS WRITTEN BACK AS STORED. The inputs are
 *    text, so a stored string `"4"` renders as `4` - and re-reading every row
 *    from its text on the way out would rewrite it to the NUMBER 4, retyping a
 *    value nobody touched. `platformParamsFromRows` keeps each row's stored
 *    value and only re-reads the ones whose key or text actually moved.
 *  * A BLUR THAT CHANGED NOTHING IS NOT A REQUEST. Tabbing through the panel
 *    must not PATCH the column or bump `updated_at`, the same rule the inline
 *    text fields follow.
 *
 * Only an edited row is re-read, and then only unambiguous JSON becomes a
 * non-string: `1:1` stays text, `{"steps": 30}` becomes an object. Typing a
 * bare `4` into a row therefore does make it a number - that is a value the
 * user just wrote, and the input carries no other type information.
 */
const PlatformParams: React.FC<PlatformParamsProps> = ({ params, readOnly, onSave }) => {
  const { t } = useTranslation();
  const [rows, setRows] = useState<PlatformParamRow[]>(() => platformParamRows(params));

  useEffect(() => {
    setRows(platformParamRows(params));
  }, [params]);

  const commit = (next: readonly PlatformParamRow[]) => {
    const out = platformParamsFromRows(next);
    // Nothing moved - no PATCH, no refetch, no `updated_at` bump.
    if (deepEqual(out, params ?? {})) return;
    onSave(out);
  };

  return (
    <div data-testid="platform-params" className="flex flex-col gap-1">
      <h4 className="text-[11px] font-medium uppercase tracking-wide text-content-4">
        {t('assets.sheet.platformParams', 'Platform Parameters')}
      </h4>
      {rows.length === 0 && readOnly && (
        <p className="text-[12px] text-content-4">{t('assets.sheet.noParams', 'None Set')}</p>
      )}
      {rows.map((row, index) => (
        <div key={index} className="flex items-center gap-1.5">
          <input
            aria-label={t('assets.sheet.paramName', 'Parameter')}
            data-testid="param-key"
            disabled={readOnly}
            value={row.key}
            onChange={(e) =>
              setRows((prev) =>
                prev.map((r, i) => (i === index ? { ...r, key: e.target.value } : r)),
              )
            }
            onBlur={() => commit(rows)}
            className="w-36 rounded border border-line-strong bg-card px-2 py-1 text-[12px] text-content focus:border-accent focus:outline-none disabled:opacity-70"
          />
          <input
            aria-label={t('assets.sheet.paramValue', 'Value')}
            data-testid="param-value"
            disabled={readOnly}
            value={row.value}
            onChange={(e) =>
              setRows((prev) =>
                prev.map((r, i) => (i === index ? { ...r, value: e.target.value } : r)),
              )
            }
            onBlur={() => commit(rows)}
            className="flex-1 rounded border border-line-strong bg-card px-2 py-1 text-[12px] text-content focus:border-accent focus:outline-none disabled:opacity-70"
          />
          {!readOnly && (
            <button
              type="button"
              data-testid="param-remove"
              aria-label={t('assets.sheet.removeParam', 'Remove Parameter')}
              onClick={() => {
                const next = rows.filter((_, i) => i !== index);
                setRows(next);
                commit(next);
              }}
              className="text-content-4 hover:text-danger"
            >
              &times;
            </button>
          )}
        </div>
      ))}
      {!readOnly && (
        <button
          type="button"
          data-testid="param-add"
          onClick={() =>
            // `original: null` marks a row with nothing stored to preserve.
            setRows((prev) => [...prev, { key: '', value: '', original: null }])
          }
          className="self-start rounded border border-dashed border-line-strong px-2 py-0.5 text-[11px] text-content-3 hover:text-content"
        >
          {t('assets.sheet.addParam', 'Add Parameter')}
        </button>
      )}
    </div>
  );
};
