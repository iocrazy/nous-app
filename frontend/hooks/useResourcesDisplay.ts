// frontend/hooks/useResourcesDisplay.ts

/**
 * Display-layer computations for ResourcesViewInner.
 *
 * Handles sort / breadcrumb / recycled item derivation. Filter bar
 * filters (tags / rating / type / source / AI / date / duration /
 * aspect / social) are pushed to the server via fetchResources — this
 * hook no longer re-applies them in memory. The only remaining
 * "filters" are:
 *   - debounced keyword search (across filename / notes / tag names;
 *     client-side because it's fuzzy and the server doesn't yet
 *     support a FTS column for resources)
 *   - the AI search matched id set (server-computed elsewhere, applied
 *     here because it is an intersection rather than a predicate)
 */

import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import type { Folder, ResourceItem, SmartCollection, Library } from '../types';
import type { SortBy } from '../contexts/ResourcesContext';
import type { BreadcrumbSegment } from '../components/Breadcrumb';
import type { ResourceSearchField } from '../components/resourceSearchScope';
import { DEFAULT_RESOURCE_SCOPE } from '../components/resourceSearchScope';

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
  aiSearchMatchedMediaIds: Set<string> | null;
  isPersonal: boolean;
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
  /** Eagle-style scope toggles — when omitted, defaults to all three
   *  fields (name / notes / tags). */
  searchScope?: ResourceSearchField[];
  /** Flatten mode (manual toggle OR active search): hide folders entirely so
   *  only the recursive flat file list shows. */
  flatten?: boolean;
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
  aiSearchMatchedMediaIds,
  isPersonal,
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
  searchScope = DEFAULT_RESOURCE_SCOPE,
  flatten = false,
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

  // ─── Search + AI-search intersection ────────────────
  // Chip filters (tags / rating / type / source / AI / date / duration /
  // aspect / social) are applied server-side by fetchResources. Here we
  // only narrow further with the fuzzy text search and the AI-search
  // matched id set when they are active.
  const filteredItems = useMemo(() => {
    let items = currentItems;
    if (debouncedSearch.trim()) {
      const q = debouncedSearch.trim().toLowerCase();
      const wantName = searchScope.includes('name');
      const wantNotes = searchScope.includes('notes');
      const wantTags = searchScope.includes('tags');
      items = items.filter((item) => {
        if (wantName) {
          const filename = (item.resource?.filename || '').toLowerCase();
          if (filename.includes(q)) return true;
        }
        if (wantNotes) {
          const notes = (item.resource?.notes || '').toLowerCase();
          if (notes.includes(q)) return true;
        }
        if (wantTags) {
          const tagNames = (resourceTagNamesMap[String(item.resource?.id)] || '').toLowerCase();
          if (tagNames.includes(q)) return true;
        }
        return false;
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
    debouncedSearch,
    aiSearchMatchedMediaIds,
    resourceTagNamesMap,
    searchScope,
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
    // Flatten mode (manual toggle OR active search) hides folders entirely —
    // the resource list is fetched recursively (current folder + descendants),
    // so showing folder cards would be redundant + the old name-only filter
    // surfaced folders that contain no matching files (the reported bug).
    if (flatten) return [];
    if (!debouncedSearch.trim()) return childFolders;
    // Folders only have a name. If 'name' isn't in the scope, the user
    // explicitly opted out of name search — leave folders unfiltered
    // rather than hiding everything.
    if (!searchScope.includes('name')) return childFolders;
    const q = debouncedSearch.trim().toLowerCase();
    return childFolders.filter((f) => f.name.toLowerCase().includes(q));
  }, [childFolders, debouncedSearch, searchScope, flatten]);

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
    if (!isPersonal && selectedLibraryId) {
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
  }, [isSharedView, isRecycleView, isDownloadsView, selectedSmartFolderId, smartFolders, isPersonal, selectedLibraryId, selectedFolderId, libraries, folderChain, t, navigate, resPath, recycleFolderId, trashedFolders, setRecycleFolderId]);

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
