import { useState, useEffect, useMemo } from 'react';
import { fetchAllTags, createTag } from '../../services/unifiedTagService';
import { fetchResourceTags, addResourceTag, removeResourceTag } from '../../services/resourceService';
import { getSupabaseClient } from '../../supabaseClient';
import { Tag } from '../../types';

export interface ResourceData {
  id: string;
  notes: string | null;
  rating: number;
  // AI pipeline statuses — sourced from resources.* (migration 067/075 moved
  // them off parsed_media). Passed to CompactMediaCard.aiStatus so the
  // transcript/summary/analysis icons light up correctly after processing.
  transcript_status?: string;
  summary_status?: string;
  visual_analysis_status?: string;
}

export function useResourceDataMap(libraryIds: string[]) {
  const [resourceDataMap, setResourceDataMap] = useState<Record<string, ResourceData>>({});

  useEffect(() => {
    if (libraryIds.length === 0) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;

    supabase
      .from('resources')
      .select(
        'id, media_id, notes, rating, transcript_status, summary_status, visual_analysis_status',
      )
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
              transcript_status: row.transcript_status ?? undefined,
              summary_status: row.summary_status ?? undefined,
              visual_analysis_status: row.visual_analysis_status ?? undefined,
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

  // Per-media AI status map — passed to CompactMediaCard so the four AI
  // icons (audio / transcript / summary / analysis) reflect the
  // resource-level status. Without this the icons were stuck gray because
  // the columns were removed from parsed_media in migration 075.
  const aiStatusMap = useMemo(() => {
    const map: Record<string, {
      transcript_status?: string;
      summary_status?: string;
      visual_analysis_status?: string;
    }> = {};
    for (const [mediaId, rd] of Object.entries(resourceDataMap)) {
      if (rd.transcript_status || rd.summary_status || rd.visual_analysis_status) {
        map[mediaId] = {
          transcript_status: rd.transcript_status,
          summary_status: rd.summary_status,
          visual_analysis_status: rd.visual_analysis_status,
        };
      }
    }
    return map;
  }, [resourceDataMap]);

  return { resourceDataMap, setResourceDataMap, resourceIdMap, aiStatusMap };
}

export function useTagSearchMap(resourceDataMap: Record<string, ResourceData>) {
  const [tagSearchMap, setTagSearchMap] = useState<Record<string, string>>({});

  useEffect(() => {
    const resourceIds = Object.values(resourceDataMap).map(r => r.id);
    if (resourceIds.length === 0) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;

    // Batch query to avoid URL length limits (Kong/nginx reject overly long GETs → 502)
    const CHUNK = 50;
    const chunks: string[][] = [];
    for (let i = 0; i < resourceIds.length; i += CHUNK) {
      chunks.push(resourceIds.slice(i, i + CHUNK));
    }
    Promise.all(
      chunks.map(ids =>
        supabase
          .from('resource_tags')
          .select('resource_id, tag:tags(name)')
          .in('resource_id', ids)
      ),
    ).then((results) => {
      const resourceToMediaId: Record<string, string> = {};
      for (const [mediaId, rd] of Object.entries(resourceDataMap)) {
        resourceToMediaId[rd.id] = mediaId;
      }
      const map: Record<string, string> = {};
      for (const { data, error } of results) {
        if (error || !data) continue;
        for (const row of data) {
          const mediaId = resourceToMediaId[String(row.resource_id)];
          if (!mediaId) continue;
          const tag = row.tag as { name: string } | { name: string }[] | null;
          const tagName = Array.isArray(tag) ? tag[0]?.name : tag?.name;
          if (tagName) {
            map[mediaId] = map[mediaId] ? `${map[mediaId]} ${tagName}` : tagName;
          }
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
