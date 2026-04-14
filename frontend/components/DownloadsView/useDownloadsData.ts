import { useState, useEffect, useMemo } from 'react';
import { fetchAllTags, createTag } from '../../services/unifiedTagService';
import { fetchResourceTags, addResourceTag, removeResourceTag } from '../../services/resourceService';
import { getSupabaseClient } from '../../supabaseClient';
import { Tag } from '../../types';

export interface ResourceData {
  id: string;
  notes: string | null;
  rating: number;
}

export function useResourceDataMap(libraryIds: string[]) {
  const [resourceDataMap, setResourceDataMap] = useState<Record<string, ResourceData>>({});

  useEffect(() => {
    if (libraryIds.length === 0) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;

    supabase
      .from('resources')
      .select('id, media_id, notes, rating')
      .in('media_id', libraryIds)
      .then(({ data, error }) => {
        if (error || !data) return;
        const map: Record<string, ResourceData> = {};
        for (const row of data) {
          if (row.media_id) {
            map[row.media_id] = {
              id: String(row.id),
              notes: row.notes,
              rating: row.rating || 0,
            };
          }
        }
        setResourceDataMap(map);
      });
  }, [libraryIds.join(',')]);

  const resourceIdMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const [mediaId, rd] of Object.entries(resourceDataMap)) {
      map[mediaId] = rd.id;
    }
    return map;
  }, [resourceDataMap]);

  return { resourceDataMap, setResourceDataMap, resourceIdMap };
}

export function useTagSearchMap(resourceDataMap: Record<string, ResourceData>) {
  const [tagSearchMap, setTagSearchMap] = useState<Record<string, string>>({});

  useEffect(() => {
    const resourceIds = Object.values(resourceDataMap).map(r => r.id);
    if (resourceIds.length === 0) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;

    supabase
      .from('resource_tags')
      .select('resource_id, tag:tags(name)')
      .in('resource_id', resourceIds)
      .then(({ data, error }) => {
        if (error || !data) return;
        const resourceToMediaId: Record<string, string> = {};
        for (const [mediaId, rd] of Object.entries(resourceDataMap)) {
          resourceToMediaId[rd.id] = mediaId;
        }
        const map: Record<string, string> = {};
        for (const row of data) {
          const mediaId = resourceToMediaId[String(row.resource_id)];
          if (!mediaId) continue;
          const tag = row.tag as { name: string } | { name: string }[] | null;
          const tagName = Array.isArray(tag) ? tag[0]?.name : tag?.name;
          if (tagName) {
            map[mediaId] = map[mediaId] ? `${map[mediaId]} ${tagName}` : tagName;
          }
        }
        setTagSearchMap(map);
      });
  }, [resourceDataMap]);

  return tagSearchMap;
}

export function useAllTags() {
  const [allTags, setAllTags] = useState<Tag[]>([]);
  useEffect(() => {
    fetchAllTags().then(setAllTags).catch(() => {});
  }, []);
  return { allTags, setAllTags };
}

export function useSelectedVideoTags(resourceId: string | undefined) {
  const [selectedVideoTags, setSelectedVideoTags] = useState<
    Array<{ tag: { id: string; name: string; color?: string } }>
  >([]);

  useEffect(() => {
    if (!resourceId) {
      setSelectedVideoTags([]);
      return;
    }
    setSelectedVideoTags([]);
    fetchResourceTags(resourceId)
      .then(setSelectedVideoTags)
      .catch(() => setSelectedVideoTags([]));
  }, [resourceId]);

  const handleAddTag = async (tagId: string) => {
    if (!resourceId) return;
    try {
      await addResourceTag(resourceId, tagId);
      const updated = await fetchResourceTags(resourceId);
      setSelectedVideoTags(updated);
    } catch (err) {
      console.error('Failed to add tag:', err);
    }
  };

  const handleRemoveTag = async (tagId: string) => {
    if (!resourceId) return;
    try {
      await removeResourceTag(resourceId, tagId);
      setSelectedVideoTags(prev => prev.filter(t => String(t.tag?.id) !== tagId));
    } catch (err) {
      console.error('Failed to remove tag:', err);
    }
  };

  return { selectedVideoTags, setSelectedVideoTags, handleAddTag, handleRemoveTag };
}
