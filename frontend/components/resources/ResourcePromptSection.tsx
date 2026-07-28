/**
 * ResourcePromptSection — self-contained mount wrapper around PromptSection.
 *
 * PromptSection itself is "dumb": it takes a Resource plus a handful of
 * callbacks and has no data access of its own (spec 2026-07-26-asset-prompt-
 * management). ResourceDetailPage wires those callbacks against page-level
 * state it already owns. The other three surfaces (upload list sidebar,
 * download list sidebar, download detail card) don't have that state lying
 * around, so this wrapper fetches/owns it itself — mount it with just a
 * `resourceId` and it takes care of the rest.
 *
 * Reverse-engineer ("Generate") is intentionally left off (canGenerate=false)
 * — these surfaces don't yet have the entry-point data line wired; that
 * lands in the follow-up data-line PR.
 */
import { useCallback, useEffect, useState } from 'react';
import { supabase } from '../../supabaseClient';
import { addResourceTag, fetchResourceTags, translateGenPrompt, updateResource } from '../../services/resourceService';
import { fetchAllTags } from '../../services/unifiedTagService';
import { ensureDefaultTriggerTag } from '../../utils/promptTriggerTags';
import type { Resource, Tag } from '../../types';
import { PromptSection } from './PromptSection';

const PROMPT_FIELDS =
  'id, filename, file_type, gen_prompt, gen_prompt_zh, gen_prompt_negative, gen_prompt_negative_zh';

type PromptResource = Pick<
  Resource,
  'id' | 'filename' | 'file_type' | 'gen_prompt' | 'gen_prompt_zh' | 'gen_prompt_negative' | 'gen_prompt_negative_zh'
>;

export function ResourcePromptSection({
  resourceId,
  onTagsChanged,
}: {
  resourceId: string;
  /** Notified after the ensure-trigger-tag flow actually writes a new tag
   *  assignment, so hosts that keep their own separate Tags-block state
   *  (ResourceInfoPanel / DownloadInfoPanel / MediaCard) can refresh it. */
  onTagsChanged?: () => void;
}) {
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

  if (loading || !resource) return null;

  return (
    <PromptSection
      key={resourceId}
      resource={resource as unknown as Resource}
      onPatch={handlePatch}
      onEnsureTriggerTag={handleEnsureTriggerTag}
      canGenerate={false}
      generating={false}
      onGenerate={() => {}}
      translating={translating}
      onTranslate={handleTranslate}
    />
  );
}
