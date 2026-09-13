/**
 * AssetShelf — the tab bar's relationship with the URL, the request the
 * filters produce, where system presets land, and what happens after a create.
 *
 * The rows below are the real `GET /assets` payload from
 * `.superpowers/sdd/2026-08-29-asset-library-p2-codex-and-sheets/wire-fixtures-assets.json`,
 * shapes intact: string ids, `scope_id: null` on the preset, sparse
 * `file_counts_by_slot`, `tags` as an object of group → values.
 *
 * The preset assertion is the load-bearing one. `GET /assets` unions global
 * presets into every scope's shelf while `GET /assets/counts` deliberately
 * excludes them, so a shelf that mixed them into the main grid would show a
 * grid permanently longer than its own sidebar badge, with nothing on screen
 * saying why.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';

const translate = (key: string, opts?: string | Record<string, unknown>): string => {
  const bundle: Record<string, string> = {
    'resources.assets': 'Assets',
    'assets.tab.all': 'All',
    'assets.types.character': 'Characters',
    'assets.types.location': 'Locations',
    'assets.types.prop': 'Props',
    'assets.types.costume': 'Costumes',
    'assets.types.prompt': 'Prompts',
    'assets.types.audio': 'Audio',
    'saveAsAsset.type.character': 'Character',
    'saveAsAsset.type.location': 'Location',
    'assets.new': 'New',
    'assets.newOfType': 'New {{type}}',
    'assets.importFromScript': 'Import From Script',
    'assets.presets.title': 'System Presets',
    'assets.empty.none': 'No Assets Yet',
    'assets.empty.ofType': 'No {{type}} Yet',
    'assets.empty.filtered': 'No Assets Match These Filters',
    'assets.loadFailed': 'Could Not Load Assets',
    'assets.readinessSortPaged': 'Readiness Sorting Only Orders One Page',
    'assets.filter.project': 'Project',
    'assets.filter.anyProject': 'Any Project',
    'assets.filter.readiness': 'Readiness',
    'assets.filter.anyReadiness': 'Any Readiness',
    'assets.readiness.ready': 'Ready',
    'assets.readiness.draft': 'Draft',
    'assets.filter.tag': 'Tag',
    'assets.filter.anyTag': 'Any Tag',
    'assets.filter.sort': 'Sort',
    'assets.filter.library': 'Library',
    'assets.library.in': 'In Library',
    'assets.library.out': 'Not In Library',
    'assets.library.all': 'All',
    'assets.library.notInLibrary': 'Not In Library',
    'assets.empty.nothingOutOfLibrary': 'Every Asset Is Already In Your Library',
    'assets.sort.recent': 'Recently Updated',
    'assets.sort.name': 'Name',
    'assets.sort.readiness': 'Readiness',
    'assets.err.not_a_member': 'You are not a member of this workspace.',
    'assets.err.generic': 'Something went wrong. Please try again.',
    'assets.card.missing': 'Missing: {{slots}}',
    'assets.card.coverage': '{{filled}} of {{total}} slots filled',
    'assets.card.open': 'Open {{name}}',
    'common.loading': 'Loading…',
  };
  const fallback = typeof opts === 'string' ? opts : (opts?.defaultValue as string | undefined);
  let out = bundle[key] ?? fallback ?? key;
  if (opts && typeof opts === 'object') {
    for (const [name, value] of Object.entries(opts)) {
      if (name === 'defaultValue') continue;
      out = out.split(`{{${name}}}`).join(String(value));
    }
  }
  return out;
};

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, o?: string | Record<string, unknown>) => translate(k, o),
  }),
}));

const addToast = vi.fn();
// `useOptionalToast` too: the asset card's action menu reads it (P5), and a
// mock factory missing an export makes every render of that card throw.
vi.mock('../../Toast', () => ({
  useToast: () => ({ addToast }),
  useOptionalToast: () => ({ addToast }),
}));

const refreshAssetCounts = vi.fn();
// Mutable so the counts cases can set their own without a second mock factory.
const ctxCounts = {
  assetCounts: { character: 2, location: 1, prop: 0, costume: 0, prompt: 0, audio: 0 } as Record<string, number>,
  promptEntryCount: null as number | null,
};
vi.mock('../../../contexts/ResourcesContext', () => ({
  useResourcesContext: () => ({
    scopeId: '727145299382534200',
    teamId: '42',
    resPath: (p: string) => `/team/42${p}`,
    assetCounts: ctxCounts.assetCounts,
    promptEntryCount: ctxCounts.promptEntryCount,
    refreshAssetCounts,
  }),
}));

const fetchProjects = vi.fn();
vi.mock('../../../services/projectsService', () => ({
  fetchProjects: (...a: unknown[]) => fetchProjects(...a),
}));

vi.mock('../../../services/resourceService', () => ({
  getResourceCoverUrl: (id: string) => `https://api.test/resources/${id}/cover`,
}));

const listAssets = vi.fn();
vi.mock('../../../services/assetsService', async () => {
  const { GeneratedApiError } = await import('../../../services/apiEnvelope');
  return {
    listAssets: (...a: unknown[]) => listAssets(...a),
    GeneratedApiError,
  };
});

// Stubbed like `GeneratedView.test.tsx` stubs `CleanupDialog`: this file owns
// the HAND-OFF (does a successful create refresh the badges and open the
// sheet?), not the dialog's own validation — that has its own suite.
vi.mock('./NewAssetDialog', () => ({
  NewAssetDialog: ({
    assetType,
    onCreated,
  }: {
    assetType: string;
    onCreated: (a: { id: string }) => void;
  }) => (
    <div data-testid="new-asset-dialog" data-dialog-type={assetType}>
      <button type="button" onClick={() => onCreated({ id: '727145299382534311' })}>
        stub-create
      </button>
    </div>
  ),
}));

import { GeneratedApiError } from '../../../services/apiEnvelope';
import { AssetShelf } from './AssetShelf';
import type { AssetRow } from '../../../services/assetsService';

const CHARACTER: AssetRow = {
  id: '727145299382534300',
  scope_id: '727145299382534200',
  asset_type: 'character',
  subtype: null,
  name: 'Sang Yao',
  role_tag: 'lead',
  description: 'Late twenties, wind-burnt.',
  attrs: {},
  prompt_positive: 'same woman as reference…',
  prompt_negative: null,
  prompt_positive_zh: null,
  prompt_negative_zh: null,
  platform_params: {},
  cover_file_id: '727145299382534146',
  source: 'manual',
  duplicated_from: null,
  is_system_preset: false,
  in_library: true,
  tags: { role: ['lead'] },
  sort_order: 0,
  created_by: '11111111-1111-1111-1111-111111111111',
  created_at: '2026-08-29T10:00:00Z',
  updated_at: '2026-08-29T10:00:00Z',
  readiness: { state: 'ready', missing: [] },
  file_counts_by_slot: { sheet: 1, stills: 5 },
  project_ids: ['55'],
  loadout_count: 2,
};

const PRESET: AssetRow = {
  ...CHARACTER,
  id: '727145299382534303',
  scope_id: null,
  asset_type: 'prompt',
  name: 'Multi-angle 3x3 sheet',
  role_tag: '',
  source: 'system_preset',
  is_system_preset: true,
  in_library: true,
  file_counts_by_slot: {},
  project_ids: [],
  loadout_count: 0,
};

let location = { pathname: '', search: '' };
function LocationProbe() {
  const l = useLocation();
  location = { pathname: l.pathname, search: l.search };
  return null;
}

function renderAt(entry: string) {
  location = { pathname: '', search: '' };
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <LocationProbe />
      <Routes>
        <Route
          path="/team/:teamId/resources/assets"
          element={<AssetShelf assetType={null} />}
        />
        <Route
          path="/team/:teamId/resources/assets/character"
          element={<AssetShelf assetType="character" />}
        />
        <Route
          path="/team/:teamId/resources/assets/location"
          element={<AssetShelf assetType="location" />}
        />
        {/* Landing spots for navigation assertions — the shelf must not be
            what proves it moved. */}
        <Route path="/team/:teamId/resources/assets/item/:assetId" element={<div>sheet</div>} />
        <Route path="/team/:teamId/projects/:projectId" element={<div>workspace</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

const tab = (name: string) => screen.getByRole('tab', { name: new RegExp(`^${name}`) });

beforeEach(() => {
  listAssets.mockReset().mockResolvedValue([]);
  fetchProjects.mockReset().mockResolvedValue([{ id: 55, name: 'Bamboo Reel' }]);
  refreshAssetCounts.mockReset();
  addToast.mockReset();
});

describe('AssetShelf — tabs and the URL', () => {
  it('marks the tab the ROUTE selected, not one it remembers', async () => {
    renderAt('/team/42/resources/assets/character');
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    expect(tab('Characters').getAttribute('aria-selected')).toBe('true');
    expect(tab('All').getAttribute('aria-selected')).toBe('false');
  });

  it('the landing page selects All', async () => {
    renderAt('/team/42/resources/assets');
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    expect(tab('All').getAttribute('aria-selected')).toBe('true');
  });

  it('clicking a tab navigates to that type URL', async () => {
    renderAt('/team/42/resources/assets');
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    fireEvent.click(tab('Characters'));
    await waitFor(() =>
      expect(location.pathname).toBe('/team/42/resources/assets/character'),
    );
    expect(tab('Characters').getAttribute('aria-selected')).toBe('true');
  });

  it('a tab change KEEPS the filters', async () => {
    // Someone narrowed to a project and then looked at Locations. Dropping
    // the project there answers a question they did not ask.
    renderAt('/team/42/resources/assets/character?project_id=55&readiness=draft');
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    fireEvent.click(tab('Locations'));
    await waitFor(() => expect(location.pathname).toBe('/team/42/resources/assets/location'));
    expect(new URLSearchParams(location.search).get('project_id')).toBe('55');
    expect(new URLSearchParams(location.search).get('readiness')).toBe('draft');
  });

  it('shows per-type counts from the context, with All as their sum', async () => {
    renderAt('/team/42/resources/assets');
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    expect(tab('All').textContent).toContain('3');
    expect(tab('Characters').textContent).toContain('2');
  });
});

describe('AssetShelf — the request', () => {
  it('sends the tab type and the URL filters', async () => {
    renderAt('/team/42/resources/assets/character?project_id=55&readiness=draft&sort=name');
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    expect(listAssets).toHaveBeenCalledWith('727145299382534200', {
      type: 'character',
      projectId: '55',
      readiness: 'draft',
      sort: 'name',
      limit: 60,
      offset: 0,
    });
  });

  it('the All tab sends no type at all', async () => {
    renderAt('/team/42/resources/assets');
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    const [, opts] = listAssets.mock.calls[0];
    expect('type' in opts).toBe(false);
  });

  it('changing a filter re-requests with the new value', async () => {
    renderAt('/team/42/resources/assets/character');
    await waitFor(() => expect(listAssets).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByText('Readiness', { selector: 'span' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Draft' }));
    await waitFor(() => expect(listAssets).toHaveBeenCalledTimes(2));
    expect(listAssets.mock.calls[1][1]).toMatchObject({ readiness: 'draft' });
    // …and it is in the URL, so the view is a link somebody can paste.
    expect(new URLSearchParams(location.search).get('readiness')).toBe('draft');
  });
});

describe('AssetShelf — library membership (mig 449)', () => {
  it('defaults to the library and sends NO library param', async () => {
    // The server's own default is `in`. Sending it explicitly would produce
    // the identical request and put a param in the URL that says nothing —
    // but the DEFAULT itself is the load-bearing half of this change, so what
    // this pins is that nothing widens it back.
    renderAt('/team/42/resources/assets/character');
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    const [, opts] = listAssets.mock.calls[0];
    expect('library' in opts).toBe(false);
    expect(new URLSearchParams(location.search).has('library')).toBe(false);
  });

  it('the chip re-requests with the value and puts it in the URL', async () => {
    renderAt('/team/42/resources/assets/character');
    await waitFor(() => expect(listAssets).toHaveBeenCalledTimes(1));

    // Addressed by the chip's own id, not by its label: unlike the other
    // chips this one ALWAYS renders a summary ("Library · In Library"),
    // because there is no "unset" state for it to fall back to.
    const chip = document.querySelector('[data-chip-id="library"] button');
    expect(chip, 'the library chip is on the filter row').toBeTruthy();
    fireEvent.click(chip as Element);
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Not In Library' }));
    await waitFor(() => expect(listAssets).toHaveBeenCalledTimes(2));
    expect(listAssets.mock.calls[1][1]).toMatchObject({ library: 'out' });
    // In the URL, so the view is a link somebody can paste — there is no
    // local chip state that could disagree with it.
    expect(new URLSearchParams(location.search).get('library')).toBe('out');
  });

  it('an empty out-of-library shelf says why, not "no assets match"', async () => {
    // A user who just clicked "Not In Library" and read the generic filtered
    // line would go hunting for a filter they never set. The answer here is
    // the good news that there is nothing left to adopt.
    renderAt('/team/42/resources/assets/character?library=out');
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    expect(listAssets.mock.calls[0][1]).toMatchObject({ library: 'out' });
    expect(screen.getByText('Every Asset Is Already In Your Library')).toBeTruthy();
    expect(screen.queryByText('No Assets Match These Filters')).toBeNull();
  });

  it('a library value the server would refuse never reaches the request', async () => {
    renderAt('/team/42/resources/assets/character?library=maybe');
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    const [, opts] = listAssets.mock.calls[0];
    expect('library' in opts).toBe(false);
  });

  it('names the project on a card chip instead of showing the raw id', async () => {
    // The bug: the chips rendered 15-digit Snowflakes. The shelf already
    // fetches the project list for its Project filter, so the map is free.
    fetchProjects.mockResolvedValue([{ id: '55', name: 'Bamboo Reel' }]);
    listAssets.mockResolvedValue([CHARACTER]);
    renderAt('/team/42/resources/assets/character');
    await waitFor(() => expect(screen.getAllByTestId('asset-card')).toHaveLength(1));
    await waitFor(() =>
      expect(screen.getByTestId('asset-card-project').textContent).toBe('Bamboo Reel'),
    );
  });
});

describe('AssetShelf — rows', () => {
  it('separates system presets into their own section', async () => {
    listAssets.mockResolvedValue([CHARACTER, PRESET]);
    renderAt('/team/42/resources/assets');
    await waitFor(() => expect(screen.getAllByTestId('asset-card')).toHaveLength(2));

    const presetSection = screen.getByTestId('preset-section');
    const presetCards = within(presetSection).getAllByTestId('asset-card');
    expect(presetCards).toHaveLength(1);
    expect(presetCards[0].getAttribute('data-asset-id')).toBe(PRESET.id);
    // The team's own card is NOT inside that section — which is what keeps
    // the main grid's length equal to the sidebar badge.
    expect(
      within(presetSection).queryByText(CHARACTER.name),
    ).toBeNull();
  });

  it('renders no preset section when the page has none', async () => {
    listAssets.mockResolvedValue([CHARACTER]);
    renderAt('/team/42/resources/assets');
    await waitFor(() => expect(screen.getAllByTestId('asset-card')).toHaveLength(1));
    expect(screen.queryByTestId('preset-section')).toBeNull();
  });

  it('opens the sheet when a card is clicked', async () => {
    listAssets.mockResolvedValue([CHARACTER]);
    renderAt('/team/42/resources/assets/character');
    await waitFor(() => expect(screen.getByTestId('asset-card')).toBeTruthy());
    fireEvent.click(screen.getByTestId('asset-card'));
    await waitFor(() =>
      expect(location.pathname).toBe('/team/42/resources/assets/item/727145299382534300'),
    );
  });

  it('an empty type shelf says so in that type’s words', async () => {
    renderAt('/team/42/resources/assets/character');
    await waitFor(() => expect(screen.getByText('No Characters Yet')).toBeTruthy());
  });

  it('an empty FILTERED shelf blames the filter, not the library', async () => {
    renderAt('/team/42/resources/assets/character?readiness=draft');
    await waitFor(() =>
      expect(screen.getByText('No Assets Match These Filters')).toBeTruthy(),
    );
  });

  it('a failed list is not reported as an empty library', async () => {
    // The distinction the user needs: "you have nothing" vs "we could not
    // ask". Rendering the empty state on a network failure tells them their
    // library is gone.
    listAssets.mockRejectedValue(new GeneratedApiError(403, 'not_a_member', 'nope'));
    renderAt('/team/42/resources/assets/character');
    await waitFor(() => expect(screen.getByTestId('asset-load-error')).toBeTruthy());
    expect(screen.queryByText('No Characters Yet')).toBeNull();
    expect(addToast).toHaveBeenCalledWith('You are not a member of this workspace.', 'error');
  });
});

describe('AssetShelf — pagination', () => {
  it('offers Load More only when the page came back full, and appends', async () => {
    const full = Array.from({ length: 60 }, (_, i) => ({ ...CHARACTER, id: `7271452993825${i}` }));
    listAssets.mockResolvedValueOnce(full).mockResolvedValueOnce([
      { ...CHARACTER, id: 'tail-1' },
    ]);
    renderAt('/team/42/resources/assets/character');
    await waitFor(() => expect(screen.getAllByTestId('asset-card')).toHaveLength(60));

    fireEvent.click(screen.getByText('Load More'));
    await waitFor(() => expect(screen.getAllByTestId('asset-card')).toHaveLength(61));
    expect(listAssets.mock.calls[1][1]).toMatchObject({ offset: 60 });
    // A short page ends the list rather than offering a button that yields
    // nothing.
    expect(screen.queryByText('Load More')).toBeNull();
  });

  it('does not offer Load More on a short first page', async () => {
    listAssets.mockResolvedValue([CHARACTER]);
    renderAt('/team/42/resources/assets/character');
    await waitFor(() => expect(screen.getByTestId('asset-card')).toBeTruthy());
    expect(screen.queryByText('Load More')).toBeNull();
  });

  it('refuses a second page under sort=readiness, and says why', async () => {
    // `sort=readiness` orders drafts-first in PYTHON over the page that came
    // back, so appending page 2 yields drafts, ready, drafts, ready — the
    // ordering silently stops holding at row 60. Refusing the page is the
    // honest answer; the hint is what keeps the refusal from reading as a
    // broken button.
    const full = Array.from({ length: 60 }, (_, i) => ({ ...CHARACTER, id: `7271452993825${i}` }));
    listAssets.mockResolvedValue(full);
    renderAt('/team/42/resources/assets/character?sort=readiness');
    await waitFor(() => expect(screen.getAllByTestId('asset-card')).toHaveLength(60));

    const button = screen.getByTestId('asset-load-more');
    expect(button).toBeDisabled();
    expect(screen.getByTestId('readiness-sort-paged')).toBeTruthy();
    // A disabled button is an affordance, not a guarantee.
    fireEvent.click(button);
    expect(listAssets).toHaveBeenCalledTimes(1);
  });

  it('re-enables Load More the moment the sort moves off readiness', async () => {
    // The other direction of the same branch: the refusal is a property of
    // ONE sort, not a permanent cap on the shelf.
    const full = Array.from({ length: 60 }, (_, i) => ({ ...CHARACTER, id: `7271452993825${i}` }));
    listAssets.mockResolvedValue(full);
    renderAt('/team/42/resources/assets/character?sort=recent');
    await waitFor(() => expect(screen.getAllByTestId('asset-card')).toHaveLength(60));

    expect(screen.getByTestId('asset-load-more')).toBeEnabled();
    expect(screen.queryByTestId('readiness-sort-paged')).toBeNull();
  });
});

describe('AssetShelf — creating', () => {
  it('a type shelf opens the dialog already knowing the type', async () => {
    renderAt('/team/42/resources/assets/character');
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId('new-asset'));
    expect(screen.getByTestId('new-asset-dialog').getAttribute('data-dialog-type')).toBe(
      'character',
    );
  });

  it('the All tab asks which type first', async () => {
    renderAt('/team/42/resources/assets');
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId('new-asset'));
    // Six choices, no dialog yet — "New" on the All tab cannot guess.
    expect(screen.queryByTestId('new-asset-dialog')).toBeNull();
    expect(screen.getAllByRole('menuitem')).toHaveLength(6);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Location' }));
    expect(screen.getByTestId('new-asset-dialog').getAttribute('data-dialog-type')).toBe(
      'location',
    );
  });

  it('refreshes the sidebar counts after a create, then opens the new sheet', async () => {
    // The Task 5 contract: `refreshAssetCounts()` is the ONLY thing that
    // moves the six badges. Skipping it leaves them stale until the scope
    // changes, and nothing on screen would say the number is old.
    renderAt('/team/42/resources/assets/character');
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId('new-asset'));
    fireEvent.click(screen.getByText('stub-create'));

    expect(refreshAssetCounts).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(location.pathname).toBe('/team/42/resources/assets/item/727145299382534311'),
    );
  });
});

describe('AssetShelf — import from script', () => {
  it('is disabled until a project filter is chosen', async () => {
    renderAt('/team/42/resources/assets');
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    const button = screen.getByTestId('import-from-script');
    expect(button.hasAttribute('disabled')).toBe(true);
    // The tooltip has to say WHY — a disabled control with no explanation is
    // a dead end.
    expect(button.getAttribute('title')).toContain('project');
  });

  it('links to that project’s workspace once one is chosen', async () => {
    renderAt('/team/42/resources/assets?project_id=55');
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    const button = screen.getByTestId('import-from-script');
    expect(button.hasAttribute('disabled')).toBe(false);
    fireEvent.click(button);
    await waitFor(() => expect(location.pathname).toBe('/team/42/projects/55'));
  });
});

describe('AssetShelf — projects chip', () => {
  it('says the options are missing rather than showing "no projects"', async () => {
    fetchProjects.mockRejectedValue(new Error('offline'));
    renderAt('/team/42/resources/assets');
    await waitFor(() => expect(listAssets).toHaveBeenCalled());
    fireEvent.click(screen.getByText('Project', { selector: 'span' }));
    await waitFor(() => expect(screen.getByText('Projects unavailable')).toBeTruthy());
    // Clearing an active filter must keep working while the list is down.
    expect(screen.getByRole('menuitemradio', { name: 'Any Project' })).toBeTruthy();
  });
});

/**
 * Tab numbers, and the one that is not in the same currency as the rest.
 *
 * The Prompts tab does not open this shelf — `AssetsView` swaps in
 * `PromptsShelf` for that type — so it counts prompt ENTRIES (prompted
 * pictures, albums, templates, system presets). Every other tab counts asset
 * rows, and so must "All", because "All" labels a grid of asset rows.
 *
 * The bug this pins: the entry count used to be written into
 * `assetCounts.prompt`, which "All" sums. A scope holding no assets at all but
 * fifteen prompted pictures rendered "All 15" directly above the words "No
 * Assets Yet" — the exact disagreement this file's subject documents as its
 * second invariant ("PRESETS ARE SEPARATED ... so the badge and the team's own
 * grid agree").
 */
describe('AssetShelf — tab counts', () => {
  beforeEach(() => {
    ctxCounts.assetCounts = { character: 0, location: 0, prop: 0, costume: 0, prompt: 0, audio: 0 };
    ctxCounts.promptEntryCount = 15;
  });

  it('does not sum prompt entries into All', async () => {
    renderAt('/team/42/resources/assets');
    await waitFor(() => expect(tab('All').textContent).toBe('All0'));
  });

  it('still shows the entry count on the Prompts tab itself', async () => {
    renderAt('/team/42/resources/assets');
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: /Prompt/ }).textContent).toContain('15'),
    );
  });

  it('All keeps counting the team\'s own prompt ASSET rows', async () => {
    ctxCounts.assetCounts = { character: 2, location: 0, prop: 0, costume: 0, prompt: 3, audio: 0 };
    renderAt('/team/42/resources/assets');
    await waitFor(() => expect(tab('All').textContent).toBe('All5'));
  });
});
