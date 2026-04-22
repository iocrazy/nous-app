// frontend/hooks/useResourcesDisplay.ts

/**
 * Display-layer computations for ResourcesViewInner.
 * Handles filter/sort/breadcrumb/recycled item derivation.
 */

import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import type { Folder, ResourceItem, SmartCollection, Library } from '../types';
import type { SortBy } from '../contexts/ResourcesContext';
import type { BreadcrumbSegment } from '../components/Breadcrumb';
import type { ChipValuesMap } from '../components/resources/filter/types';
import { datePresetToRange } from '../components/resources/filter/dateUtils';

export type FilterType = 'video' | 'image' | 'audio' | 'document' | 'other';

// Known mime-type prefixes. "Other" is everything NOT in this list.
const KNOWN_MIME_PREFIXES = [
  'video/',
  'image/',
  'audio/',
  'application/pdf',
  'application/msword',
  'application/vnd.',
  'text/',
] as const;

function mimeMatchesType(mime: string, type: FilterType): boolean {
  switch (type) {
    case 'video':
      return mime.startsWith('video/');
    case 'image':
      return mime.startsWith('image/');
    case 'audio':
      return mime.startsWith('audio/');
    case 'document':
      return (
        mime.startsWith('application/pdf') ||
        mime.startsWith('application/msword') ||
        mime.startsWith('application/vnd.') ||
        mime.startsWith('text/')
      );
    case 'other':
      return !KNOWN_MIME_PREFIXES.some((p) => mime.startsWith(p));
    default:
      return false;
  }
}

interface UseResourcesDisplayOptions {
  sidebarView: string;
  resources: ResourceItem[];
  downloadedResources: ResourceItem[];
  trashedResources: ResourceItem[];
  trashedFolders: Folder[];
  recycleFolderItems: ResourceItem[];
  recycleFolderId: string | null;
  childFolders: Folder[];
  sortBy: SortBy;
  debouncedSearch: string;
  resourceTagNamesMap: Record<string, string>;
  /** Per-resource set of tag ids used for the tags chip filter. */
  resourceTagIdsMap: Record<string, Set<string>>;
  /** Active filter bar values. Drives tag / rating / type filtering. */
  chipValues: ChipValuesMap;
  aiSearchMatchedMediaIds: Set<string> | null;
  scopeType: 'personal' | 'team';
  selectedFolderId: string | null | undefined;
  selectedLibraryId: string | null | undefined;
  selectedSmartFolderId: string | null | undefined;
  isSharedView: boolean;
  isRecycleView: boolean;
  isDownloadsView: boolean;
  smartFolders: SmartCollection[];
  libraries: Library[];
  folderChain: Folder[];
  navigate: (path: string) => void;
  resPath: (path: string) => string;
  setRecycleFolderId: (id: string | null) => void;
}

export function useResourcesDisplay({
  sidebarView,
  resources,
  downloadedResources,
  trashedResources,
  trashedFolders,
  recycleFolderItems,
  recycleFolderId,
  childFolders,
  sortBy,
  debouncedSearch,
  resourceTagNamesMap,
  resourceTagIdsMap,
  chipValues,
  aiSearchMatchedMediaIds,
  scopeType,
  selectedFolderId,
  selectedLibraryId,
  selectedSmartFolderId,
  isSharedView,
  isRecycleView,
  isDownloadsView,
  smartFolders,
  libraries,
  folderChain,
  navigate,
  resPath,
  setRecycleFolderId,
}: UseResourcesDisplayOptions) {
  const { t } = useTranslation();

  // ─── Recycle bin items ─────────────────────────────
  const recycleItems = useMemo(() => {
    if (!recycleFolderId) {
      const trashedFolderIds = new Set(trashedFolders.map((f) => String(f.id)));
      return trashedResources.filter((item) => {
        const fid = item.folder_id ? String(item.folder_id) : null;
        return !fid || !trashedFolderIds.has(fid);
      });
    }
    return recycleFolderItems;
  }, [trashedResources, trashedFolders, recycleFolderId, recycleFolderItems]);

  const recycleSubFolders = useMemo(() => {
    if (!recycleFolderId) {
      const trashedIds = new Set(trashedFolders.map((f) => String(f.id)));
      return trashedFolders.filter((f) => !f.parent_id || !trashedIds.has(String(f.parent_id)));
    }
    return trashedFolders.filter((f) => f.parent_id && String(f.parent_id) === recycleFolderId);
  }, [trashedFolders, recycleFolderId]);

  const trashedFolderPreviews = useMemo(() => {
    const map: Record<string, Array<{ resource_id?: string | null; thumbnail_path?: string | null; cover_image_path?: string | null; mime_type?: string | null }>> = {};
    for (const item of trashedResources) {
      const fid = item.folder_id ? String(item.folder_id) : null;
      if (!fid) continue;
      if (!map[fid]) map[fid] = [];
      if (map[fid].length < 4) {
        const r = item.resource;
        map[fid].push({ resource_id: r?.id ? String(r.id) : null, thumbnail_path: r?.thumbnail_path || null, cover_image_path: r?.cover_image_path || null, mime_type: r?.mime_type || null });
      }
    }
    return map;
  }, [trashedResources]);

  // ─── Current items (view-aware) ────────────────────
  const currentItems = useMemo(() => {
    if (sidebarView === 'recycle') return recycleItems;
    if (sidebarView === 'downloads') return downloadedResources;
    return resources;
  }, [sidebarView, recycleItems, downloadedResources, resources]);

  // ─── Filter & sort ─────────────────────────────────
  const filteredItems = useMemo(() => {
    let items = currentItems;

    // Type chip (IN semantics across categories).
    const types = chipValues.type.types;
    if (types.length > 0) {
      items = items.filter((item) => {
        const mime = item.resource?.mime_type || '';
        return types.some((t) => mimeMatchesType(mime, t));
      });
    }

    // Rating chip (>= min_rating; 0 means inactive).
    const minRating = chipValues.rating.min_rating;
    if (minRating > 0) {
      items = items.filter((item) => (item.resource?.rating ?? 0) >= minRating);
    }

    // Tags chip (AND semantics — resource must carry every selected tag).
    const selectedTagIds = chipValues.tags.tag_ids;
    if (selectedTagIds.length > 0) {
      items = items.filter((item) => {
        const rid = item.resource?.id ? String(item.resource.id) : null;
        if (!rid) return false;
        const tagSet = resourceTagIdsMap[rid];
        if (!tagSet || tagSet.size === 0) return false;
        return selectedTagIds.every((id) => tagSet.has(id));
      });
    }

    // Source chip (OR semantics across platforms; join via resource.media).
    const selectedPlatforms = chipValues.source.platforms;
    if (selectedPlatforms.length > 0) {
      const platformSet = new Set(selectedPlatforms);
      items = items.filter((item) => {
        const platform = item.resource?.media?.source_platform;
        return typeof platform === 'string' && platformSet.has(platform);
      });
    }

    // AI status chip (AND semantics — every checked flag must equal
    // 'completed' on the resource).
    const aiFlags = chipValues.ai_status;
    if (aiFlags.transcribed || aiFlags.summarized || aiFlags.analyzed) {
      items = items.filter((item) => {
        const r = item.resource;
        if (!r) return false;
        if (aiFlags.transcribed && r.transcript_status !== 'completed') return false;
        if (aiFlags.summarized && r.summary_status !== 'completed') return false;
        if (aiFlags.analyzed && r.visual_analysis_status !== 'completed') return false;
        return true;
      });
    }

    // Date added chip (inclusive, local-calendar comparison).
    const dateRange = datePresetToRange(chipValues.date_added);
    if (dateRange.after || dateRange.before) {
      const afterTs = dateRange.after
        ? new Date(`${dateRange.after}T00:00:00`).getTime()
        : null;
      const beforeTs = dateRange.before
        ? new Date(`${dateRange.before}T23:59:59.999`).getTime()
        : null;
      items = items.filter((item) => {
        const raw = item.resource?.created_at ?? item.created_at;
        if (!raw) return false;
        const ts = new Date(raw).getTime();
        if (Number.isNaN(ts)) return false;
        if (afterTs !== null && ts < afterTs) return false;
        if (beforeTs !== null && ts > beforeTs) return false;
        return true;
      });
    }

    if (debouncedSearch.trim()) {
      const q = debouncedSearch.trim().toLowerCase();
      items = items.filter((item) => {
        const filename = (item.resource?.filename || '').toLowerCase();
        const notes = (item.resource?.notes || '').toLowerCase();
        const tagNames = (resourceTagNamesMap[String(item.resource?.id)] || '').toLowerCase();
        return filename.includes(q) || notes.includes(q) || tagNames.includes(q);
      });
    }
    if (aiSearchMatchedMediaIds) {
      items = items.filter((item) => {
        const mediaId = item.resource?.media_id;
        return mediaId && aiSearchMatchedMediaIds.has(String(mediaId));
      });
    }
    return items;
  }, [
    currentItems,
    chipValues,
    debouncedSearch,
    aiSearchMatchedMediaIds,
    resourceTagNamesMap,
    resourceTagIdsMap,
  ]);

  const sortedItems = useMemo(() => {
    const items = [...filteredItems];
    switch (sortBy) {
      case 'newest': return items.sort((a, b) => new Date(b.resource?.created_at ?? b.created_at).getTime() - new Date(a.resource?.created_at ?? a.created_at).getTime());
      case 'oldest': return items.sort((a, b) => new Date(a.resource?.created_at ?? a.created_at).getTime() - new Date(b.resource?.created_at ?? b.created_at).getTime());
      case 'name-az': return items.sort((a, b) => (a.resource?.filename ?? '').localeCompare(b.resource?.filename ?? ''));
      case 'name-za': return items.sort((a, b) => (b.resource?.filename ?? '').localeCompare(a.resource?.filename ?? ''));
      case 'largest': return items.sort((a, b) => (b.resource?.file_size_bytes ?? 0) - (a.resource?.file_size_bytes ?? 0));
      case 'smallest': return items.sort((a, b) => (a.resource?.file_size_bytes ?? 0) - (b.resource?.file_size_bytes ?? 0));
      default: return items;
    }
  }, [filteredItems, sortBy]);

  const filteredFolders = useMemo(() => {
    if (!debouncedSearch.trim()) return childFolders;
    const q = debouncedSearch.trim().toLowerCase();
    return childFolders.filter((f) => f.name.toLowerCase().includes(q));
  }, [childFolders, debouncedSearch]);

  const allSelectableIds = useMemo(() => {
    const ids: string[] = [];
    filteredFolders.forEach((f) => ids.push(`folder:${f.id}`));
    sortedItems.forEach((i) => ids.push(`item:${i.id}`));
    return ids;
  }, [filteredFolders, sortedItems]);

  // ─── Breadcrumb ────────────────────────────────────
  const breadcrumbSegments = useMemo((): BreadcrumbSegment[] => {
    if (isSharedView) return [{ label: t('resources.sharedManagement') }];
    if (isRecycleView) {
      const segments: BreadcrumbSegment[] = [{ label: t('resources.recycleBin'), onClick: recycleFolderId ? () => setRecycleFolderId(null) : undefined }];
      if (recycleFolderId) {
        const chain: Folder[] = [];
        let currentId: string | null = recycleFolderId;
        while (currentId) {
          const f = trashedFolders.find((tf) => String(tf.id) === currentId);
          if (!f) break;
          chain.unshift(f);
          currentId = f.parent_id ? String(f.parent_id) : null;
          if (currentId && !trashedFolders.some((tf) => String(tf.id) === currentId)) break;
        }
        chain.forEach((f, idx) => {
          segments.push({ label: f.name, onClick: idx === chain.length - 1 ? undefined : () => setRecycleFolderId(String(f.id)) });
        });
      }
      return segments;
    }
    if (isDownloadsView) return [{ label: t('resources.downloads') }];
    if (selectedSmartFolderId) {
      const sf = smartFolders.find((s) => String(s.id) === selectedSmartFolderId);
      return [{ label: t('resources.smartFolders'), onClick: () => {} }, { label: sf?.name ?? '' }];
    }
    if (scopeType === 'team' && selectedLibraryId) {
      const lib = libraries.find((l) => String(l.id) === selectedLibraryId);
      const libName = lib?.name ?? t('resources.allFiles');
      const segs: BreadcrumbSegment[] = [{ label: libName, onClick: selectedFolderId ? () => navigate(resPath(`/resources/library/${selectedLibraryId}`)) : undefined }];
      if (selectedFolderId && folderChain.length > 0) {
        folderChain.forEach((f, idx) => {
          segs.push({ label: f.name, onClick: idx === folderChain.length - 1 ? undefined : () => navigate(resPath(`/resources/library/${selectedLibraryId}/folder/${f.id}`)) });
        });
      }
      return segs;
    }
    const segs: BreadcrumbSegment[] = [{ label: t('resources.myResources'), onClick: selectedFolderId ? () => navigate(resPath('/resources')) : undefined }];
    if (selectedFolderId && folderChain.length > 0) {
      folderChain.forEach((f, idx) => {
        segs.push({ label: f.name, onClick: idx === folderChain.length - 1 ? undefined : () => navigate(resPath(`/resources/folder/${f.id}`)) });
      });
    }
    return segs;
  }, [isSharedView, isRecycleView, isDownloadsView, selectedSmartFolderId, smartFolders, scopeType, selectedLibraryId, selectedFolderId, libraries, folderChain, t, navigate, resPath, recycleFolderId, trashedFolders, setRecycleFolderId]);

  const sortOptions: { value: SortBy; label: string }[] = [
    { value: 'newest', label: t('resources.sortNewest') },
    { value: 'oldest', label: t('resources.sortOldest') },
    { value: 'name-az', label: t('resources.sortNameAZ') },
    { value: 'name-za', label: t('resources.sortNameZA') },
    { value: 'largest', label: t('resources.sortLargest') },
    { value: 'smallest', label: t('resources.sortSmallest') },
  ];

  return {
    recycleItems, recycleSubFolders, trashedFolderPreviews,
    currentItems, filteredItems, sortedItems,
    filteredFolders, allSelectableIds,
    breadcrumbSegments, sortOptions,
  };
}
