/**
 * MediaTagPicker — self-contained tag picker for parsed_media items.
 * Wraps UnifiedTagPicker with lazy-load logic: fetches allTags + videoTags
 * on first render, then delegates to UnifiedTagPicker for the UI.
 */
import React, { useState, useEffect, useCallback } from 'react';
import type { Tag } from '../types';
import { UnifiedTagPicker } from './UnifiedTagPicker';
import {
  fetchAllTags,
  createTag,
  addEntityTag,
  removeEntityTag,
  fetchEntityTags,
} from '../services/unifiedTagService';

interface MediaTagPickerProps {
  /** parsed_media ID */
  mediaId: string;
  /** Pre-existing tag names for immediate (pre-fetch) display */
  initialTagNames?: string[];
}

export const MediaTagPicker: React.FC<MediaTagPickerProps> = ({
  mediaId,
  initialTagNames = [],
}) => {
  const [allTags, setAllTags] = useState<Tag[]>([]);
  const [assignedTags, setAssignedTags] = useState<Tag[]>([]);
  const [loaded, setLoaded] = useState(false);

  // Lazy-load tags on mount
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [all, associations] = await Promise.all([
          fetchAllTags(),
          fetchEntityTags('media', mediaId),
        ]);
        if (cancelled) return;
        setAllTags(all);
        setAssignedTags(associations.map((a) => a.tag).filter(Boolean));
        setLoaded(true);
      } catch {
        // If fetch fails, we still show initial tag names as placeholders
      }
    })();
    return () => { cancelled = true; };
  }, [mediaId]);

  const handleAdd = useCallback(async (tagId: string) => {
    try {
      await addEntityTag('media', mediaId, tagId);
      const tag = allTags.find((t) => String(t.id) === tagId);
      if (tag) setAssignedTags((prev) => [...prev, tag]);
    } catch { /* ignore */ }
  }, [mediaId, allTags]);

  const handleRemove = useCallback(async (tagId: string) => {
    try {
      await removeEntityTag('media', mediaId, tagId);
      setAssignedTags((prev) => prev.filter((t) => String(t.id) !== tagId));
    } catch { /* ignore */ }
  }, [mediaId]);

  const handleCreate = useCallback(async (name: string, color: string): Promise<Tag | null> => {
    try {
      const tag = await createTag({ name, color, type: 'user' });
      setAllTags((prev) => [...prev, tag]);
      return tag;
    } catch {
      return null;
    }
  }, []);

  // Before tags are loaded, show initial tag names as simple badges
  if (!loaded && initialTagNames.length > 0) {
    return (
      <div className="flex flex-wrap gap-1.5">
        {initialTagNames.map((name, i) => (
          <span
            key={i}
            className="inline-flex items-center px-2 py-0.5 text-xs rounded-full bg-zinc-800/50 text-zinc-400"
          >
            {name}
          </span>
        ))}
      </div>
    );
  }

  return (
    <UnifiedTagPicker
      assignedTags={assignedTags}
      allTags={allTags}
      onAdd={handleAdd}
      onRemove={handleRemove}
      onCreate={handleCreate}
    />
  );
};
