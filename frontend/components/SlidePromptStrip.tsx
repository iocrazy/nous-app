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
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, Copy, Loader2 } from 'lucide-react';
import { supabase } from '../supabaseClient';
import { updateResource } from '../services/resourceService';
import { useOptionalToast } from './Toast';
import type { Resource } from '../types';

type SlidePromptEntry = NonNullable<Resource['slide_prompts']>[string];
type SlidePromptsMap = NonNullable<Resource['slide_prompts']>;

export interface SlidePromptStripProps {
  resourceId: string;
  slideName: string;
}

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

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadFailed(false);
    (async () => {
      try {
        const { data, error } = await supabase
          .from('resources')
          .select('slide_prompts')
          .eq('id', resourceId)
          .single();
        if (cancelled) return;
        if (error) {
          console.error('Failed to load slide_prompts:', error);
          setLoadFailed(true);
          return;
        }
        setSlidePrompts((data?.slide_prompts as SlidePromptsMap | null) || {});
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

  // Collapse the editor whenever the user browses to a different slide —
  // an open editor should never silently keep editing the previous slide.
  useEffect(() => {
    setExpanded(false);
  }, [slideName]);

  if (loading) return null;

  if (loadFailed) {
    return (
      <div className="absolute bottom-16 left-0 right-0 px-3 z-10">
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

  if (!expanded) {
    return (
      <div className="absolute bottom-16 left-0 right-0 px-3 z-10">
        {hasEntry ? (
          <div className="flex items-center gap-2 bg-black/55 backdrop-blur-sm rounded-lg px-3 py-1.5">
            <span className="flex-1 min-w-0 truncate text-xs text-white/85 font-mono">
              {previewText}
            </span>
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
        ) : (
          <button
            type="button"
            onClick={openEditor}
            className="bg-black/55 backdrop-blur-sm rounded-lg px-3 py-1.5 text-xs text-white/60 hover:text-white/90 transition-colors"
          >
            + {t('resources.slidePrompt.addPrompt', 'Add prompt for this slide')}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="absolute inset-x-3 bottom-16 z-20 bg-ink-900/95 backdrop-blur border border-ink-700 rounded-xl p-2.5 shadow-lg">
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
