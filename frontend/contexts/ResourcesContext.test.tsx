/**
 * ResourcesContext — the Generated inbox wiring.
 *
 * Two behaviours that no component test would catch:
 *  1. Entering the Generated view clears a multi-selection. The retired temp
 *     effect used to do this on entry; deleting it left stale `item:` ids
 *     selected from the previous view, which the batch toolbar would then act
 *     on against files no longer on screen.
 *  2. `refreshGeneratedCounts()` must NOT blank the pill. The count is `null`
 *     while unknown and the sidebar renders no pill for `null`, so resetting on
 *     every tick made the badge disappear and pop back on each refresh.
 *     `null` is reserved for "we do not know" — scope change and first load.
 *
 * Everything the provider touches is mocked; only the count/selection wiring is
 * under test. The URL is driven through a real MemoryRouter so `section` (and
 * therefore `sidebarView`) comes from the route the way it does in the app.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act, waitFor } from '@testing-library/react';
import React from 'react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));
vi.mock('./TaskManagerContext', () => ({ useTaskManager: () => ({ tasks: [] }) }));
vi.mock('./AuthContext', () => ({ useAuth: () => ({ currentUserId: null }) }));
vi.mock('../hooks/usePermission', () => ({ usePermission: () => ({ canDo: () => true }) }));
vi.mock('../components/Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('../supabaseClient', () => ({
  supabase: {},
  getSupabaseClient: () => null, // no realtime channel in the test
}));
vi.mock('../services/libraryService', () => ({ fetchLibraries: vi.fn().mockResolvedValue([]) }));
vi.mock('../services/unifiedTagService', () => ({
  fetchAllTags: vi.fn().mockResolvedValue([]),
  createTag: vi.fn(),
}));
vi.mock('../utils/tagMerge', () => ({ mergeAssignedTagsIntoAllTags: (a: unknown) => a }));
// The keyset hook owns list state; the inbox wiring never touches it.
// The returned object MUST be referentially stable: the real hook keeps
// `items` stable between renders, and a fresh `[]` per render sends the
// tag-map effect (dep: `resources`) into an infinite setState loop.
const keyset = vi.hoisted(() => ({
  value: {
    items: [] as unknown[],
    setItems: () => {},
    isLoadingMore: false,
    hasMore: false,
    load: () => Promise.resolve(undefined),
    loadMore: () => Promise.resolve(undefined),
  },
}));
vi.mock('../hooks/useKeysetPagination', () => ({
  useKeysetPagination: () => keyset.value,
}));
vi.mock('../services/resourceService', () => ({
  fetchFolders: vi.fn().mockResolvedValue([]),
  fetchChildFolders: vi.fn().mockResolvedValue([]),
  fetchResourcesPaginated: vi.fn(),
  trashResource: vi.fn(),
  restoreResource: vi.fn(),
  permanentDeleteResource: vi.fn(),
  permanentDeleteFolder: vi.fn(),
  fetchTrashedResourcesPaginated: vi.fn(),
  fetchDownloadedResources: vi.fn().mockResolvedValue([]),
  fetchDownloadedResourceCount: vi.fn().mockResolvedValue(0),
  fetchResourceCount: vi.fn().mockResolvedValue(0),
  fetchResourceTags: vi.fn().mockResolvedValue([]),
  addResourceTag: vi.fn(),
  removeResourceTag: vi.fn(),
  fetchSmartFolders: vi.fn().mockResolvedValue([]),
  fetchSmartFolderResultsPaginated: vi.fn(),
  trashResources: vi.fn(),
  getFolderPreview: vi.fn().mockResolvedValue([]),
  updateResource: vi.fn(),
  fetchTrashedFolders: vi.fn().mockResolvedValue([]),
  restoreFolder: vi.fn(),
  fetchFolderContents: vi.fn().mockResolvedValue([]),
}));
vi.mock('../services/generatedService', () => ({
  fetchGeneratedCounts: vi.fn(),
}));
vi.mock('../services/assetsService', () => ({
  fetchAssetCounts: vi.fn(),
}));
vi.mock('../services/promptsService', () => ({
  fetchPromptCounts: vi.fn(),
}));

import {
  ResourcesProvider,
  useResourcesContext,
  RESOURCES_VIEW_MODE_KEY,
} from './ResourcesContext';
import { fetchGeneratedCounts } from '../services/generatedService';
import { fetchAssetCounts } from '../services/assetsService';
import { fetchPromptCounts } from '../services/promptsService';

const counts = vi.mocked(fetchGeneratedCounts);
const assetCounts = vi.mocked(fetchAssetCounts);
const promptCounts = vi.mocked(fetchPromptCounts);

/** The server zero-fills, so every type is always present on the wire. */
const ZERO_COUNTS = {
  character: 0,
  location: 0,
  prop: 0,
  costume: 0,
  prompt: 0,
  audio: 0,
};

/** Resolve-on-demand promise so a refetch can be held in flight. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

let refresh: () => void = () => {};
let select: (ids: string[]) => void = () => {};
let go: (to: string) => void = () => {};

let refreshAssets: () => void = () => {};
let setView: (m: 'grid' | 'list' | 'justified') => void = () => {};

function Harness() {
  const ctx = useResourcesContext();
  refresh = ctx.refreshGeneratedCounts;
  refreshAssets = ctx.refreshAssetCounts;
  select = (ids) => ctx.setSelectedIds(new Set(ids));
  go = (to) => ctx.navigate(to);
  setView = ctx.setViewMode;
  return (
    <div>
      <span data-testid="viewMode">{ctx.viewMode}</span>
      <span data-testid="count">{String(ctx.generatedUnreviewedCount)}</span>
      <span data-testid="selected">{[...ctx.selectedIds].join(',')}</span>
      <span data-testid="view">{ctx.sidebarView}</span>
      <span data-testid="assetCounts">{JSON.stringify(ctx.assetCounts)}</span>
      <span data-testid="promptEntryCount">{String(ctx.promptEntryCount)}</span>
      <span data-testid="assetType">{String(ctx.selectedAssetType)}</span>
      <span data-testid="assetTypeParam">{String(ctx.assetTypeParam)}</span>
      <span data-testid="assetId">{String(ctx.selectedAssetId)}</span>
      <span data-testid="isAssets">{String(ctx.isAssetsView)}</span>
    </div>
  );
}

// One route for every section, so changing `:section` re-renders the SAME
// provider instance instead of remounting it — otherwise "state survives the
// navigation" could never be observed.
function renderAt(entry: string, scopeId = 'team-1') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route
          path="/team/:teamId/resources/:section"
          element={
            <ResourcesProvider isPersonal={false} scopeId={scopeId}>
              <Harness />
            </ResourcesProvider>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

/** The three asset URL shapes each need their own route pattern — that is the
 *  whole point of the routing work under test, so the harness must mirror it
 *  rather than funnel everything through `:section`. */
function renderAssetsAt(entry: string, scopeId = 'team-1') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        {[
          '/team/:teamId/resources/:section',
          '/team/:teamId/resources/assets/:assetType',
          '/team/:teamId/resources/assets/item/:assetId',
        ].map((path) => (
          <Route
            key={path}
            path={path}
            element={
              <ResourcesProvider isPersonal={false} scopeId={scopeId}>
                <Harness />
              </ResourcesProvider>
            }
          />
        ))}
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  counts.mockReset();
  counts.mockResolvedValue({ unreviewed: 12, saved: 3, in_assets: 1 });
  assetCounts.mockReset();
  assetCounts.mockResolvedValue({ ...ZERO_COUNTS, character: 4, location: 2 });
  promptCounts.mockReset();
  // Zero by default so every case written before the override still renders
  // the exact same `assetCounts` JSON. The cases that care set their own.
  promptCounts.mockResolvedValue({ mine: 0, project: null, system: 0 });
});

describe('ResourcesContext — generated count', () => {
  it('loads the unreviewed count for the current scope', async () => {
    renderAt('/team/42/resources/generated');
    await waitFor(() => expect(screen.getByTestId('count').textContent).toBe('12'));
    expect(counts).toHaveBeenCalledWith('team-1');
  });

  it('keeps the previous count visible while a refresh is in flight', async () => {
    renderAt('/team/42/resources/generated');
    await waitFor(() => expect(screen.getByTestId('count').textContent).toBe('12'));

    const pending = deferred<{ unreviewed: number; saved: number; in_assets: number }>();
    counts.mockReturnValueOnce(pending.promise);

    await act(async () => {
      refresh();
    });
    // The pill must not blank out mid-refresh.
    expect(screen.getByTestId('count').textContent).toBe('12');

    await act(async () => {
      pending.resolve({ unreviewed: 5, saved: 3, in_assets: 1 });
    });
    await waitFor(() => expect(screen.getByTestId('count').textContent).toBe('5'));
  });

  it('leaves the count unknown (null) when the initial fetch fails', async () => {
    const err = new Error('boom');
    counts.mockRejectedValueOnce(err);
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});

    renderAt('/team/42/resources/generated');

    await waitFor(() => expect(spy).toHaveBeenCalled());
    // null, never 0 — a 0 would claim "nothing to review" on no evidence.
    expect(screen.getByTestId('count').textContent).toBe('null');
    expect(spy.mock.calls[0][0]).toBe('[ResourcesContext] generated counts failed:');
    spy.mockRestore();
  });

  it('asks for nothing while the scope is still unresolved', async () => {
    // An empty `scopeId` is "we do not know yet", not "scope zero". Sending it
    // makes `?scope_id=` a 422 on every cold load, and the only trace is a
    // console.error the user never sees — a request that can only fail.
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    renderAt('/team/42/resources/generated', '');

    await waitFor(() => expect(screen.getByTestId('count').textContent).toBe('null'));
    expect(counts).not.toHaveBeenCalled();
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});

describe('ResourcesContext — selection on entering Generated', () => {
  it('clears a carried-over multi-selection on entering the Generated view', async () => {
    renderAt('/team/42/resources/downloads');
    await waitFor(() => expect(screen.getByTestId('view').textContent).toBe('downloads'));

    await act(async () => {
      select(['item:1', 'item:2']);
    });
    // Guard the guard: if the selection never took, the assertion below would
    // pass for the wrong reason.
    expect(screen.getByTestId('selected').textContent).toBe('item:1,item:2');

    await act(async () => {
      go('/team/42/resources/generated');
    });

    await waitFor(() => expect(screen.getByTestId('view').textContent).toBe('generated'));
    expect(screen.getByTestId('selected').textContent).toBe('');
  });

  // On ENTRY only. Task 9 gives the Generated view its own multi-select +
  // batch bar, so a selection made while already in the view must survive —
  // an effect that cleared on every render would silently break that.
  it('leaves a selection made while already inside the Generated view', async () => {
    renderAt('/team/42/resources/generated');
    await waitFor(() => expect(screen.getByTestId('view').textContent).toBe('generated'));

    await act(async () => {
      select(['gen:1', 'gen:2']);
    });

    expect(screen.getByTestId('selected').textContent).toBe('gen:1,gen:2');
  });
});

describe('ResourcesContext — assets route derivation', () => {
  it('reads the Assets landing page off `:section`', async () => {
    renderAssetsAt('/team/42/resources/assets');
    await waitFor(() => expect(screen.getByTestId('view').textContent).toBe('assets'));
    expect(screen.getByTestId('isAssets').textContent).toBe('true');
    // No type on the landing page — and that must NOT read as an unknown one,
    // or the view would redirect the landing page to itself forever.
    expect(screen.getByTestId('assetType').textContent).toBe('null');
    expect(screen.getByTestId('assetTypeParam').textContent).toBe('undefined');
  });

  it('derives the type from `assets/:assetType`, where `section` is undefined', async () => {
    renderAssetsAt('/team/42/resources/assets/location');
    await waitFor(() => expect(screen.getByTestId('view').textContent).toBe('assets'));
    expect(screen.getByTestId('assetType').textContent).toBe('location');
    expect(screen.getByTestId('assetId').textContent).toBe('null');
  });

  it('keeps an unknown type out of `selectedAssetType` but visible as the raw param', async () => {
    // The two fields differ ONLY here, and the difference is what lets the
    // view tell "no type asked for" from "a type that does not exist".
    renderAssetsAt('/team/42/resources/assets/nonsense');
    await waitFor(() => expect(screen.getByTestId('view').textContent).toBe('assets'));
    expect(screen.getByTestId('assetType').textContent).toBe('null');
    expect(screen.getByTestId('assetTypeParam').textContent).toBe('nonsense');
  });

  it('derives the asset id from `assets/item/:assetId` without reading it as a type', async () => {
    renderAssetsAt('/team/42/resources/assets/item/727145299382534201');
    await waitFor(() => expect(screen.getByTestId('view').textContent).toBe('assets'));
    expect(screen.getByTestId('assetId').textContent).toBe('727145299382534201');
    expect(screen.getByTestId('assetTypeParam').textContent).toBe('undefined');
  });

  it('leaves an unrelated section alone', async () => {
    // The negative control: `sidebarView === 'assets'` must come from the URL,
    // not from the new arm firing on every route.
    renderAssetsAt('/team/42/resources/downloads');
    await waitFor(() => expect(screen.getByTestId('view').textContent).toBe('downloads'));
    expect(screen.getByTestId('isAssets').textContent).toBe('false');
  });
});

describe('ResourcesContext — asset counts', () => {
  it('loads the per-type counts for the current scope', async () => {
    renderAssetsAt('/team/42/resources/assets');
    await waitFor(() =>
      expect(screen.getByTestId('assetCounts').textContent).toContain('"character":4'),
    );
    expect(assetCounts).toHaveBeenCalledWith('team-1');
  });

  it('keeps the previous counts visible while a refresh is in flight', async () => {
    renderAssetsAt('/team/42/resources/assets');
    await waitFor(() =>
      expect(screen.getByTestId('assetCounts').textContent).toContain('"character":4'),
    );

    const pending = deferred<typeof ZERO_COUNTS>();
    assetCounts.mockReturnValueOnce(pending.promise);

    await act(async () => {
      refreshAssets();
    });
    // Six badges must not blink out and back on every refresh.
    expect(screen.getByTestId('assetCounts').textContent).toContain('"character":4');

    await act(async () => {
      pending.resolve({ ...ZERO_COUNTS, character: 9 });
    });
    await waitFor(() =>
      expect(screen.getByTestId('assetCounts').textContent).toContain('"character":9'),
    );
  });

  it('leaves the counts unknown (null) when the fetch fails', async () => {
    assetCounts.mockRejectedValueOnce(new Error('boom'));
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});

    renderAssetsAt('/team/42/resources/assets');

    await waitFor(() => expect(spy).toHaveBeenCalled());
    // null, not six zeros — zeros would claim an empty library on a fetch we
    // never got an answer to.
    expect(screen.getByTestId('assetCounts').textContent).toBe('null');
    expect(spy.mock.calls[0][0]).toBe('[ResourcesContext] asset counts failed:');
    spy.mockRestore();
  });

  it('asks for nothing while the scope is still unresolved', async () => {
    // Same contract as the Generated pill: no scope yet is not scope zero.
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    renderAssetsAt('/team/42/resources/assets', '');

    await waitFor(() => expect(screen.getByTestId('assetCounts').textContent).toBe('null'));
    expect(assetCounts).not.toHaveBeenCalled();
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  // The Prompts badge counts the UNIFIED catalog — prompted pictures, albums
  // and templates, PLUS the system presets, because the presets section is on
  // that page and a badge that ignored it would undercount what the user is
  // looking at. That is a different number from the `prompt` asset rows
  // `/assets/counts` knows about, and it now lives in its own field.
  //
  // Ruling R15 (sidebar badge and shelf tab must say the same thing) still
  // holds — both read `promptEntryCount`. What changed is that the unified
  // number no longer rides INSIDE `assetCounts`: the shelf's "All" tab sums
  // that record, so a picture count in there made a scope with no assets
  // advertise fifteen of them over the words "No Assets Yet".
  it('counts the unified catalog, presets included, in its own field', async () => {
    promptCounts.mockResolvedValueOnce({ mine: 12, project: null, system: 3 });

    renderAssetsAt('/team/42/resources/assets');

    await waitFor(() => expect(screen.getByTestId('promptEntryCount').textContent).toBe('15'));
    expect(promptCounts).toHaveBeenCalledWith('team-1');
  });

  // The other half of the same rule: `assetCounts` stays asset rows all the
  // way across, so whoever sums it gets a number the asset grid can show.
  it('leaves every assetCounts key an asset-row count', async () => {
    assetCounts.mockResolvedValueOnce({ ...ZERO_COUNTS, character: 4, location: 2, prompt: 7 });
    promptCounts.mockResolvedValueOnce({ mine: 12, project: null, system: 3 });

    renderAssetsAt('/team/42/resources/assets');

    await waitFor(() =>
      expect(screen.getByTestId('assetCounts').textContent).toContain('"character":4'),
    );
    // 7 — the team's own prompt ASSETS — not the 15 entries on the Prompts page.
    expect(screen.getByTestId('assetCounts').textContent).toContain('"prompt":7');
    expect(screen.getByTestId('assetCounts').textContent).toContain('"location":2');
  });

  // M8: the fallback used to be `/assets/counts`'s `prompt`, which counts
  // ASSET ROWS ONLY — a different metric under the same label, reading 0
  // while the page lists 13. The last known unified number is the honest
  // thing to keep; a failed refresh must not rewrite the badge.
  it('keeps the LAST KNOWN unified count when a refresh of it fails', async () => {
    promptCounts.mockResolvedValueOnce({ mine: 12, project: null, system: 3 });
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});

    renderAssetsAt('/team/42/resources/assets');
    await waitFor(() => expect(screen.getByTestId('promptEntryCount').textContent).toBe('15'));

    assetCounts.mockResolvedValueOnce({ ...ZERO_COUNTS, character: 4, prompt: 7 });
    promptCounts.mockRejectedValueOnce(new Error('boom'));
    await act(async () => {
      refreshAssets();
    });

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(
        '[ResourcesContext] prompt counts failed:',
        expect.any(Error),
      ),
    );
    // 15, not the 7 asset rows `/assets/counts` just reported.
    expect(screen.getByTestId('promptEntryCount').textContent).toBe('15');
    spy.mockRestore();
  });

  it('omits the prompt count entirely when the FIRST unified fetch fails', async () => {
    // Nothing known yet, so there is no last value to keep. The badge is then
    // absent (the sidebar renders no number for a null count) rather than
    // showing the asset-row count under the unified label.
    assetCounts.mockResolvedValueOnce({ ...ZERO_COUNTS, character: 4, prompt: 7 });
    promptCounts.mockRejectedValueOnce(new Error('boom'));
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});

    renderAssetsAt('/team/42/resources/assets');

    await waitFor(() =>
      expect(screen.getByTestId('assetCounts').textContent).toContain('"character":4'),
    );
    expect(screen.getByTestId('promptEntryCount').textContent).toBe('null');
    spy.mockRestore();
  });

  it('asks for both counts at once, not one after the other', async () => {
    // Serialized, the badge waited for two round trips before showing any of
    // the six numbers.
    const pending = deferred<typeof ZERO_COUNTS>();
    assetCounts.mockReturnValueOnce(pending.promise);

    renderAssetsAt('/team/42/resources/assets');

    await waitFor(() => expect(promptCounts).toHaveBeenCalledWith('team-1'));
    await act(async () => {
      pending.resolve({ ...ZERO_COUNTS, character: 4 });
    });
  });
});

describe('ResourcesContext — grid view mode preference', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('defaults to the adaptive (justified) layout for a first-time user', () => {
    // Eagle-style rows are the default look: thumbnails keep their own
    // proportions instead of being letterboxed onto a fixed tile.
    renderAt('/team/42/resources/library');
    expect(screen.getByTestId('viewMode').textContent).toBe('justified');
  });

  it('restores a stored preference instead of the default', () => {
    localStorage.setItem(RESOURCES_VIEW_MODE_KEY, 'grid');
    renderAt('/team/42/resources/library');
    expect(screen.getByTestId('viewMode').textContent).toBe('grid');
  });

  it('persists a switch so it survives a reload', () => {
    const first = renderAt('/team/42/resources/library');
    act(() => setView('list'));
    expect(localStorage.getItem(RESOURCES_VIEW_MODE_KEY)).toBe('list');

    // Remount: a fresh provider must come back on 'list', not 'justified'.
    first.unmount();
    renderAt('/team/42/resources/library');
    expect(screen.getByTestId('viewMode').textContent).toBe('list');
  });

  it('ignores a stored value that is not a known mode', () => {
    // A stale or hand-edited key must not put the grid into an unrenderable
    // state — fall back to the default rather than trusting the string.
    localStorage.setItem(RESOURCES_VIEW_MODE_KEY, 'mosaic');
    renderAt('/team/42/resources/library');
    expect(screen.getByTestId('viewMode').textContent).toBe('justified');
  });

  it('still renders when localStorage throws (privacy mode)', () => {
    const spy = vi.spyOn(window.localStorage, 'getItem').mockImplementation(() => {
      throw new Error('storage disabled');
    });
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    renderAt('/team/42/resources/library');
    expect(screen.getByTestId('viewMode').textContent).toBe('justified');
    // Never a silent swallow — the failure is reported.
    expect(errSpy).toHaveBeenCalled();

    spy.mockRestore();
    errSpy.mockRestore();
  });
});
