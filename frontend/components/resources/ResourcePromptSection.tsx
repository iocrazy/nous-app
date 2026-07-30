/**
 * ResourcePromptSection — self-contained mount wrapper around PromptSection.
 *
 * PromptSection takes a Resource plus a handful of callbacks and has no data
 * access of its own for patch/translate/ensure-trigger-tag (spec 2026-07-26-
 * asset-prompt-management) — but it self-manages the Generate dispatch and
 * progress tracking (spec 2026-07-28-prompt-dataline). ResourceDetailPage
 * wires the callbacks against page-level state it already owns. The other
 * three surfaces (upload list sidebar, download list sidebar, download
 * detail card) don't have that state lying around, so this wrapper
 * fetches/owns it itself — mount it with just a `resourceId` and it takes
 * care of the rest, including Generate for image resources.
 *
 * ── Slide mode (2026-07-29) ──────────────────────────────────────────────
 * Pass `slideName` and the block edits ONE slide of a download album instead
 * of the resource's own `gen_prompt*` columns. The album's per-slide prompts
 * live in a single JSONB column on the PARENT resource (`slide_prompts:
 * Record<slideName, {en, zh, neg_en, neg_zh}>`), fetched with everything else
 * in the one row read — so paging through slides never refetches, and the
 * displayed entry is always re-derived from the freshest map (including
 * slides edited earlier in the same session).
 *
 * This replaces the on-image SlidePromptStrip overlay: the user asked for the
 * prompt to stay in its usual place on the right and just follow the slide.
 *
 * Two invariants carried over from that overlay:
 *   - Saving PATCHes the WHOLE map (`{...map, [slideName]: entry}`), never a
 *     bare `{[slideName]: entry}`, so other slides are never clobbered. That
 *     only holds because a failed row read renders nothing at all (see the
 *     `!resource` guard) rather than defaulting the map to `{}`.
 *   - An unsaved draft on slide A is never written to slide B — PromptSection's
 *     `editorScopeKey` re-binds its editors on every slide change.
 */
import { useCallback, useEffect, useState } from 'react';
import { Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { supabase } from '../../supabaseClient';
import {
  addResourceTag, fetchResourceTags, generateSlidePrompt, translateGenPrompt, updateResource,
} from '../../services/resourceService';
import { fetchAllTags } from '../../services/unifiedTagService';
import { ensureDefaultTriggerTag } from '../../utils/promptTriggerTags';
import type { Resource, Tag } from '../../types';
import { PromptSection } from './PromptSection';

// `slide_prompts` is fetched unconditionally rather than only in slide mode:
// making the select depend on `slideName` would refetch the row the moment the
// viewer reports its first slide, and the column is null on everything that
// isn't a download album.
const PROMPT_FIELDS =
  'id, filename, file_type, mime_type, media_id, gen_prompt, gen_prompt_zh, gen_prompt_negative, gen_prompt_negative_zh, gen_prompt_json, slide_prompts';

type PromptResource = Pick<
  Resource,
  | 'id' | 'filename' | 'file_type' | 'mime_type' | 'media_id' | 'gen_prompt' | 'gen_prompt_zh'
  | 'gen_prompt_negative' | 'gen_prompt_negative_zh' | 'gen_prompt_json' | 'slide_prompts'
>;

type SlidePromptsMap = NonNullable<Resource['slide_prompts']>;
type SlidePromptEntry = SlidePromptsMap[string];

/** slide entry ⇄ the `gen_prompt*` shape PromptSection edits. One place, so
 *  the read mapping and the write mapping can't drift apart. */
const SLIDE_FIELD_OF: Record<string, keyof SlidePromptEntry> = {
  gen_prompt: 'en',
  gen_prompt_zh: 'zh',
  gen_prompt_negative: 'neg_en',
  gen_prompt_negative_zh: 'neg_zh',
};

/**
 * Can this resource be reverse-engineered as a WHOLE? Mirrors the backend's
 * `caption_gate_reason` (`app/services/ai/caption_source.py`) — keep the two
 * in step, or the button offers something the endpoint rejects.
 *
 * - image → yes, it captions its own bytes
 * - video → yes, it captions its downloaded cover still
 * - download-backed image → NO: that's an album, whose `file_path` is a
 *   directory of slides. It captions per slide — this component's slide mode
 *   drives that endpoint instead (and turns Generate back on there).
 * - anything else (audio, documents) → no
 *
 * Gated on `mime_type`, not `file_type`: for downloaded rows `file_type`
 * holds the raw platform type code ('0', '4', '68', …), so `file_type ===
 * 'video'` would miss most of the video library.
 */
export function canGenerateForResource(
  resource: Pick<PromptResource, 'file_type' | 'mime_type' | 'media_id'>,
): boolean {
  const mime = (resource.mime_type || '').toLowerCase();
  const fileType = (resource.file_type || '').toLowerCase();
  if (mime.startsWith('video/') || (!mime && fileType === 'video')) return true;
  if (mime.startsWith('image/') || (!mime && fileType === 'image')) {
    return !resource.media_id;
  }
  return false;
}

export function ResourcePromptSection({
  resourceId,
  onTagsChanged,
  sectionClassName,
  slideName,
  slideIndex,
  slideCount,
}: {
  resourceId: string;
  /** Notified after the ensure-trigger-tag flow actually writes a new tag
   *  assignment, so hosts that keep their own separate Tags-block state
   *  (ResourceInfoPanel / DownloadInfoPanel / MediaCard) can refresh it. */
  onTagsChanged?: () => void;
  /** Outer wrapper classes, forwarded to PromptSection. Hosts whose own
   *  container already pads its children override the default `px-4 mt-3`
   *  so the block lines up with its neighbours. */
  sectionClassName?: string;
  /** Album detail only: the slide the viewer is currently showing. Switches
   *  the block into slide mode (see the header). The list sidebars leave it
   *  unset and keep the "open the item" hint. */
  slideName?: string;
  /** 0-based position + total, for the "Slide 2/11" header badge. */
  slideIndex?: number;
  slideCount?: number;
}) {
  const { t } = useTranslation();
  const [resource, setResource] = useState<PromptResource | null>(null);
  const [assignedTags, setAssignedTags] = useState<Array<{ tag: Tag }>>([]);
  const [loading, setLoading] = useState(true);
  const [translating, setTranslating] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setResource(null);
    setAssignedTags([]);

    (async () => {
      try {
        const [{ data: row, error }, tags] = await Promise.all([
          supabase.from('resources').select(PROMPT_FIELDS).eq('id', resourceId).single(),
          fetchResourceTags(resourceId).catch(() => []),
        ]);
        if (cancelled) return;
        if (error || !row) {
          console.error('Failed to load resource for prompt section:', error);
          return;
        }
        setResource(row as unknown as PromptResource);
        setAssignedTags(tags);
      } catch (err) {
        if (!cancelled) console.error('Failed to load resource prompt data:', err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => { cancelled = true; };
  }, [resourceId]);

  const handlePatch = useCallback((fields: Partial<Resource>) => {
    setResource((prev) => (prev ? { ...prev, ...fields } : prev));
    updateResource(resourceId, fields as Parameters<typeof updateResource>[1]).catch((err) =>
      console.error('Failed to update resource prompt:', err),
    );
  }, [resourceId]);

  // Slide mode's write path. PromptSection speaks `gen_prompt*`; this maps the
  // committed field back onto the slide's entry and PATCHes the WHOLE map, so
  // no other slide is touched. Depending on the map object (state, stable
  // between edits) rather than reading it inside a state updater keeps the
  // PATCH out of the updater — updaters can be double-invoked.
  const slidePromptsMap = resource?.slide_prompts ?? null;
  const handleSlidePatch = useCallback((fields: Partial<Resource>) => {
    if (!slideName) return;
    const entry: SlidePromptEntry = { ...(slidePromptsMap?.[slideName] || {}) };
    let touched = false;
    for (const [column, value] of Object.entries(fields)) {
      const slideField = SLIDE_FIELD_OF[column];
      if (!slideField) continue;
      entry[slideField] = typeof value === 'string' ? value : '';
      touched = true;
    }
    if (!touched) return;
    const merged: SlidePromptsMap = { ...(slidePromptsMap || {}), [slideName]: entry };
    setResource((prev) => (prev ? { ...prev, slide_prompts: merged } : prev));
    updateResource(resourceId, { slide_prompts: merged }).catch((err) =>
      console.error('Failed to save slide prompt:', err),
    );
  }, [resourceId, slideName, slidePromptsMap]);

  const dispatchSlideGenerate = useCallback(
    () => generateSlidePrompt(resourceId, slideName as string),
    [resourceId, slideName],
  );

  const handleTranslate = useCallback((lang: 'en' | 'zh') => {
    setTranslating(true);
    translateGenPrompt(resourceId, lang)
      .then((data) => setResource((prev) => (prev ? { ...prev, ...data } : prev)))
      .catch((err) => console.error('Failed to translate prompt:', err))
      .finally(() => setTranslating(false));
  }, [resourceId]);

  const handleEnsureTriggerTag = useCallback(async () => {
    const allTags = await fetchAllTags();
    const tag = await ensureDefaultTriggerTag(allTags);
    if (!assignedTags.some((it) => String(it.tag?.id) === String(tag.id))) {
      await addResourceTag(resourceId, String(tag.id));
      const updated = await fetchResourceTags(resourceId);
      setAssignedTags(updated);
      onTagsChanged?.();
    }
  }, [resourceId, assignedTags, onTagsChanged]);

  // PromptSection's self-managed Generate flow calls this once the caption
  // workflow completes — re-pull the full prompt data line (gen_prompt* +
  // gen_prompt_json) plus tags (the workflow may attach new AI tags too).
  const handleGenerated = useCallback(async () => {
    try {
      const [{ data: row, error }, tags] = await Promise.all([
        supabase.from('resources').select(PROMPT_FIELDS).eq('id', resourceId).single(),
        fetchResourceTags(resourceId).catch(() => []),
      ]);
      if (error || !row) {
        console.error('Failed to refetch resource after prompt generation:', error);
        return;
      }
      setResource(row as unknown as PromptResource);
      setAssignedTags(tags);
      onTagsChanged?.();
    } catch (err) {
      console.error('Failed to refetch resource after prompt generation:', err);
    }
  }, [resourceId, onTagsChanged]);

  if (loading || !resource) return null;

  const canGenerate = canGenerateForResource(resource);
  // An album (download-backed, so no whole-resource caption) whose host tells
  // us which slide is on screen: edit that slide's entry right here.
  const slideMode = Boolean(slideName) && !canGenerate && Boolean(resource.media_id);
  // A gallery WITHOUT a slide on screen — the list sidebars, and the detail
  // page before the viewer reports its first slide. Point at where Generate
  // does live instead of silently omitting the button.
  const effectiveGalleryHint =
    !canGenerate && !slideMode && resource.media_id
      ? t(
          'resources.infoPanel.generatePerSlideHint',
          'Galleries: open the item and generate per slide',
        )
      : undefined;

  const slideEntry: SlidePromptEntry =
    (slideName ? resource.slide_prompts?.[slideName] : null) || {};
  // The slide's entry wearing the `gen_prompt*` shape PromptSection edits.
  // `gen_prompt_json` is blanked deliberately: a slide entry has no structured
  // result, and leaving the album's own JSON here would offer a JSON tab and
  // Generate Similar built from the wrong asset.
  const slideResource: PromptResource = {
    ...resource,
    gen_prompt: slideEntry.en ?? null,
    gen_prompt_zh: slideEntry.zh ?? null,
    gen_prompt_negative: slideEntry.neg_en ?? null,
    gen_prompt_negative_zh: slideEntry.neg_zh ?? null,
    gen_prompt_json: null,
  };

  const slideBadge = slideMode ? (
    <span
      data-testid="slide-prompt-badge"
      className="px-1.5 py-0.5 rounded-full bg-ink-800 border border-ink-700 text-[9.5px] text-ink-400 normal-case tracking-normal max-w-[120px] truncate"
      title={slideName}
    >
      {typeof slideIndex === 'number' && slideCount
        ? t('resources.slidePrompt.slideBadge', 'Slide {{index}}/{{count}}', {
            index: slideIndex + 1,
            count: slideCount,
          })
        : slideName}
    </span>
  ) : undefined;

  // Gallery in resource mode (list sidebars): its resource-level gen_prompt*
  // columns are invisible on the detail page (which is slide-mode only), so an
  // editable empty block here is a trap — anything typed in gets saved to
  // fields "inside" never shows, and lights the card's has_prompt badge with
  // content the user can't find (real incident: a stray test string). Offer
  // the editor only when legacy data already exists (viewable / clearable);
  // otherwise show just the pointer to where prompts actually live.
  const galleryResourceMode = !slideMode && !canGenerate && Boolean(resource.media_id);
  const hasResourceLevelData = [
    resource.gen_prompt,
    resource.gen_prompt_zh,
    resource.gen_prompt_negative,
    resource.gen_prompt_negative_zh,
  ].some((v) => (v ?? '').trim() !== '');
  if (galleryResourceMode && !hasResourceLevelData) {
    return (
      <div className={sectionClassName ?? 'px-4 mt-3'} data-testid="gallery-prompt-hint-only">
        <div className="flex items-center gap-1.5 text-[11px] font-semibold text-content-3 uppercase tracking-widest">
          <Sparkles size={11} />
          {t('resources.infoPanel.promptSection', 'Prompt')}
        </div>
        <p className="mt-1.5 text-xs text-content-4">{effectiveGalleryHint}</p>
      </div>
    );
  }

  return (
    <PromptSection
      key={resourceId}
      resource={(slideMode ? slideResource : resource) as unknown as Resource}
      onPatch={slideMode ? handleSlidePatch : handlePatch}
      onEnsureTriggerTag={handleEnsureTriggerTag}
      canGenerate={slideMode || canGenerate}
      generateUnavailableHint={effectiveGalleryHint}
      onGenerated={handleGenerated}
      translating={translating}
      onTranslate={handleTranslate}
      sectionClassName={sectionClassName}
      {...(slideMode
        ? {
            onDispatchGenerate: dispatchSlideGenerate,
            generateTitle: t(
              'resources.slidePrompt.generateHint',
              'Reverse-engineer the prompt from this slide',
            ),
            // No per-slide translate endpoint exists — the resource-level one
            // would rewrite the album's own columns instead.
            canTranslate: false,
            headerBadge: slideBadge,
            editorScopeKey: `${resourceId}:${slideName}`,
          }
        : {})}
    />
  );
}
