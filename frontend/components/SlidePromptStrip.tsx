/**
 * SlidePromptStrip — per-slide prompt editor for download albums (spec
 * 2026-07-28-prompt-dataline §2). Mounted as a sibling of SlidePlayer's
 * bottom gradient bar (only when the album has a resolved `resourceId`).
 *
 * Data model: the PARENT resource carries one JSONB column,
 * `slide_prompts: Record<slideName, {en, zh, neg_en, neg_zh}>`. This
 * component fetches that whole map once per resourceId (cheap — one small
 * JSONB column) and re-derives the current slide's entry client-side on
 * every `slideName` change, so switching slides never re-fetches and always
 * reflects the latest saved state (including slides edited earlier in the
 * same session).
 *
 * Three states:
 *   - no entry for this slide  → translucent bottom bar, "+ Add prompt..."
 *   - entry present, collapsed → bar shows first line + Expand/Copy
 *   - expanded                 → floating card OVER the image: lang toggle
 *                                 (en/zh) + positive/negative textareas + Save
 *
 * Save always PATCHes the WHOLE `slide_prompts` object (`{...map, [slideName]:
 * entry}`) — never a bare `{[slideName]: entry}` — so other slides' entries
 * are never clobbered by a partial write. That promise only holds if the
 * initial fetch actually landed: on a read failure the component renders an
 * error state (loadFailed) instead of defaulting the map to `{}`, so a
 * save can never PATCH an empty object over every other slide's real
 * entries.
 *
 * ⚡ Generate (2026-07-29): the same reverse-engineering the resource-level
 * PromptSection offers, scoped to one slide — an album's `file_path` is a
 * DIRECTORY, so there is no single image to caption and the whole-resource
 * Generate can't work on it. Dispatch → track the task via `useTaskCompletion`
 * → refetch the map on completion. The backend merges server-side (see
 * `merge_slide_prompt_map`), so this component only has to re-read.
 *
 * Regenerating over an existing entry OVERWRITES it without a confirm. Two
 * things make that acceptable rather than destructive: the merge is per-FIELD,
 * so a hand-written negative prompt survives (the caption contract has no
 * negative side), and the action is one explicit click on a per-slide control.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, ChevronDown, Copy, Loader2, X, Zap } from 'lucide-react';
import { supabase } from '../supabaseClient';
import { generateSlidePrompt, updateResource } from '../services/resourceService';
import { useTaskCompletion } from '../hooks/useTaskCompletion';
import { resolveTaskError, type ResolvedTaskError } from '../utils/errorCatalog';
import { useOptionalToast } from './Toast';
import type { Resource } from '../types';

type SlidePromptEntry = NonNullable<Resource['slide_prompts']>[string];
type SlidePromptsMap = NonNullable<Resource['slide_prompts']>;

export interface SlidePromptStripProps {
  resourceId: string;
  slideName: string;
}

/** The one read of the column, shared by the initial load and the
 *  post-Generate refresh so the two can never drift apart. Throws — the
 *  two callers want different failure handling (block vs. log). */
async function fetchSlidePrompts(resourceId: string): Promise<SlidePromptsMap> {
  const { data, error } = await supabase
    .from('resources')
    .select('slide_prompts')
    .eq('id', resourceId)
    .single();
  if (error) throw error;
  return (data?.slide_prompts as SlidePromptsMap | null) || {};
}

/**
 * Vertical anchor for both the collapsed strip and the expanded editor.
 *
 * SlidePlayer's bottom gradient bar (indicator dots + mute toggle + slide
 * counter) is ~100px tall. The original `bottom-16` (64px) put this strip
 * INSIDE it: the pill sat on top of the dots, over a `from-black/70` gradient,
 * which is why the per-slide prompt affordance read as "missing" on the
 * gallery detail page even though it was mounted and rendering. `bottom-28`
 * (112px) clears the bar so the strip reads as its own row under the slide.
 */
const STRIP_ANCHOR = 'absolute bottom-28 left-0 right-0 px-3';

export function SlidePromptStrip({ resourceId, slideName }: SlidePromptStripProps) {
  const { t } = useTranslation();
  const toast = useOptionalToast();
  const [slidePrompts, setSlidePrompts] = useState<SlidePromptsMap>({});
  const [loading, setLoading] = useState(true);
  // Set only on a read failure — while true, expand/save stay disabled so a
  // save can never PATCH an empty `{}` over every other slide's real
  // entries (the map here never actually held the real data).
  const [loadFailed, setLoadFailed] = useState(false);
  // Bumping this re-runs the fetch effect — a transient read failure would
  // otherwise leave the whole album stuck in the error state (M5).
  const [reloadKey, setReloadKey] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const [lang, setLang] = useState<'en' | 'zh'>('en');
  const [posValue, setPosValue] = useState('');
  const [negValue, setNegValue] = useState('');
  const [saving, setSaving] = useState(false);

  // ─── Generate — self-managed dispatch + realtime completion ─────
  // Mirrors PromptSection's flow, minus the progress card: a slide strip is
  // ~28px of chrome over the image, so it shows a spinner and stays out of
  // the way rather than growing a progress bar over the artwork.
  const [dispatching, setDispatching] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  // Which slide the in-flight run belongs to. The watch deliberately SURVIVES
  // browsing to another slide — the user starts a caption and keeps swiping,
  // and dropping the watch there would mean the finished prompt never lands
  // in the map until the component remounts. Both the spinner and the error
  // card are gated on this matching the visible slide instead, so a run for
  // slide 1 never decorates slide 2's strip.
  const [pendingSlide, setPendingSlide] = useState<string | null>(null);
  const [genError, setGenError] = useState<
    { slide: string; resolved: ResolvedTaskError; raw: string } | null
  >(null);
  const generating = (dispatching || Boolean(taskId)) && pendingSlide === slideName;
  const slideError = genError && genError.slide === slideName ? genError : null;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadFailed(false);
    (async () => {
      try {
        const map = await fetchSlidePrompts(resourceId);
        if (!cancelled) setSlidePrompts(map);
      } catch (err) {
        if (!cancelled) {
          console.error('Failed to load slide_prompts:', err);
          setLoadFailed(true);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [resourceId, reloadKey]);

  // Silent re-read after a successful Generate. Deliberately NOT a reloadKey
  // bump: that path flips `loading`, and `loading` unmounts the whole strip,
  // so the control the user just clicked would blink out at the exact moment
  // its result arrives.
  const refreshPrompts = useCallback(async () => {
    try {
      setSlidePrompts(await fetchSlidePrompts(resourceId));
    } catch (err) {
      console.error('Failed to refresh slide_prompts after generation:', err);
    }
  }, [resourceId]);

  useTaskCompletion(taskId, {
    onComplete: () => {
      setTaskId(null);
      refreshPrompts();
    },
    onError: (task) => {
      setTaskId(null);
      // metadata.error_code is the backend's classification; error_msg is the
      // trigger-owned raw text and only the fallback (see utils/errorCatalog).
      setGenError({
        slide: pendingSlide || slideName,
        resolved: resolveTaskError(task.metadata, task.error_msg, t),
        raw: task.error_msg || '',
      });
    },
  });

  const handleGenerate = async () => {
    if (generating || dispatching) return;
    setGenError(null);
    setPendingSlide(slideName);
    setDispatching(true);
    const target = slideName;
    try {
      setTaskId(await generateSlidePrompt(resourceId, target));
    } catch (err) {
      console.error('Failed to start slide prompt generation:', err);
      // Dispatch-time failure — no task row exists, so there is no error_code
      // to resolve and the catalog falls back to the raw text. That text is
      // the endpoint's `detail`, which is where the actionable gate reasons
      // ("Only image slides…", "…not found") live.
      const msg =
        err instanceof Error && err.message
          ? err.message
          : t('resources.slidePrompt.generateFailed', 'Failed to generate prompt');
      setGenError({ slide: target, resolved: resolveTaskError(null, msg, t), raw: msg });
    } finally {
      setDispatching(false);
    }
  };

  // Collapse the editor whenever the user browses to a different slide —
  // an open editor should never silently keep editing the previous slide.
  useEffect(() => {
    setExpanded(false);
  }, [slideName]);

  if (loading) return null;

  if (loadFailed) {
    return (
      <div className={`${STRIP_ANCHOR} z-10`}>
        <div className="bg-black/55 backdrop-blur-sm rounded-lg px-3 py-1.5 text-[10px] text-red-400/85 flex items-center justify-between gap-2">
          <span>{t('resources.slidePrompt.loadFailed', 'Failed to load prompt for this slide')}</span>
          <button
            onClick={() => setReloadKey((k) => k + 1)}
            className="shrink-0 text-ink-300 hover:text-white transition-colors"
          >
            {t('common.retry', 'Retry')}
          </button>
        </div>
      </div>
    );
  }

  const entry: SlidePromptEntry = slidePrompts[slideName] || {};
  const hasEntry = Boolean(entry.en?.trim() || entry.zh?.trim());
  const previewText = (entry.en?.trim() || entry.zh?.trim() || '');

  const openEditor = () => {
    const initialLang: 'en' | 'zh' = entry.en?.trim() ? 'en' : entry.zh?.trim() ? 'zh' : 'en';
    setLang(initialLang);
    setPosValue((initialLang === 'zh' ? entry.zh : entry.en) || '');
    setNegValue((initialLang === 'zh' ? entry.neg_zh : entry.neg_en) || '');
    setExpanded(true);
  };

  const switchLang = (next: 'en' | 'zh') => {
    setLang(next);
    setPosValue((next === 'zh' ? entry.zh : entry.en) || '');
    setNegValue((next === 'zh' ? entry.neg_zh : entry.neg_en) || '');
  };

  const copyText = (text: string) => {
    if (text) navigator.clipboard.writeText(text).catch((err) => console.error('Copy failed:', err));
  };

  const handleSave = async () => {
    if (saving) return;
    setSaving(true);
    const posField = lang === 'zh' ? 'zh' : 'en';
    const negField = lang === 'zh' ? 'neg_zh' : 'neg_en';
    const newEntry: SlidePromptEntry = {
      ...entry,
      [posField]: posValue.trim(),
      [negField]: negValue.trim(),
    };
    // Whole-object merge — PATCH carries every other slide's entry untouched,
    // only this slide's key is replaced.
    const merged: SlidePromptsMap = { ...slidePrompts, [slideName]: newEntry };
    try {
      await updateResource(resourceId, { slide_prompts: merged });
      setSlidePrompts(merged);
      setExpanded(false);
    } catch (err) {
      console.error('Failed to save slide prompt:', err);
      toast?.addToast(t('resources.slidePrompt.saveFailed', 'Failed to save'), 'error');
    } finally {
      setSaving(false);
    }
  };

  // ⚡ — present in BOTH collapsed states. On an empty slide it is the whole
  // point of the strip; on a filled one it regenerates (overwriting the
  // positive sides, keeping any hand-written negative — see the header).
  const generateButton = (
    <button
      type="button"
      onClick={handleGenerate}
      disabled={generating}
      title={
        hasEntry
          ? t('resources.slidePrompt.regenerateHint', 'Regenerate — replaces this slide’s prompt')
          : t('resources.slidePrompt.generateHint', 'Reverse-engineer the prompt from this slide')
      }
      aria-label={t('resources.slidePrompt.generate', 'Generate')}
      className="shrink-0 flex items-center gap-1 text-[10px] text-white/70 hover:text-white transition-colors disabled:opacity-50"
    >
      {generating ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
    </button>
  );

  // Failure copy sits IN the strip rather than in a toast: the whole point of
  // the error catalog is that the user can act on it, and a notification that
  // disappears in a few seconds can't be read twice.
  const errorRow = slideError ? (
    <div
      role="alert"
      className="mb-1 flex items-start gap-1.5 bg-black/70 backdrop-blur-sm border border-red-400/30 rounded-lg px-2.5 py-1.5"
    >
      <AlertTriangle size={11} className="mt-0.5 shrink-0 text-red-400" />
      <div className="min-w-0 flex-1">
        <div className="text-[10px] text-red-300">{slideError.resolved.title}</div>
        {Boolean(slideError.resolved.hint) && (
          <div className="mt-0.5 text-[9.5px] text-white/60">{slideError.resolved.hint}</div>
        )}
      </div>
      <button
        type="button"
        onClick={() => setGenError(null)}
        aria-label={t('common.dismiss', 'Dismiss')}
        className="shrink-0 text-white/50 hover:text-white transition-colors"
      >
        <X size={11} />
      </button>
    </div>
  ) : null;

  if (!expanded) {
    return (
      <div className={`${STRIP_ANCHOR} z-10`}>
        {errorRow}
        {hasEntry ? (
          <div className="flex items-center gap-2 bg-black/55 backdrop-blur-sm rounded-lg px-3 py-1.5">
            <span className="flex-1 min-w-0 truncate text-xs text-white/85 font-mono">
              {generating
                ? t('resources.slidePrompt.generating', 'Analyzing slide...')
                : previewText}
            </span>
            {generateButton}
            <button
              type="button"
              onClick={openEditor}
              className="shrink-0 text-[10px] text-white/70 hover:text-white transition-colors"
            >
              {t('resources.slidePrompt.expand', 'Expand')}
            </button>
            <button
              type="button"
              onClick={() => copyText(previewText)}
              className="shrink-0 text-white/70 hover:text-white transition-colors"
              aria-label={t('resources.slidePrompt.copy', 'Copy')}
            >
              <Copy size={12} />
            </button>
          </div>
        ) : generating ? (
          <div className="inline-flex items-center gap-1.5 bg-black/60 backdrop-blur-sm rounded-full px-3 py-1 text-xs text-white/85">
            <Loader2 size={12} className="animate-spin" />
            {t('resources.slidePrompt.generating', 'Analyzing slide...')}
          </div>
        ) : (
          // Dashed pill + full-strength label, mirroring PromptSection's
          // "+ Add Prompt" empty state. The previous `text-white/60` with no
          // border dissolved into the gradient behind it and didn't read as a
          // control at all. The literal "+ " (rather than a Plus icon) is
          // deliberate — it keeps the M1 double-plus guard meaningful and
          // matches PromptSection's own pill.
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={openEditor}
              className="bg-black/60 backdrop-blur-sm border border-dashed border-white/35 rounded-full px-3 py-1 text-xs text-white/85 hover:text-white hover:border-white/60 transition-colors"
            >
              + {t('resources.slidePrompt.addPrompt', 'Add prompt for this slide')}
            </button>
            <div className="bg-black/60 backdrop-blur-sm rounded-full px-2.5 py-1.5 flex items-center">
              {generateButton}
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="absolute inset-x-3 bottom-28 z-20 bg-ink-900/95 backdrop-blur border border-ink-700 rounded-xl p-2.5 shadow-lg">
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex rounded overflow-hidden border border-ink-700/60">
          {(['en', 'zh'] as const).map((l) => (
            <button
              key={l}
              type="button"
              onClick={() => switchLang(l)}
              className={`px-1.5 py-0.5 text-[9px] transition-colors ${
                lang === l ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]' : 'text-ink-500 hover:text-ink-300'
              }`}
            >
              {l === 'en' ? 'EN' : '中'}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => setExpanded(false)}
          className="text-ink-500 hover:text-ink-300 transition-colors"
          aria-label={t('resources.slidePrompt.close', 'Close')}
        >
          <ChevronDown size={13} />
        </button>
      </div>

      <textarea
        value={posValue}
        onChange={(e) => setPosValue(e.target.value)}
        placeholder={t('resources.slidePrompt.positivePlaceholder', 'Prompt for this slide...')}
        rows={2}
        maxLength={20000}
        className="w-full bg-ink-800/50 border border-ink-700/50 rounded-lg px-2 py-1.5 text-[11px] font-mono text-ink-300 placeholder-ink-600 focus:outline-none focus:border-indigo-500/50 resize-none"
      />
      <div className="mt-1 text-[9.5px] text-red-400/85 uppercase tracking-wider">
        {t('resources.slidePrompt.negative', 'Negative')}
      </div>
      <textarea
        value={negValue}
        onChange={(e) => setNegValue(e.target.value)}
        placeholder={t('resources.slidePrompt.negativePlaceholder', 'Negative prompt (what to avoid)...')}
        rows={1}
        maxLength={20000}
        className="w-full mt-0.5 bg-red-500/[.06] border border-red-400/25 rounded-lg px-2 py-1.5 text-[11px] font-mono text-ink-300 placeholder-ink-600 focus:outline-none focus:border-red-400/50 resize-none"
      />

      <div className="flex justify-end mt-1.5">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-1 px-2.5 py-1 text-[10px] rounded-md bg-[var(--accent-soft)] text-[var(--accent-text)] hover:opacity-90 transition-opacity disabled:opacity-50"
        >
          {saving && <Loader2 size={10} className="animate-spin" />}
          {t('resources.slidePrompt.save', 'Save')}
        </button>
      </div>
    </div>
  );
}

export default SlidePromptStrip;
