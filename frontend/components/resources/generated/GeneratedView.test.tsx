import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';

// A stand-in bundle, not a bare `d ?? k`: the error path resolves
// `generated.err.<code>` with `generated.err.generic` AS its default, so a
// mock that always returned the default could not tell a mapped code from an
// unmapped one.
const BUNDLE: Record<string, string> = {
  'generated.err.not_a_member': 'You are not a member of this workspace',
  'generated.err.generic': 'Something went wrong',
  'generated.source.canvas_run': 'Canvas',
  'generated.source.agent_run': 'Agent',
};
// Emulates i18next's two call forms — `t(key, 'Default')` and
// `t(key, { var, defaultValue })` — including interpolation. A mock that only
// understood the string form would silently drop every `{{var}}`.
const translate = (
  bundle: Record<string, string>,
  key: string,
  opts?: string | Record<string, unknown>,
): string => {
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
    t: (k: string, o?: string | Record<string, unknown>) => translate(BUNDLE, k, o),
  }),
}));

const addToast = vi.fn();
vi.mock('../../Toast', () => ({ useToast: () => ({ addToast }) }));

const refreshGeneratedCounts = vi.fn();
vi.mock('../../../contexts/ResourcesContext', () => ({
  useResourcesContext: () => ({
    scopeId: '727145299382534200',
    teamId: 't1',
    refreshGeneratedCounts,
  }),
}));

vi.mock('../../../services/generatedMediaService', () => ({
  generatedMediaCoverUrl: (id: string) => `https://api.test/gen/${id}/cover`,
  generatedMediaStreamUrl: (id: string) => `https://api.test/gen/${id}/stream`,
  // The FULL file, distinct from `/cover` — the lightbox must not be showing
  // the thumbnail, so the two URLs have to be distinguishable here.
  generatedMediaFileUrl: (id: string) => `https://api.test/gen/${id}/file`,
}));

// The real player decodes the file with `fetch` + `AudioContext` and observes
// its container with `ResizeObserver`; jsdom has none of the three. Stubbed to
// a marker carrying the props the lightbox is responsible for handing it.
vi.mock('../../AudioWaveformPlayer', () => ({
  AudioWaveformPlayer: ({ src, filename }: { src: string; filename: string }) => (
    <div data-testid="waveform-player" data-src={src} data-filename={filename} />
  ),
}));

const fetchProjects = vi.fn();
vi.mock('../../../services/projectsService', () => ({
  fetchProjects: (...a: unknown[]) => fetchProjects(...a),
}));

const fetchGenerated = vi.fn();
const fetchGeneratedCounts = vi.fn();
const saveGeneration = vi.fn();
const deleteGeneration = vi.fn();
const batchGenerated = vi.fn();
vi.mock('../../../services/generatedService', async () => {
  const { GeneratedApiError } = await import('../../../services/apiEnvelope');
  return {
    fetchGenerated: (...a: unknown[]) => fetchGenerated(...a),
    fetchGeneratedCounts: (...a: unknown[]) => fetchGeneratedCounts(...a),
    saveGeneration: (...a: unknown[]) => saveGeneration(...a),
    deleteGeneration: (...a: unknown[]) => deleteGeneration(...a),
    batchGenerated: (...a: unknown[]) => batchGenerated(...a),
    GeneratedApiError,
  };
});

vi.mock('./CleanupDialog', () => ({
  CleanupDialog: ({ onDone }: { onDone: () => void }) => (
    <div data-testid="cleanup-dialog">
      <button type="button" onClick={onDone}>
        cleanup-done
      </button>
    </div>
  ),
}));

// Stubbed like CleanupDialog: what this file owns is the HAND-OFF (which items
// go in, what the view does with the outcome), not the picker's own behaviour
// — that has its own suite in `components/assets/SaveAsAssetDialog.test.tsx`.
// The stub records its props so the hand-off itself stays assertable.
let dialogProps: {
  open: boolean;
  items: GeneratedItem[];
  scopeId: string;
} | null = null;
let dialogDone: ((result: SaveAsAssetOutcome) => void) | null = null;
vi.mock('../../assets/SaveAsAssetDialog', () => ({
  SaveAsAssetDialog: (props: {
    open: boolean;
    scopeId: string;
    items: GeneratedItem[];
    onClose: () => void;
    onDone: (result: SaveAsAssetOutcome) => void;
  }) => {
    dialogProps = { open: props.open, items: props.items, scopeId: props.scopeId };
    dialogDone = props.onDone;
    if (!props.open) return null;
    return <div data-testid="save-as-asset-dialog" data-count={props.items.length} />;
  },
}));

import { GeneratedView } from './GeneratedView';
import { GeneratedApiError } from '../../../services/apiEnvelope';
import type { GeneratedItem } from '../../../services/generatedService';
import type { SaveAsAssetOutcome } from '../../assets/SaveAsAssetDialog';

// ─── Fixtures: copied verbatim from wire-fixtures.json (string ids) ──────────

const ITEM_A: GeneratedItem = {
  id: '727145299382534145',
  scope_id: '727145299382534200',
  media_kind: 'image',
  mime: 'image/png',
  prompt: 'Prompt 0. more',
  model: 'gpt-image-2',
  provider: 'openai',
  origin_kind: 'canvas_run',
  canvas_id: '325005725244722',
  node_id: 'n9',
  created_at: '2026-08-28T10:00:00Z',
  promoted_resource_id: null,
  review_state: 'unreviewed',
  source_asset_id: '727145299382534201',
  source: {
    kind: 'canvas_run',
    label: 'EP1 · Storyboard · Canvas',
    canvas_id: '325005725244722',
    node_id: 'n9',
    shot_id: null,
    conversation_id: null,
    deep_link: '/team/727145299382534200/canvas/325005725244722?node=n9',
  },
  title: 'Prompt 0',
};

const ITEM_B: GeneratedItem = {
  ...ITEM_A,
  id: '727145299382534146',
  model: 'seedream-4',
  origin_kind: 'chat_upload',
  canvas_id: null,
  node_id: null,
  created_at: '2026-08-28T10:01:00Z',
  promoted_resource_id: '727145299382534301',
  review_state: 'unreviewed',
  source_asset_id: null,
  source: {
    kind: 'chat_upload',
    label: 'Chat upload',
    canvas_id: null,
    node_id: null,
    shot_id: null,
    conversation_id: null,
    deep_link: null,
  },
  title: 'Prompt 1',
};

const ITEM_C: GeneratedItem = { ...ITEM_B, id: '727145299382534147', title: 'Prompt 2' };

const COUNTS = { unreviewed: 12, saved: 87, in_assets: 41 };

let currentSearch = '';
const LocationProbe: React.FC = () => {
  currentSearch = useLocation().search;
  return null;
};

const renderView = (initialEntry = '/team/t1/resources/generated') => {
  currentSearch = '';
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <GeneratedView />
      <LocationProbe />
    </MemoryRouter>,
  );
};

const lastListOptions = () => fetchGenerated.mock.calls.at(-1)?.[1] as Record<string, unknown>;

beforeEach(() => {
  addToast.mockReset();
  refreshGeneratedCounts.mockReset();
  fetchProjects.mockReset().mockResolvedValue([{ id: 'p1', name: 'Project One', team_id: 't1' }]);
  fetchGeneratedCounts.mockReset().mockResolvedValue(COUNTS);
  fetchGenerated.mockReset().mockResolvedValue({ items: [ITEM_A, ITEM_B], next_cursor: null });
  saveGeneration.mockReset();
  deleteGeneration.mockReset();
  batchGenerated.mockReset();
  dialogProps = null;
  dialogDone = null;
});

describe('GeneratedView — tabs', () => {
  it('lands on Unreviewed and asks the router for exactly that', async () => {
    renderView();

    await waitFor(() => expect(fetchGenerated).toHaveBeenCalledTimes(1));
    expect(fetchGenerated).toHaveBeenCalledWith('727145299382534200', { state: 'unreviewed' });
    expect(currentSearch).toBe('');

    const tab = await screen.findByRole('tab', { name: /Unreviewed/ });
    expect(tab.getAttribute('aria-selected')).toBe('true');
  });

  it('shows the unreviewed count in the header chip and on every tab', async () => {
    renderView();

    expect(await screen.findByText('12 unreviewed')).toBeTruthy();
    expect(within(screen.getByRole('tab', { name: /Saved/ })).getByText('87')).toBeTruthy();
    expect(within(screen.getByRole('tab', { name: /In Assets/ })).getByText('41')).toBeTruthy();
  });

  it('writes the tab into the URL and refetches', async () => {
    renderView();
    await waitFor(() => expect(fetchGenerated).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('tab', { name: /In Assets/ }));

    await waitFor(() => expect(currentSearch).toBe('?state=in_assets'));
    await waitFor(() => expect(fetchGenerated).toHaveBeenCalledTimes(2));
    expect(lastListOptions()).toEqual({ state: 'in_assets' });
  });

  it('honours a tab supplied in the URL on first load', async () => {
    renderView('/team/t1/resources/generated?state=saved');

    await waitFor(() => expect(fetchGenerated).toHaveBeenCalledWith('727145299382534200', {
      state: 'saved',
    }));
    expect(screen.getByRole('tab', { name: /Saved/ }).getAttribute('aria-selected')).toBe('true');
  });
});

describe('GeneratedView — filter chips', () => {
  it('puts a source selection in the URL as a repeated param and in the request', async () => {
    renderView();
    await waitFor(() => expect(fetchGenerated).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: /^Source/ }));
    fireEvent.click(await screen.findByRole('menuitemcheckbox', { name: 'Canvas' }));

    await waitFor(() => expect(currentSearch).toBe('?origin_kind=canvas_run'));
    await waitFor(() =>
      expect(lastListOptions()).toEqual({ state: 'unreviewed', originKinds: ['canvas_run'] }),
    );
  });

  it('offers the models present on the loaded page', async () => {
    renderView();
    await screen.findByText('Prompt 0');

    fireEvent.click(screen.getByRole('button', { name: /^Model/ }));
    expect(await screen.findByRole('menuitemradio', { name: 'gpt-image-2' })).toBeTruthy();
    expect(screen.getByRole('menuitemradio', { name: 'seedream-4' })).toBeTruthy();

    fireEvent.click(screen.getByRole('menuitemradio', { name: 'seedream-4' }));
    await waitFor(() => expect(currentSearch).toBe('?model=seedream-4'));
  });

  it('offers all four media kinds in the Type chip and filters by one', async () => {
    // Audio and File are reachable rows, not hypotheticals: audio is what P6's
    // My Uploads → As Asset mints, file is a chat upload that is neither
    // image nor video. A Type chip that lists only two cannot narrow to them.
    renderView();
    await screen.findByText('Prompt 0');

    fireEvent.click(screen.getByRole('button', { name: /^Type/ }));
    for (const label of ['Image', 'Video', 'Audio', 'File']) {
      expect(await screen.findByRole('menuitemradio', { name: label })).toBeTruthy();
    }

    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Audio' }));
    await waitFor(() => expect(currentSearch).toBe('?media_kind=audio'));
    await waitFor(() =>
      expect(lastListOptions()).toEqual({ state: 'unreviewed', mediaKind: 'audio' }),
    );
  });

  it('lists projects for the scope in the Project chip', async () => {
    renderView();
    await screen.findByText('Prompt 0');

    fireEvent.click(screen.getByRole('button', { name: /^Project/ }));
    fireEvent.click(await screen.findByRole('menuitemradio', { name: 'Project One' }));

    await waitFor(() => expect(currentSearch).toBe('?project_id=p1'));
    await waitFor(() =>
      expect(lastListOptions()).toEqual({ state: 'unreviewed', projectId: 'p1' }),
    );
  });

  it('says so in the Project popover when the project list could not be fetched', async () => {
    // A failed fetch must not read as "this workspace has no projects".
    fetchProjects.mockRejectedValueOnce(new Error('network down'));
    renderView();
    await screen.findByText('Prompt 0');

    fireEvent.click(screen.getByRole('button', { name: /^Project/ }));

    expect(await screen.findByText('Projects unavailable')).toBeTruthy();
    // The chip stays usable: clearing an active filter must still work.
    expect(screen.getByRole('menuitemradio', { name: 'Any Project' })).toBeTruthy();
  });

  it('shows no failure notice when the list is merely empty', async () => {
    fetchProjects.mockResolvedValueOnce([]);
    renderView();
    await screen.findByText('Prompt 0');

    fireEvent.click(screen.getByRole('button', { name: /^Project/ }));
    await screen.findByRole('menuitemradio', { name: 'Any Project' });
    expect(screen.queryByText('Projects unavailable')).toBeNull();
  });

  it('turns a Date preset into an ISO instant on the request but keeps the preset in the URL', async () => {
    renderView();
    await waitFor(() => expect(fetchGenerated).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: /^Date/ }));
    fireEvent.click(await screen.findByRole('menuitemradio', { name: 'Last 7 Days' }));

    await waitFor(() => expect(currentSearch).toBe('?since=7d'));
    await waitFor(() => {
      const since = lastListOptions()?.since;
      expect(typeof since).toBe('string');
      expect(String(since)).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    });
  });
});

describe('GeneratedView — paging', () => {
  it('appends the next page instead of replacing the list', async () => {
    fetchGenerated.mockResolvedValueOnce({ items: [ITEM_A, ITEM_B], next_cursor: 'cur-1' });
    renderView();

    await screen.findByText('Prompt 0');
    fetchGenerated.mockResolvedValueOnce({ items: [ITEM_C], next_cursor: null });
    fireEvent.click(screen.getByRole('button', { name: 'Load More' }));

    expect(await screen.findByText('Prompt 2')).toBeTruthy();
    expect(screen.getByText('Prompt 0')).toBeTruthy();
    expect(lastListOptions()?.cursor).toBe('cur-1');
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Load More' })).toBeNull(),
    );
  });

  it('offers no Load More when the page is the last one', async () => {
    renderView();
    await screen.findByText('Prompt 0');
    expect(screen.queryByRole('button', { name: 'Load More' })).toBeNull();
  });
});

describe('GeneratedView — batch bar', () => {
  const selectFirstTwo = async () => {
    await screen.findByText('Prompt 0');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Prompt 0' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Prompt 1' }));
    return screen.getByTestId('generated-batch-bar');
  };

  it('appears once something is selected and reports the count', async () => {
    renderView();
    expect(screen.queryByTestId('generated-batch-bar')).toBeNull();

    const bar = await selectFirstTwo();
    expect(within(bar).getByText('2 selected')).toBeTruthy();
  });

  it('saves the selection through batchGenerated and summarises partial failure', async () => {
    batchGenerated.mockResolvedValue({
      ok: ['727145299382534145'],
      failed: [
        { id: '727145299382534146', code: 'invalid_slot', detail: "Slot 'flat' is not valid" },
      ],
    });
    renderView();
    const bar = await selectFirstTwo();

    fireEvent.click(within(bar).getByRole('button', { name: 'Save To Uploads' }));

    await waitFor(() =>
      expect(batchGenerated).toHaveBeenCalledWith('727145299382534200', {
        ids: ['727145299382534145', '727145299382534146'],
        action: 'save',
      }),
    );
    // The failed half must be nameable — "1 done" alone would report a
    // half-applied batch as a success.
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith(
        expect.stringContaining('1 done, 1 failed'),
        'error',
      ),
    );
    expect(addToast.mock.calls.at(-1)?.[0]).toContain('invalid_slot');
    expect(refreshGeneratedCounts).toHaveBeenCalled();
  });

  it('requires a confirm before the batch delete reaches the server', async () => {
    batchGenerated.mockResolvedValue({ ok: ['727145299382534145', '727145299382534146'], failed: [] });
    renderView();
    const bar = await selectFirstTwo();

    fireEvent.click(within(bar).getByRole('button', { name: 'Delete' }));
    expect(batchGenerated).not.toHaveBeenCalled();

    fireEvent.click(within(bar).getByRole('button', { name: 'Confirm Delete (2)' }));
    await waitFor(() =>
      expect(batchGenerated).toHaveBeenCalledWith('727145299382534200', {
        ids: ['727145299382534145', '727145299382534146'],
        action: 'delete',
      }),
    );
    await waitFor(() => expect(screen.queryByText('Prompt 0')).toBeNull());
  });

  it('retracts a pending batch confirm when the selection changes', async () => {
    renderView();
    const bar = await selectFirstTwo();

    fireEvent.click(within(bar).getByRole('button', { name: 'Delete' }));
    expect(within(bar).getByRole('button', { name: 'Confirm Delete (2)' })).toBeTruthy();

    // Deselecting one leaves a confirm that would quote the wrong count.
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Prompt 1' }));
    expect(within(bar).queryByRole('button', { name: /^Confirm Delete/ })).toBeNull();
    expect(within(bar).getByRole('button', { name: 'Delete' })).toBeTruthy();
    expect(batchGenerated).not.toHaveBeenCalled();
  });

  it('clears the selection without touching the server', async () => {
    renderView();
    const bar = await selectFirstTwo();

    fireEvent.click(within(bar).getByRole('button', { name: 'Clear' }));

    await waitFor(() => expect(screen.queryByTestId('generated-batch-bar')).toBeNull());
    expect(batchGenerated).not.toHaveBeenCalled();
  });

  it('routes the batch "As Asset" into the dialog rather than the API', async () => {
    renderView();
    const bar = await selectFirstTwo();

    fireEvent.click(within(bar).getByRole('button', { name: 'As Asset' }));

    expect(await screen.findByTestId('save-as-asset-dialog')).toBeTruthy();
    // The dialog owns the call; the view must not have fired one of its own.
    expect(batchGenerated).not.toHaveBeenCalled();
    expect(dialogProps?.items.map((i) => i.id)).toEqual([ITEM_A.id, ITEM_B.id]);
  });

  it('refuses batch Delete when the selection contains a promoted row', async () => {
    // A saved row's bytes belong to a `resources` row now; the content-
    // addressed key is shared, so "delete this card" must never be a route to
    // "delete my file". The per-card menu already hides Delete outside
    // Unreviewed — the batch bar was the hole.
    fetchGenerated
      .mockReset()
      .mockResolvedValue({
        items: [ITEM_A, { ...ITEM_B, review_state: 'saved' }],
        next_cursor: null,
      });
    renderView();
    const bar = await selectFirstTwo();

    const del = within(bar).getByRole('button', { name: 'Delete' });
    expect((del as HTMLButtonElement).disabled).toBe(true);
    expect(del.getAttribute('title')).toContain('Only unreviewed items');

    fireEvent.click(del);
    expect(within(bar).queryByRole('button', { name: /^Confirm Delete/ })).toBeNull();
    expect(batchGenerated).not.toHaveBeenCalled();
  });

  it('still allows batch Delete for an all-unreviewed selection', async () => {
    // Positive control: without it the test above passes on a Delete button
    // that is disabled unconditionally.
    renderView();
    const bar = await selectFirstTwo();

    const del = within(bar).getByRole('button', { name: 'Delete' });
    expect((del as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(del);
    expect(within(bar).getByRole('button', { name: 'Confirm Delete (2)' })).toBeTruthy();
  });
});

describe('GeneratedView — single-card actions', () => {
  it('saves one card, updates it in place, and refreshes the counters', async () => {
    saveGeneration.mockResolvedValue({ ...ITEM_A, review_state: 'saved' });
    renderView();
    await screen.findByText('Prompt 0');

    const card = screen.getByText('Prompt 0').closest('[data-testid="generated-card"]')!;
    fireEvent.click(within(card as HTMLElement).getByRole('button', { name: 'Save To Uploads' }));

    await waitFor(() =>
      expect(saveGeneration).toHaveBeenCalledWith('727145299382534200', '727145299382534145'),
    );
    // Patched in place — no second list fetch.
    await waitFor(() => expect(card.getAttribute('data-review-state')).toBe('saved'));
    expect(fetchGenerated).toHaveBeenCalledTimes(1);
    expect(refreshGeneratedCounts).toHaveBeenCalled();
  });

  it('drops a deleted card from the list', async () => {
    deleteGeneration.mockResolvedValue(undefined);
    renderView();
    await screen.findByText('Prompt 0');

    const card = screen.getByText('Prompt 0').closest('[data-testid="generated-card"]')!;
    fireEvent.click(within(card as HTMLElement).getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(card as HTMLElement).getByRole('button', { name: 'Confirm Delete' }));

    await waitFor(() => expect(screen.queryByText('Prompt 0')).toBeNull());
    expect(screen.getByText('Prompt 1')).toBeTruthy();
    expect(refreshGeneratedCounts).toHaveBeenCalled();
  });

  it('opens the dialog for a single card, with just that card', async () => {
    renderView();
    await screen.findByText('Prompt 0');

    const card = screen.getByText('Prompt 0').closest('[data-testid="generated-card"]')!;
    fireEvent.click(within(card as HTMLElement).getByRole('button', { name: 'As Asset' }));

    expect(await screen.findByTestId('save-as-asset-dialog')).toBeTruthy();
    expect(dialogProps?.items.map((i) => i.id)).toEqual([ITEM_A.id]);
    expect(dialogProps?.scopeId).toBe('727145299382534200');
  });

  it('keeps the dialog closed until a card asks for it', async () => {
    renderView();
    await screen.findByText('Prompt 0');

    expect(screen.queryByTestId('save-as-asset-dialog')).toBeNull();
    expect(dialogProps?.open).toBe(false);
  });

  it('moves an attached card to in_assets in place, without refetching', async () => {
    renderView();
    await screen.findByText('Prompt 0');

    const card = screen.getByText('Prompt 0').closest('[data-testid="generated-card"]')!;
    fireEvent.click(within(card as HTMLElement).getByRole('button', { name: 'As Asset' }));
    await screen.findByTestId('save-as-asset-dialog');

    act(() => dialogDone!({
      attachedCount: 1,
      failed: [],
      assetId: '727145299382534201',
      assetName: 'Sang Yao',
      slot: 'stills',
    }));

    await waitFor(() => expect(card.getAttribute('data-review-state')).toBe('in_assets'));
    // Patched in place — the list was not asked for again.
    expect(fetchGenerated).toHaveBeenCalledTimes(1);
    expect(refreshGeneratedCounts).toHaveBeenCalled();
    // …and the card now points at the asset it joined.
    expect(
      within(card as HTMLElement).getByRole('button', { name: 'Open asset' }),
    ).toBeTruthy();
  });

  it('leaves the ids the batch refused exactly where they were', async () => {
    renderView();
    await screen.findByText('Prompt 0');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Prompt 0' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Prompt 1' }));
    fireEvent.click(
      within(screen.getByTestId('generated-batch-bar')).getByRole('button', {
        name: 'As Asset',
      }),
    );
    await screen.findByTestId('save-as-asset-dialog');

    act(() => dialogDone!({
      attachedCount: 1,
      failed: [{ id: ITEM_B.id, code: 'invalid_slot' }],
      assetId: '727145299382534201',
      assetName: 'Sang Yao',
      slot: 'stills',
    }));

    const cardA = screen.getByText('Prompt 0').closest('[data-testid="generated-card"]')!;
    const cardB = screen.getByText('Prompt 1').closest('[data-testid="generated-card"]')!;
    await waitFor(() => expect(cardA.getAttribute('data-review-state')).toBe('in_assets'));
    // The refused one did not become an asset — saying it did would be the UI
    // claiming a success the server declined.
    expect(cardB.getAttribute('data-review-state')).toBe('unreviewed');
    // …and it stays selected, so it is still the thing you can retry.
    expect(screen.getByTestId('generated-batch-bar').textContent).toContain('1 selected');
  });
});

describe('GeneratedView — failures and empty states', () => {
  it('maps a typed refusal onto its own i18n key', async () => {
    fetchGenerated.mockRejectedValueOnce(
      new GeneratedApiError(403, 'not_a_member', 'You are not a member of this scope'),
    );
    renderView();

    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('You are not a member of this workspace', 'error'),
    );
  });

  it('falls back to the generic message for a code with no translation', async () => {
    fetchGenerated.mockRejectedValueOnce(new GeneratedApiError(500, 'http_500', 'boom'));
    renderView();

    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('Something went wrong', 'error'),
    );
  });

  it('a failed list says the fetch failed, NOT that the inbox is empty', async () => {
    // Back-ported from `AssetShelf`'s `loadError`. The toast fades; the view
    // does not. Rendering the empty state here tells the user their
    // generations are gone when the request simply never landed.
    fetchGenerated.mockRejectedValueOnce(new GeneratedApiError(500, 'http_500', 'boom'));
    renderView();

    const alert = await screen.findByTestId('generated-load-error');
    expect(alert.getAttribute('role')).toBe('alert');
    // The falsifiable half: with the flag missing, THIS is what renders.
    expect(screen.queryByText('Nothing To Review')).toBeNull();
    // The toast is still raised — the line and the toast are both owed.
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('Something went wrong', 'error'),
    );
  });

  it('says there is nothing to review on an empty unreviewed tab', async () => {
    fetchGenerated.mockResolvedValue({ items: [], next_cursor: null });
    renderView();

    expect(await screen.findByText('Nothing To Review')).toBeTruthy();
  });

  it('explains the project filter when it hides everything', async () => {
    fetchGenerated.mockResolvedValue({ items: [], next_cursor: null });
    renderView('/team/t1/resources/generated?project_id=p1');

    expect(
      await screen.findByText('Only Canvas Generations Can Be Filtered By Project'),
    ).toBeTruthy();
  });
});

describe('GeneratedView — clean up', () => {
  it('opens the dialog and reloads once it reports a deletion', async () => {
    renderView();
    await screen.findByText('Prompt 0');

    fireEvent.click(screen.getByRole('button', { name: 'Clean Up…' }));
    fireEvent.click(await screen.findByRole('button', { name: 'cleanup-done' }));

    await waitFor(() => expect(fetchGenerated).toHaveBeenCalledTimes(2));
    expect(refreshGeneratedCounts).toHaveBeenCalled();
  });
});


// ─── Intermediate canvas inputs ─────────────────────────────────────────────

describe('GeneratedView — Intermediate Inputs filter', () => {
  it('is off on first load and asks for nothing extra', async () => {
    renderView();
    await waitFor(() => expect(fetchGenerated).toHaveBeenCalledTimes(1));
    expect(lastListOptions().includeIntermediate).toBeUndefined();
  });

  it('turns the flag on, writes it to the URL and refetches', async () => {
    renderView();
    await waitFor(() => expect(fetchGenerated).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: /^Source/ }));
    fireEvent.click(
      await screen.findByRole('menuitemcheckbox', { name: 'Intermediate Inputs' }),
    );

    await waitFor(() => expect(currentSearch).toBe('?include_intermediate=true'));
    await waitFor(() =>
      expect(lastListOptions()).toEqual({
        state: 'unreviewed',
        includeIntermediate: true,
      }),
    );
  });

  it('is restored from the URL, not just settable from the menu', async () => {
    // A pasted link has to reproduce the view. Without parsing it back the
    // toggle would look off while the page was showing masks.
    renderView('/team/t1/resources/generated?include_intermediate=true');
    await waitFor(() => expect(fetchGenerated).toHaveBeenCalledTimes(1));
    expect(lastListOptions().includeIntermediate).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: /^Source/ }));
    expect(
      (await screen.findByRole('menuitemcheckbox', { name: 'Intermediate Inputs' }))
        .getAttribute('aria-checked'),
    ).toBe('true');
  });
});

// ─── Lightbox ───────────────────────────────────────────────────────────────

describe('GeneratedView — preview lightbox', () => {
  const openFirstCard = async () => {
    renderView();
    await waitFor(() => expect(fetchGenerated).toHaveBeenCalledTimes(1));
    const cards = await screen.findAllByTestId('generated-card');
    fireEvent.click(within(cards[0] as HTMLElement).getByRole('button', { name: /^Preview/ }));
  };

  it('opens from the thumbnail on the FULL file, not the thumbnail URL', async () => {
    await openFirstCard();

    const lightbox = await screen.findByTestId('pin-lightbox');
    expect(lightbox).toBeTruthy();
    expect(screen.getByTestId('pin-lightbox-image').getAttribute('src')).toBe(
      'https://api.test/gen/727145299382534145/file',
    );
  });

  it('shows the metadata panel with source, model, state and prompt', async () => {
    await openFirstCard();

    const meta = await screen.findByTestId('lightbox-metadata');
    expect(meta.textContent).toContain('EP1 · Storyboard · Canvas');
    expect(meta.textContent).toContain('gpt-image-2');
    expect(meta.textContent).toContain('openai');
    expect(meta.textContent).toContain('Prompt 0. more');
  });

  it('deep-links from the metadata panel', async () => {
    await openFirstCard();

    fireEvent.click(await screen.findByTestId('lightbox-source-link'));
    await waitFor(() =>
      expect(currentSearch).toBe('?node=n9'),
    );
  });

  it('carries the same three actions the card offers', async () => {
    await openFirstCard();

    const panel = await screen.findByTestId('pin-lightbox-panel');
    expect(within(panel).getByRole('button', { name: /Save To Uploads/ })).toBeTruthy();
    expect(within(panel).getByRole('button', { name: /As Asset/ })).toBeTruthy();
    expect(within(panel).getByRole('button', { name: /^Delete/ })).toBeTruthy();
  });

  it('runs the save action for the item on screen', async () => {
    saveGeneration.mockResolvedValue({ ...ITEM_A, review_state: 'saved' });
    await openFirstCard();

    const panel = await screen.findByTestId('pin-lightbox-panel');
    fireEvent.click(within(panel).getByRole('button', { name: /Save To Uploads/ }));

    await waitFor(() =>
      expect(saveGeneration).toHaveBeenCalledWith('727145299382534200', ITEM_A.id),
    );
  });

  it('keeps the delete confirmation gate', async () => {
    deleteGeneration.mockResolvedValue(undefined);
    await openFirstCard();

    const panel = await screen.findByTestId('pin-lightbox-panel');
    fireEvent.click(within(panel).getByRole('button', { name: /^Delete/ }));
    expect(deleteGeneration).not.toHaveBeenCalled();

    fireEvent.click(
      within(await screen.findByTestId('pin-lightbox-panel')).getByRole('button', {
        name: /Confirm Delete/,
      }),
    );
    await waitFor(() =>
      expect(deleteGeneration).toHaveBeenCalledWith('727145299382534200', ITEM_A.id),
    );
    // Closed BEFORE the row leaves the list: an index left pointing into a
    // shrunken array shows the user a different generation than the one they
    // just deleted.
    await waitFor(() => expect(screen.queryByTestId('pin-lightbox')).toBeNull());
  });

  it('plays a video generation as a video, not a broken <img>', async () => {
    fetchGenerated.mockResolvedValue({
      items: [{ ...ITEM_A, media_kind: 'video' }],
      next_cursor: null,
    });
    await openFirstCard();

    expect(await screen.findByTestId('pin-lightbox-video')).toBeTruthy();
    expect(screen.queryByTestId('pin-lightbox-image')).toBeNull();
  });

  it('plays an audio row through /stream — no placeholder, actions intact', async () => {
    fetchGenerated.mockResolvedValue({
      items: [{ ...ITEM_A, media_kind: 'audio', mime: 'audio/mpeg' }],
      next_cursor: null,
    });
    await openFirstCard();

    const audio = await screen.findByTestId('pin-lightbox-audio');
    expect(audio.getAttribute('data-resource-id')).toBe(ITEM_A.id);
    expect(screen.queryByTestId('pin-lightbox-placeholder')).toBeNull();
    expect(screen.queryByTestId('pin-lightbox-image')).toBeNull();
    expect(screen.queryByTestId('pin-lightbox-video')).toBeNull();

    // `/stream`, not `/file`: the player fetches the bytes itself and can
    // carry no Bearer header, and `/stream` is the Range-capable public route.
    const player = screen.getByTestId('waveform-player');
    expect(player.getAttribute('data-src')).toBe(`https://api.test/gen/${ITEM_A.id}/stream`);
    expect(player.getAttribute('data-filename')).toBe(ITEM_A.title);

    // Playing it must not have cost the row its triage actions.
    const panel = await screen.findByTestId('pin-lightbox-panel');
    expect(within(panel).getByRole('button', { name: /Save To Uploads/ })).toBeTruthy();
    expect(within(panel).getByRole('button', { name: /As Asset/ })).toBeTruthy();
  });

  it('opens a `file` row on the generic placeholder', async () => {
    fetchGenerated.mockResolvedValue({
      items: [{ ...ITEM_A, media_kind: 'file', mime: 'application/pdf' }],
      next_cursor: null,
    });
    await openFirstCard();

    const placeholder = await screen.findByTestId('pin-lightbox-placeholder');
    expect(placeholder.getAttribute('data-media-kind')).toBe('file');
    expect(placeholder.querySelector('.lucide-file')).toBeTruthy();
    expect(screen.queryByTestId('pin-lightbox-image')).toBeNull();
  });
});
