/**
 * PromptSection — bilingual positive/negative AI-generation prompt block.
 *
 * Extracted from ResourceDetailPage (spec 2026-07-26-asset-prompt-management),
 * upgraded to the three-format result card + self-managed Generate flow
 * (spec 2026-07-28-prompt-dataline). Sits AFTER the Tags picker, BEFORE
 * Properties. A first-class block matching the Tags section, with three
 * states (v6.1 mockup):
 *   - no data (trigger tag or not) → section header + dashed "+ Add Prompt" pill
 *   - data present, collapsed      → section header + bordered preview card
 *   - expanded                     → section header + result card + editors
 * Expanding auto-applies the default trigger tag (onEnsureTriggerTag).
 *
 * Generate is self-managed: this component dispatches the caption workflow
 * directly, tracks the returned task via `useTaskCompletion`, and renders
 * the "analyzing" progress card itself — hosts just get an `onGenerated()`
 * callback once the workflow completes (to refetch resource + tags, since
 * the workflow may also write new AI tags). Patch/translate/ensure-trigger-
 * tag stay callback-based — hosts differ in how they hold that state.
 *
 * A failed Generate renders an in-section card (not a toast) whose copy comes
 * from `utils/errorCatalog.ts` — the backend classifies the underlying
 * provider error into `metadata.error_code`, so "Step … exceeded its maximum
 * of 3 retries" becomes "check the API key in Settings → AI". The raw engine
 * text stays available behind a Details disclosure.
 *
 * ⚡ Generate Similar (Task 5): when the analysis JSON result is present,
 * a second action sits next to Send to Canvas. It opens the same
 * SendToCanvasModal, just flagged `autoRun` with the analysis result's
 * `aspect_ratio` — CanvasComposer's promptInsert consumer merges that into
 * a `gen` block and reruns the freshly-inserted prompt node once it lands.
 */
import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, ChevronUp, Copy, Languages, Loader2, Send, Sparkles, X, Zap } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { Resource } from '../../types';
import { hasPromptData } from '../../utils/promptTriggerTags';
import { resolveTaskError, type ResolvedTaskError } from '../../utils/errorCatalog';
import { generateGenPrompt } from '../../services/resourceService';
import { useTaskCompletion } from '../../hooks/useTaskCompletion';
import { useOptionalToast } from '../Toast';
import { SendToCanvasModal } from './SendToCanvasModal';

export interface PromptSectionProps {
  resource: Resource;
  /** Optimistic local-merge + PATCH (page's handleResourceUpdate). */
  onPatch: (fields: Partial<Resource>) => void;
  /** Apply the default trigger tag to this asset (page implements). */
  onEnsureTriggerTag: () => Promise<void>;
  /** Image assets get the Generate (reverse-engineer) button. */
  canGenerate: boolean;
  /** Shown in the Generate button's place when `canGenerate` is false but
   *  generation exists elsewhere for this asset — e.g. galleries, whose
   *  per-slide Generate lives in the detail viewer's slide strip. Without
   *  it the block renders two bare textareas and reads as "not built". */
  generateUnavailableHint?: string;
  /** Fired once the self-managed Generate flow completes successfully —
   *  the host should refetch the resource (new gen_prompt* / gen_prompt_json)
   *  and its tags (the workflow may attach new AI tags too). */
  onGenerated?: () => void;
  translating: boolean;
  onTranslate: (lang: 'en' | 'zh') => void;
  /** One-shot signal (spec 2026-07-28-extension-prompt-analyze, Task 1):
   *  the `?generateSimilar=1` deep link — opened from the Chrome
   *  extension's result card ("Generate Similar in nous"), or the same
   *  URL shared elsewhere. On the first render where this flips true,
   *  auto-expand the section and, if an analysis result already exists
   *  (gen_prompt_json + a non-empty positive prompt for the current lang
   *  side — mirroring the Generate Similar button's own disabled gate),
   *  open the canvas picker immediately. Otherwise just expand and toast
   *  a hint to run Generate first. The host is responsible for stripping
   *  the query param once this prop turns true; this component only
   *  guards against firing the effect more than once (ref-guarded,
   *  StrictMode double-invoke safe — mirrors CanvasComposer's
   *  insertedRef). */
  autoOpenGenerateSimilar?: boolean;
  /**
   * Outer wrapper classes for the whole block. The default suits the sidebar
   * panels, whose sections are `px-4 mt-N` siblings at the panel root with no
   * padding of their own. MediaCard's info column already carries `p-4 sm:p-6`,
   * so it overrides this (together with a negative margin on its side) to keep
   * the block on the same left edge and vertical rhythm as its neighbours
   * instead of sitting one indent deeper.
   */
  sectionClassName?: string;
}

function ResultChip({ children }: { children: React.ReactNode }) {
  return (
    <span className="px-1.5 py-0.5 rounded-full bg-ink-800 border border-ink-700 text-[9.5px] text-ink-400">
      {children}
    </span>
  );
}

export function PromptSection({
  resource, onPatch, onEnsureTriggerTag,
  canGenerate, generateUnavailableHint, onGenerated, translating, onTranslate,
  autoOpenGenerateSimilar, sectionClassName = 'px-4 mt-3',
}: PromptSectionProps) {
  const { t } = useTranslation();
  const toast = useOptionalToast();
  const [expanded, setExpanded] = useState(false);
  const [lang, setLang] = useState<'en' | 'zh'>(() =>
    (resource.gen_prompt?.trim() ? 'en' : resource.gen_prompt_zh?.trim() ? 'zh' : 'en'),
  );
  const [showJson, setShowJson] = useState(false);
  const [posValue, setPosValue] = useState('');
  const [negValue, setNegValue] = useState('');
  const [sendToCanvasOpen, setSendToCanvasOpen] = useState(false);
  // ⚡ Generate Similar (spec 2026-07-28-prompt-dataline, Task 5): reuses
  // the same modal as plain Send to Canvas, just flagged to auto-run the
  // inserted prompt node once it lands.
  const [autoRunTarget, setAutoRunTarget] = useState(false);

  // ─── Generate — self-managed dispatch + realtime progress ───────
  const [dispatching, setDispatching] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  // Escape hatch for a task_tracking row that never got created (backend's
  // manager.create() is warn-and-continue, not a hard failure) — without
  // this, useTaskCompletion never sees a matching row and the analyzing
  // spinner + locked Generate button would spin forever.
  const genTimeoutRef = useRef<number | null>(null);
  const clearGenTimeout = () => {
    if (genTimeoutRef.current !== null) {
      window.clearTimeout(genTimeoutRef.current);
      genTimeoutRef.current = null;
    }
  };
  useEffect(() => () => clearGenTimeout(), []);

  // Failure surface. A toast was the wrong channel for this: the whole
  // problem being fixed is that the user can't act on an AI failure, and a
  // notification that disappears in a few seconds can't be read twice. The
  // resolved copy sits in the section until the next Generate, with the raw
  // engine text kept behind a Details disclosure so nothing is lost.
  const [genError, setGenError] = useState<{ resolved: ResolvedTaskError; raw: string } | null>(null);

  const { task: genTask } = useTaskCompletion(taskId, {
    onComplete: () => {
      clearGenTimeout();
      setTaskId(null);
      onGenerated?.();
    },
    onError: (task) => {
      clearGenTimeout();
      setTaskId(null);
      // metadata.error_code is written by the backend's error_catalog on the
      // workflow's failure path; error_msg is the trigger-owned raw text and
      // is only the fallback (see utils/errorCatalog.ts).
      setGenError({
        resolved: resolveTaskError(task.metadata, task.error_msg, t),
        raw: task.error_msg || '',
      });
    },
  });
  const generating = dispatching || Boolean(taskId);

  const handleGenerate = async () => {
    if (generating) return;
    setGenError(null);
    setDispatching(true);
    try {
      const id = await generateGenPrompt(resource.id);
      setTaskId(id);
      clearGenTimeout();
      genTimeoutRef.current = window.setTimeout(() => {
        genTimeoutRef.current = null;
        setTaskId(null);
        toast?.addToast(
          t('resources.infoPanel.promptGenerateSlow', 'Still generating — check Task Center'),
          'info',
        );
      }, 90000);
    } catch (err) {
      console.error('Failed to start prompt generation:', err);
      // Dispatch-time failure — no task row exists, so there is no
      // error_code to resolve; the catalog falls back to the raw text.
      const msg =
        err instanceof Error && err.message
          ? err.message
          : t('resources.infoPanel.promptGenerateFailed', 'Failed to generate prompt');
      setGenError({ resolved: resolveTaskError(null, msg, t), raw: msg });
    } finally {
      setDispatching(false);
    }
  };

  const posField = lang === 'zh' ? 'gen_prompt_zh' : 'gen_prompt';
  const negField = lang === 'zh' ? 'gen_prompt_negative_zh' : 'gen_prompt_negative';

  // Sync editors from the resource whenever id/lang/data changes.
  useEffect(() => {
    setPosValue((resource[posField] as string | null) || '');
    setNegValue((resource[negField] as string | null) || '');
  }, [resource.id, resource[posField], resource[negField], lang]);

  const dataPresent = hasPromptData(resource);
  const posPreview = ((resource[posField] as string | null) || '').trim();
  const negPreview = ((resource[negField] as string | null) || '').trim();

  const hasJson = Boolean(resource.gen_prompt_json?.trim());
  let parsedJson: Record<string, unknown> | null = null;
  let jsonPretty = '';
  if (hasJson) {
    try {
      parsedJson = JSON.parse(resource.gen_prompt_json as string);
      jsonPretty = JSON.stringify(parsedJson, null, 2);
    } catch (err) {
      console.error('Failed to parse gen_prompt_json:', err);
      jsonPretty = resource.gen_prompt_json as string;
    }
  }
  const category = typeof parsedJson?.category === 'string' ? parsedJson.category : '';
  const aspectRatio = typeof parsedJson?.aspect_ratio === 'string' ? parsedJson.aspect_ratio : '';

  const expand = () => {
    setExpanded(true);
    // Fire-and-forget: tag failure must not block editing.
    onEnsureTriggerTag().catch((err) => console.error('ensureTriggerTag:', err));
  };

  // ─── generateSimilar deep link (Task 1, spec 2026-07-28-extension-prompt-analyze) ───
  // Ref-guarded to fire exactly once per prop transition to true — the host
  // (ResourceDetailPage) keys this component on resource.id, so a fresh
  // mount already resets the guard per resource; this only protects against
  // StrictMode's synchronous double-invoke within a single mount.
  const autoOpenedRef = useRef(false);
  useEffect(() => {
    if (!autoOpenGenerateSimilar || autoOpenedRef.current) return;
    autoOpenedRef.current = true;
    expand();
    // Mirror the Generate Similar button's own disabled gate (M3): only
    // auto-open the picker when there's an analysis result AND a non-empty
    // positive prompt for the current lang side — an empty prompt would
    // reach the canvas as an empty node and 422 the autoRun silently.
    if (hasJson && posPreview) {
      setAutoRunTarget(true);
      setSendToCanvasOpen(true);
    } else {
      toast?.addToast(
        t('resources.infoPanel.generateSimilarNeedsPrompt', 'Generate a prompt first, then try Generate Similar'),
        'info',
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoOpenGenerateSimilar]);

  const commit = (field: string, value: string, current: string) => {
    if (value.trim() !== current.trim()) onPatch({ [field]: value.trim() } as Partial<Resource>);
  };

  const copyText = (text: string) => {
    if (text) navigator.clipboard.writeText(text).catch((e) => console.error(e));
  };

  // Section header — Sparkles + "Prompt" caption, styled like the Tags
  // section header (EagleTagPicker) so the two blocks read as one family.
  const sectionHeader = (
    <div className="flex items-center gap-1.5">
      <Sparkles size={11} className="text-ink-500" />
      <h4 className="text-[11px] font-semibold text-ink-500 uppercase tracking-widest">
        {t('resources.infoPanel.promptSection', 'Prompt')}
      </h4>
    </div>
  );

  const langToggle = (
    <div className="flex rounded overflow-hidden border border-ink-700/60">
      {(['en', 'zh'] as const).map((l) => (
        <button
          key={l}
          onClick={() => setLang(l)}
          className={`px-1.5 py-0.5 text-[9px] transition-colors ${
            lang === l ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]' : 'text-ink-500 hover:text-ink-300'
          }`}
        >
          {l === 'en' ? 'EN' : '中'}
        </button>
      ))}
    </div>
  );

  // With a structured JSON result present, the expanded view's toggle grows
  // a third tab. 中文/EN keep the existing lang-toggle semantics; JSON shows
  // the parsed structured prompt read-only, with the positive/negative
  // editors underneath hidden while it's active.
  const resultTabs = (
    <div className="flex rounded overflow-hidden border border-ink-700/60">
      {([
        { key: 'zh', label: '中文' },
        { key: 'en', label: 'EN' },
        { key: 'json', label: 'JSON' },
      ] as const).map(({ key, label }) => {
        const active = key === 'json' ? showJson : !showJson && lang === key;
        return (
          <button
            key={key}
            onClick={() => {
              if (key === 'json') {
                setShowJson(true);
              } else {
                setShowJson(false);
                setLang(key);
              }
            }}
            className={`px-1.5 py-0.5 text-[9px] transition-colors ${
              active ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]' : 'text-ink-500 hover:text-ink-300'
            }`}
          >
            {label}
          </button>
        );
      })}
    </div>
  );

  // Both empty states (no data at all, whether or not a trigger tag is
  // already assigned) collapse into the same first-class block: a section
  // header plus a dashed "+ Add Prompt" pill.
  if (!expanded && !dataPresent) {
    return (
      <div className={sectionClassName}>
        <div className="mb-2">{sectionHeader}</div>
        <button
          onClick={expand}
          className="border border-dashed border-ink-600 rounded-full px-3 py-1 text-xs text-ink-400 hover:text-[var(--accent-text)] hover:border-[var(--accent-border)] hover:bg-[var(--accent-soft)] transition-colors"
        >
          + {t('resources.infoPanel.addPrompt', 'Add Prompt')}
        </button>
      </div>
    );
  }

  if (!expanded) {
    return (
      <div className={sectionClassName}>
        <div className="flex items-center justify-between mb-2">
          {sectionHeader}
          {langToggle}
        </div>
        <button
          onClick={expand}
          className="w-full text-left border border-ink-700 rounded-[10px] bg-ink-800/40 px-3 py-2.5 hover:border-[var(--accent-border)] transition-colors cursor-pointer"
        >
          {Boolean(posPreview) && (
            <div className="font-mono text-[11px] text-ink-300 line-clamp-2">{posPreview}</div>
          )}
          {Boolean(negPreview) && (
            <div className="mt-1 text-[10.5px] text-red-400/85 line-clamp-1">{negPreview}</div>
          )}
          <div className="mt-1.5 text-[9.5px] text-ink-600">
            {t('resources.infoPanel.promptExpandHint', 'Click to expand')}
          </div>
        </button>
      </div>
    );
  }

  const otherSidePos = lang === 'zh' ? resource.gen_prompt : resource.gen_prompt_zh;
  const otherSideNeg = lang === 'zh' ? resource.gen_prompt_negative : resource.gen_prompt_negative_zh;
  const copyValue = showJson ? (resource.gen_prompt_json || '') : posValue;

  // Failure card — sits above the editors rather than replacing them, so a
  // failed Generate never blocks writing the prompt by hand.
  const errorCard = genError && !generating ? (
    <div
      role="alert"
      className="mb-2 flex items-start gap-1.5 border border-red-400/30 rounded-[10px] bg-red-500/[.06] px-3 py-2.5"
    >
      <AlertTriangle size={12} className="mt-0.5 shrink-0 text-red-400" />
      <div className="min-w-0 flex-1">
        <div className="text-[11px] text-red-300">{genError.resolved.title}</div>
        {Boolean(genError.resolved.hint) && (
          <div className="mt-0.5 text-[10px] text-ink-400">{genError.resolved.hint}</div>
        )}
        {/* Only when the copy above REPLACED the raw text — without a resolved
            code the title already IS the raw error, and a disclosure would
            just repeat it. */}
        {Boolean(genError.resolved.code) && Boolean(genError.raw) && (
          <details className="mt-1">
            <summary className="cursor-pointer text-[9.5px] text-ink-600 hover:text-ink-400">
              {t('common.details', 'Details')}
            </summary>
            <div className="mt-1 font-mono text-[9.5px] text-ink-600 break-words">
              {genError.raw}
            </div>
          </details>
        )}
      </div>
      <button
        onClick={() => setGenError(null)}
        aria-label={t('common.dismiss', 'Dismiss')}
        className="text-ink-600 hover:text-ink-300 transition-colors"
      >
        <X size={11} />
      </button>
    </div>
  ) : null;

  return (
    <div className={sectionClassName}>
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          {sectionHeader}
          {hasJson ? resultTabs : langToggle}
        </div>
        <div className="flex items-center gap-2.5">
          {canGenerate && (
            <button onClick={handleGenerate} disabled={generating}
              title={t('resources.infoPanel.generatePromptHint', 'Reverse-engineer the prompt from this asset (a video uses its cover)')}
              className="flex items-center gap-1 text-[10px] text-ink-500 hover:text-[var(--accent-text)] transition-colors disabled:opacity-50">
              {generating ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />}{' '}
              {t('resources.infoPanel.generatePrompt', 'Generate')}
            </button>
          )}
          {!canGenerate && generateUnavailableHint && (
            <span className="text-[10px] text-ink-500" data-testid="generate-unavailable-hint">
              {generateUnavailableHint}
            </span>
          )}
          {Boolean(otherSidePos || otherSideNeg) && (
            <button onClick={() => onTranslate(lang)} disabled={translating}
              title={t('resources.infoPanel.translatePromptHint', 'Translate from the other language')}
              className="flex items-center gap-1 text-[10px] text-ink-500 hover:text-[var(--accent-text)] transition-colors disabled:opacity-50">
              {translating ? <Loader2 size={11} className="animate-spin" /> : <Languages size={11} />}{' '}
              {t('resources.infoPanel.translatePrompt', 'Translate')}
            </button>
          )}
          {Boolean(copyValue) && (
            <button onClick={() => copyText(copyValue)}
              className="flex items-center gap-1 text-[10px] text-ink-500 hover:text-[var(--accent-text)] transition-colors">
              <Copy size={11} /> {t('resources.infoPanel.copyPrompt', 'Copy')}
            </button>
          )}
          <button onClick={() => setExpanded(false)}
            className="text-ink-500 hover:text-ink-300 transition-colors">
            <ChevronUp size={12} />
          </button>
        </div>
      </div>

      {errorCard}

      {generating ? (
        <div className="border border-ink-700 rounded-[10px] bg-ink-800/40 px-3 py-2.5">
          <div className="flex items-center gap-1.5 text-[11px] text-ink-300">
            <Loader2 size={12} className="animate-spin text-[var(--accent-text)]" />
            {t('resources.infoPanel.promptAnalyzing', 'Analyzing...')}
            {genTask?.progress ? (
              <span className="ml-auto text-[10px] text-ink-500">{genTask.progress}%</span>
            ) : null}
          </div>
          <div className="mt-2 h-1 bg-ink-800 rounded-full overflow-hidden">
            <div
              className="h-full rounded-full bg-indigo-500 transition-all duration-300"
              style={{ width: `${Math.max(genTask?.progress ?? 0, 4)}%` }}
            />
          </div>
          {genTask?.subtitle && (
            <div className="mt-1 text-[10px] text-ink-600">{genTask.subtitle}</div>
          )}
        </div>
      ) : showJson ? (
        <div>
          {Boolean(category || aspectRatio) && (
            <div className="flex items-center gap-1.5 mb-1.5">
              {Boolean(category) && <ResultChip>{category}</ResultChip>}
              {Boolean(aspectRatio) && <ResultChip>{aspectRatio}</ResultChip>}
            </div>
          )}
          <pre className="w-full max-h-64 overflow-y-auto overflow-x-auto bg-ink-800/50 border border-ink-700/50 rounded-lg px-2.5 py-2 text-[11px] font-mono text-ink-300 whitespace-pre-wrap break-words">
            {jsonPretty}
          </pre>
        </div>
      ) : (
        <>
          <textarea
            value={posValue}
            onChange={(e) => setPosValue(e.target.value)}
            onBlur={() => commit(posField, posValue, (resource[posField] as string | null) || '')}
            placeholder={t('resources.infoPanel.promptPlaceholder', 'Paste the AI generation prompt...')}
            rows={4}
            maxLength={20000}
            className="w-full bg-ink-800/50 border border-ink-700/50 rounded-lg px-2.5 py-2 text-xs font-mono text-ink-300 placeholder-ink-600 focus:outline-none focus:border-indigo-500/50 resize-none"
          />
          <div className="flex items-center justify-between mt-1.5 mb-1">
            <span className="text-[10px] text-red-400/85 uppercase tracking-wider">
              {t('resources.infoPanel.negativePrompt', 'Negative')}
            </span>
            {Boolean(negValue) && (
              <button onClick={() => copyText(negValue)}
                className="flex items-center gap-1 text-[10px] text-ink-500 hover:text-[var(--accent-text)] transition-colors">
                <Copy size={11} /> {t('resources.infoPanel.copyPrompt', 'Copy')}
              </button>
            )}
          </div>
          <textarea
            value={negValue}
            onChange={(e) => setNegValue(e.target.value)}
            onBlur={() => commit(negField, negValue, (resource[negField] as string | null) || '')}
            placeholder={t('resources.infoPanel.negativePromptPlaceholder', 'Negative prompt (what to avoid)...')}
            rows={2}
            maxLength={20000}
            className="w-full bg-red-500/[.06] border border-red-400/25 rounded-lg px-2.5 py-2 text-xs font-mono text-ink-300 placeholder-ink-600 focus:outline-none focus:border-red-400/50 resize-none"
          />
        </>
      )}
      {dataPresent && (
        <div className="flex justify-end items-center gap-3 mt-2">
          {hasJson && (
            <button
              onClick={() => {
                setAutoRunTarget(true);
                setSendToCanvasOpen(true);
              }}
              disabled={!posValue.trim()}
              title={
                posValue.trim()
                  ? undefined
                  : t('resources.infoPanel.generateSimilarEmptyHint', 'Add a positive prompt first')
              }
              className="flex items-center gap-1.5 text-[10px] text-ink-500 hover:text-[var(--accent-text)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:text-ink-500"
            >
              <Zap size={11} /> {t('resources.infoPanel.generateSimilar', 'Generate Similar')}
            </button>
          )}
          <button
            onClick={() => {
              setAutoRunTarget(false);
              setSendToCanvasOpen(true);
            }}
            className="flex items-center gap-1.5 text-[10px] text-ink-500 hover:text-[var(--accent-text)] transition-colors"
          >
            <Send size={11} /> {t('resources.infoPanel.sendToCanvas', 'Send to Canvas')}
          </button>
        </div>
      )}
      {sendToCanvasOpen && (
        <SendToCanvasModal
          resource={resource}
          positive={posValue}
          negative={negValue.trim() ? negValue : null}
          onClose={() => setSendToCanvasOpen(false)}
          autoRun={autoRunTarget || undefined}
          ratio={autoRunTarget ? (aspectRatio || undefined) : undefined}
        />
      )}
    </div>
  );
}
