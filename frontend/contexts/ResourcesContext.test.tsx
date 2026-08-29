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

import { ResourcesProvider, useResourcesContext } from './ResourcesContext';
import { fetchGeneratedCounts } from '../services/generatedService';

const counts = vi.mocked(fetchGeneratedCounts);

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

function Harness() {
  const ctx = useResourcesContext();
  refresh = ctx.refreshGeneratedCounts;
  select = (ids) => ctx.setSelectedIds(new Set(ids));
  go = (to) => ctx.navigate(to);
  return (
    <div>
      <span data-testid="count">{String(ctx.generatedUnreviewedCount)}</span>
      <span data-testid="selected">{[...ctx.selectedIds].join(',')}</span>
      <span data-testid="view">{ctx.sidebarView}</span>
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

beforeEach(() => {
  counts.mockReset();
  counts.mockResolvedValue({ unreviewed: 12, saved: 3, in_assets: 1 });
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
