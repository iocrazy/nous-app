// features/canvas-core/library/librarySearch.ts
//
// One index over the three libraries a canvas can pull from — assets, uploads
// and generations — fanned out in PARALLEL from the client.
//
// There is deliberately no backend aggregate endpoint. The three stores have
// three different permission models (assets are scope+preset, uploads are
// per-resource ACL, generations are scope-only), and a server-side union would
// have to invent a fourth filtering rule that agreed with all three. Fanning
// out here keeps each store answering in its own terms, and it is what makes
// "assets failed, uploads is fine" representable — see LibraryStoreResult.
//
// WARNING: `GET /api/v1/generated` HAS NO TEXT SEARCH. The other two narrow
// server-side; generations are narrowed here, over the fetched page only. So a
// query can hide a match that lives past the page boundary. That is a real
// limitation, stated rather than papered over: the alternative is a backend
// search parameter, which is a separate change.

import { useCallback, useEffect, useState } from 'react';

import {
  listAssets,
  searchAssets,
  type AssetType,
} from '../../../services/assetsService';
import { generatedMediaCoverUrl } from '../../../services/generatedMediaService';
import { fetchGenerated, type GeneratedItem } from '../../../services/generatedService';
import { getResourceCoverUrl } from '../../../services/resourceService';
import { searchResources } from '../../../services/resourceSearchService';
import type { ResourceSearchResult } from '../../../types';
import { mediaSrc } from '../smart/mediaUrl';

const DEBOUNCE_MS = 300;
const LIMIT = 60;

export type LibraryStore = 'assets' | 'uploads' | 'generated';
export const LIBRARY_STORES: readonly LibraryStore[] = ['assets', 'uploads', 'generated'];

export interface LibraryItem {
  store: LibraryStore;
  id: string;
  title: string;
  thumbUrl: string;
  kind: string;
  aspect?: number;
  ready?: boolean;
}

export type GeneratedScope = 'this-canvas' | 'today' | 'all';
export type AssetScope = 'this-project' | 'all';

export interface LibrarySearchOptions {
  scopeId: string;
  stores?: readonly LibraryStore[];
  assetType?: AssetType | null;
  assetScope?: AssetScope;
  projectId?: string | null;
  uploadKinds?: string;
  generatedScope?: GeneratedScope;
  canvasId?: string | null;
  limit?: number;
}

export interface LibraryStoreResult {
  items: LibraryItem[];
  loading: boolean;
  error: Error | null;
  reload: () => void;
}

export type LibrarySearchResult = Record<LibraryStore, LibraryStoreResult>;

/** Raised when a store cannot be asked at all, so the UI shows a reason
 *  instead of an empty shelf that reads like "you own nothing". */
export class LibraryScopeError extends Error {
  constructor() {
    super('This canvas has no workspace scope');
    this.name = 'LibraryScopeError';
  }
}

export function assetToLibraryItem(row: {
  id: string;
  name: string;
  asset_type: AssetType;
  cover_file_id: string | null;
  readiness?: { state: 'ready' | 'draft'; missing: string[] };
}): LibraryItem {
  return {
    store: 'assets',
    id: row.id,
    title: row.name,
    thumbUrl: row.cover_file_id ? getResourceCoverUrl(row.cover_file_id) : '',
    kind: row.asset_type,
    ready: row.readiness?.state === 'ready',
  };
}

export function uploadToLibraryItem(row: ResourceSearchResult): LibraryItem {
  // `thumbnail_url` is RELATIVE and may be null. Building the absolute form
  // from the id is the same URL the search router would have produced, and it
  // keeps a null from becoming the string "null" in an <img src>.
  return {
    store: 'uploads',
    id: row.id,
    title: row.name,
    thumbUrl: row.thumbnail_url ? getResourceCoverUrl(row.id) : '',
    kind: row.kind,
    ready: true,
  };
}

export function generatedToLibraryItem(row: GeneratedItem): LibraryItem {
  return {
    store: 'generated',
    id: row.id,
    title: row.title,
    // Through `mediaSrc`, never raw: the cover endpoint serves a 1024px preview
    // now, but browsers still hold the OLD response (the full original) under a
    // seven-day immutable cache, so a preview consumer has to ask a URL that
    // cache has never seen. `mediaSrc` stamps the `v=2` that does it, and is
    // idempotent, so composing it with an already-resolved src stays safe.
    // Assets and uploads need no equivalent: `/resources/{id}/cover` is
    // single-tier.
    thumbUrl: mediaSrc(generatedMediaCoverUrl(row.id)),
    kind: row.media_kind,
    ready: true,
  };
}

/** Midnight local time, as the instant the router parses. */
export function startOfTodayIso(now: Date = new Date()): string {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0, 0);
  return d.toISOString();
}

export async function fetchLibraryAssets(
  query: string,
  opts: LibrarySearchOptions,
): Promise<LibraryItem[]> {
  if (!opts.scopeId) throw new LibraryScopeError();
  const q = query.trim() || undefined;
  // `library: 'all'` — EXPLICIT, and the explicitness is the point. The
  // server's default is `in` (library members only), which hides script
  // imports and every asset the P4 legacy-card migration created — exactly the
  // population a canvas points at. Same reasoning as AssetPickerDialog.
  if (opts.assetScope === 'this-project' && opts.projectId) {
    const rows = await listAssets(opts.scopeId, {
      q,
      type: opts.assetType ?? undefined,
      projectId: opts.projectId,
      library: 'all',
      limit: opts.limit ?? LIMIT,
    });
    return rows.map(assetToLibraryItem);
  }
  const rows = await searchAssets(opts.scopeId, {
    q,
    type: opts.assetType ?? undefined,
    library: 'all',
    limit: opts.limit ?? LIMIT,
  });
  return rows.map(assetToLibraryItem);
}

export async function fetchLibraryUploads(
  query: string,
  opts: LibrarySearchOptions,
  signal?: AbortSignal,
): Promise<LibraryItem[]> {
  if (!opts.scopeId) throw new LibraryScopeError();
  const resp = await searchResources({
    q: query.trim(),
    kinds: opts.uploadKinds ?? '',
    limit: opts.limit ?? 50, // 50 is the backend's own ceiling for this route
    teamId: opts.scopeId,
    signal,
  });
  const results = Array.isArray(resp?.results) ? resp.results : [];
  return results.map(uploadToLibraryItem);
}

export async function fetchLibraryGenerated(
  query: string,
  opts: LibrarySearchOptions,
): Promise<LibraryItem[]> {
  if (!opts.scopeId) throw new LibraryScopeError();
  const scope = opts.generatedScope ?? 'this-canvas';
  const page = await fetchGenerated(opts.scopeId, {
    // `all` rather than the router's `unreviewed` default: this is a picker,
    // not a triage queue. A generation the user already saved is still a
    // picture they want to reuse, and the inbox default would hide it.
    state: 'all',
    canvasId: scope === 'this-canvas' ? (opts.canvasId ?? undefined) : undefined,
    since: scope === 'today' ? startOfTodayIso() : undefined,
    limit: opts.limit ?? LIMIT,
    // `include_intermediate` is deliberately omitted: absent and false are the
    // same request, and the "hide masks and brush composites" rule lives
    // server-side in generated_roles.py. Spelling it out here would be a
    // second copy of it.
  });
  const needle = query.trim().toLowerCase();
  const items = (page.items ?? []).map(generatedToLibraryItem);
  if (!needle) return items;
  return items.filter((i) => i.title.toLowerCase().includes(needle));
}

const EMPTY: LibraryItem[] = [];

/** One store's effect. Split out so the three cannot share a loading flag,
 *  an error, or an abort — which is what §3.7 asks for. */
function useOneStore(
  store: LibraryStore,
  enabled: boolean,
  query: string,
  opts: LibrarySearchOptions,
  key: string,
): LibraryStoreResult {
  const [items, setItems] = useState<LibraryItem[]>(EMPTY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    // Only the uploads route takes a signal. The other two services expose no
    // abort, so a superseded call is DISCARDED on arrival rather than
    // cancelled — the request still costs a round trip, but a late answer can
    // never overwrite a newer one.
    const ctrl = new AbortController();
    // Pending starts NOW, not when the timer fires. Announcing it only inside
    // the callback leaves a store reporting `{items: [], loading: false}` for
    // the whole debounce window — which a shelf renders as "there is nothing
    // here", the one thing that is not known yet. Clearing the error here too
    // keeps the triple honest: a store cannot be pending and still be showing
    // the reason its previous attempt failed.
    setLoading(true);
    setError(null);
    const timer = setTimeout(() => {
      const run =
        store === 'assets'
          ? fetchLibraryAssets(query, opts)
          : store === 'uploads'
            ? fetchLibraryUploads(query, opts, ctrl.signal)
            : fetchLibraryGenerated(query, opts);
      run
        .then((rows) => {
          if (cancelled) return;
          setItems(rows);
          setLoading(false);
        })
        .catch((err: unknown) => {
          if (cancelled || (err as { name?: string } | null)?.name === 'AbortError') return;
          console.error(`[useLibrarySearch] ${store} failed:`, err);
          setItems(EMPTY);
          setError(err instanceof Error ? err : new Error(String(err)));
          setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      ctrl.abort();
      clearTimeout(timer);
    };
    // `key` carries every option this store reads; see useLibrarySearch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, store, query, key, nonce]);

  return { items, loading, error, reload };
}

export function useLibrarySearch(
  query: string,
  opts: LibrarySearchOptions,
): LibrarySearchResult {
  const stores = opts.stores ?? LIBRARY_STORES;
  // One string per store holding exactly the options that store reads, so a
  // kind chip on Uploads does not re-fetch Assets.
  const assetKey = `${opts.scopeId}|${opts.assetType ?? ''}|${opts.assetScope ?? 'all'}|${opts.projectId ?? ''}|${opts.limit ?? ''}`;
  const uploadKey = `${opts.scopeId}|${opts.uploadKinds ?? ''}|${opts.limit ?? ''}`;
  const generatedKey = `${opts.scopeId}|${opts.generatedScope ?? 'this-canvas'}|${opts.canvasId ?? ''}|${opts.limit ?? ''}`;

  const assets = useOneStore('assets', stores.includes('assets'), query, opts, assetKey);
  const uploads = useOneStore('uploads', stores.includes('uploads'), query, opts, uploadKey);
  const generated = useOneStore('generated', stores.includes('generated'), query, opts, generatedKey);

  // A plain object on purpose. Each `useOneStore` returns a fresh literal every
  // render, so a `useMemo` over the three could never hit — it would only cost a
  // deps comparison while reading as a stability guarantee it does not provide.
  return { assets, uploads, generated };
}
