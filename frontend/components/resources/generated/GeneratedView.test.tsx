import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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

import { GeneratedView } from './GeneratedView';
import { GeneratedApiError } from '../../../services/apiEnvelope';
import type { GeneratedItem } from '../../../services/generatedService';

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

    fireEvent.click(within(bar).getByRole('button', { name: 'Save' }));

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

  it('routes the batch "As Asset…" into the (Task 10) dialog rather than the API', async () => {
    renderView();
    const bar = await selectFirstTwo();

    fireEvent.click(within(bar).getByRole('button', { name: 'As Asset…' }));

    expect(await screen.findByTestId('save-as-asset-dialog')).toBeTruthy();
    expect(batchGenerated).not.toHaveBeenCalled();
  });
});

describe('GeneratedView — single-card actions', () => {
  it('saves one card, updates it in place, and refreshes the counters', async () => {
    saveGeneration.mockResolvedValue({ ...ITEM_A, review_state: 'saved' });
    renderView();
    await screen.findByText('Prompt 0');

    const card = screen.getByText('Prompt 0').closest('[data-testid="generated-card"]')!;
    fireEvent.click(within(card as HTMLElement).getByRole('button', { name: 'Save' }));

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

  it('opens the (Task 10) dialog for a single card', async () => {
    renderView();
    await screen.findByText('Prompt 0');

    const card = screen.getByText('Prompt 0').closest('[data-testid="generated-card"]')!;
    fireEvent.click(within(card as HTMLElement).getByRole('button', { name: 'As Asset…' }));

    expect(await screen.findByTestId('save-as-asset-dialog')).toBeTruthy();
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
