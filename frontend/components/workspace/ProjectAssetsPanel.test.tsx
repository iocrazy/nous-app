/**
 * ProjectAssetsPanel — the project workspace's view over the asset library.
 *
 * The fixtures are REAL wire rows (`GET /projects/{id}/assets` serializes an
 * `assets` row exactly as `GET /assets` does): string ids throughout,
 * `scope_id` non-null on everything a project can reference (only global
 * presets carry null, and a preset can never be project-linked), `tags` as an
 * object of group → values, sparse `file_counts_by_slot`.
 *
 * What each block is really pinning:
 *
 *  * EMPTY vs ERROR. The two states differ by one boolean and render two
 *    different sentences; collapsing them tells a user whose request failed
 *    that their project has no cast.
 *  * UNLINK sends the ROW's own `scope_id`, not the panel's resolved one, and
 *    refetches instead of splicing its own list.
 *  * IMPORT renders per-item failures, not just tallies. `created + linked +
 *    skipped` can be a cheerful summary over names that were refused — and an
 *    item can be BOTH `created` and carry a `code` (the asset landed, the ref
 *    did not), which is the case a naive "failures are the skipped ones"
 *    filter drops on the floor.
 *  * NEW creates, then LINKS, then refetches — and does NOT navigate away.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const translate = (key: string, opts?: string | Record<string, unknown>): string => {
  const bundle: Record<string, string> = {
    'assets.types.character': 'Characters',
    'assets.types.location': 'Locations',
    'assets.types.prop': 'Props',
    'assets.types.costume': 'Costumes',
    'saveAsAsset.type.character': 'Character',
    'saveAsAsset.type.location': 'Location',
    'saveAsAsset.type.costume': 'Costume',
    'assets.newOfType': 'New {{type}}',
    'assets.importFromScript': 'Import From Script',
    'assets.project.linkFromLibrary': 'Link From Library',
    'assets.project.count': '{{n}} Linked',
    'assets.project.empty': 'No {{type}} In This Project Yet — Link One Or Create One',
    'assets.project.loadFailed': 'Could Not Load This Project’s Assets',
    'assets.project.linked': 'Added To This Project',
    'assets.project.unlink': 'Remove {{name}} From This Project',
    'assets.project.unlinked': 'Removed From This Project — Still In Your Library',
    'assets.project.importDone':
      'Imported — {{created}} Created, {{linked}} Linked, {{skipped}} Skipped',
    'assets.project.importSkipped': '{{name}} ({{type}}) — {{reason}}',
    'assets.project.importTypeCount': '{{type}} {{n}}',
    'assets.project.importNothing': 'Your Scripts Name No Characters Or Locations Yet',
    'assets.project.scopeUnknown': 'Workspace Not Resolved Yet',
    'assets.err.empty_name': 'The Script Left This Name Blank',
    'assets.err.project_scope_mismatch': 'That Project Belongs To A Different Workspace',
    'assets.err.generic': 'Something went wrong. Please try again.',
    'assets.readiness.ready': 'Ready',
    'assets.readiness.draft': 'Draft',
    'assets.card.open': 'Open {{name}}',
    'assets.card.missing': 'Missing: {{slots}}',
    'assets.card.coverage': '{{filled}} of {{total}} slots filled',
    'common.loading': 'Loading…',
    'common.close': 'Close',
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
vi.mock('../Toast', () => ({ useToast: () => ({ addToast }) }));

const navigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigate };
});

vi.mock('../../services/resourceService', () => ({
  getResourceCoverUrl: (id: string) => `https://api.test/resources/${id}/cover`,
}));

const listProjectAssets = vi.fn();
const linkProject = vi.fn();
const unlinkProject = vi.fn();
const importFromScript = vi.fn();
const searchAssets = vi.fn();
vi.mock('../../services/assetsService', async () => {
  const { GeneratedApiError } = await import('../../services/apiEnvelope');
  return {
    listProjectAssets: (...a: unknown[]) => listProjectAssets(...a),
    linkProject: (...a: unknown[]) => linkProject(...a),
    unlinkProject: (...a: unknown[]) => unlinkProject(...a),
    importFromScript: (...a: unknown[]) => importFromScript(...a),
    searchAssets: (...a: unknown[]) => searchAssets(...a),
    GeneratedApiError,
  };
});

// The dialog's own validation and its 409 branch have their own suite; this
// file owns the HAND-OFF — does a create get linked to the project, and does
// the panel stay put afterwards?
vi.mock('../resources/assets/NewAssetDialog', () => ({
  NewAssetDialog: ({
    assetType,
    onCreated,
    onOpenExisting,
  }: {
    assetType: string;
    onCreated: (a: { id: string }) => void;
    onOpenExisting: (id: string) => void;
  }) => (
    <div data-testid="new-asset-dialog" data-dialog-type={assetType}>
      <button type="button" onClick={() => onCreated({ id: '727145299382534311' })}>
        stub-create
      </button>
      <button type="button" onClick={() => onOpenExisting('727145299382534322')}>
        stub-existing
      </button>
    </div>
  ),
}));

import { GeneratedApiError } from '../../services/apiEnvelope';
import { ProjectAssetsPanel } from './ProjectAssetsPanel';
import type { AssetRow, ImportFromScriptResponse } from '../../services/assetsService';

const SCOPE = '727145299382534200';
const PROJECT = '727145299382534055';

function row(over: Partial<AssetRow> = {}): AssetRow {
  return {
    id: '727145299382534300',
    scope_id: SCOPE,
    asset_type: 'character',
    subtype: null,
    name: 'Sang Yao',
    role_tag: 'lead',
    description: 'Late twenties, wind-burnt.',
    attrs: {},
    prompt_positive: null,
    prompt_negative: null,
    prompt_positive_zh: null,
    prompt_negative_zh: null,
    platform_params: {},
    cover_file_id: null,
    source: 'manual',
    duplicated_from: null,
    is_system_preset: false,
    tags: { role: ['lead'] },
    sort_order: 0,
    created_by: '11111111-1111-1111-1111-111111111111',
    created_at: '2026-08-31T10:00:00Z',
    updated_at: '2026-08-31T10:00:00Z',
    readiness: { state: 'ready', missing: [] },
    file_counts_by_slot: { sheet: 1 },
    project_ids: [PROJECT],
    loadout_count: 0,
    ...over,
  };
}

function mount(over: Partial<React.ComponentProps<typeof ProjectAssetsPanel>> = {}) {
  return render(
    <MemoryRouter>
      <ProjectAssetsPanel
        assetType="character"
        projectId={PROJECT}
        scopeId={SCOPE}
        teamId="42"
        {...over}
      />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listProjectAssets.mockResolvedValue([]);
  linkProject.mockResolvedValue(undefined);
  unlinkProject.mockResolvedValue(undefined);
  searchAssets.mockResolvedValue([]);
});

describe('ProjectAssetsPanel — the grid', () => {
  it('asks the project-scoped route for its own type and renders the rows', async () => {
    listProjectAssets.mockResolvedValue([row(), row({ id: '727145299382534301', name: 'Lin Mo' })]);
    mount();

    await waitFor(() => expect(screen.getAllByTestId('asset-card')).toHaveLength(2));
    // No `scope_id` on this call — the route derives the scope from the
    // project's OWNER, which is the whole reason the client omits it.
    expect(listProjectAssets).toHaveBeenCalledWith(PROJECT, 'character');
    expect(screen.getByTestId('project-assets-count').textContent).toBe('2 Linked');
  });

  it.each([
    ['character', 'Characters'],
    ['location', 'Locations'],
    ['prop', 'Props'],
    ['costume', 'Costumes'],
  ] as const)('renders the %s panel over the same grid', async (type, heading) => {
    listProjectAssets.mockResolvedValue([row({ asset_type: type })]);
    mount({ assetType: type });

    await waitFor(() => expect(screen.getByTestId('asset-card')).toBeTruthy());
    expect(listProjectAssets).toHaveBeenCalledWith(PROJECT, type);
    expect(screen.getByRole('heading', { name: heading })).toBeTruthy();
    expect(screen.getByTestId('project-assets-panel').getAttribute('data-asset-type')).toBe(type);
  });

  it('opens the resources module’s asset sheet on a card click', async () => {
    listProjectAssets.mockResolvedValue([row()]);
    mount();

    await waitFor(() => expect(screen.getByTestId('asset-card')).toBeTruthy());
    fireEvent.click(screen.getByTestId('asset-card'));
    expect(navigate).toHaveBeenCalledWith('/team/42/resources/assets/item/727145299382534300');
  });

  it('drops the team segment in the personal workspace', async () => {
    listProjectAssets.mockResolvedValue([row()]);
    mount({ teamId: undefined });

    await waitFor(() => expect(screen.getByTestId('asset-card')).toBeTruthy());
    fireEvent.click(screen.getByTestId('asset-card'));
    expect(navigate).toHaveBeenCalledWith('/resources/assets/item/727145299382534300');
  });
});

describe('ProjectAssetsPanel — empty is not error', () => {
  it('invites the user to link or create when the project has none', async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId('project-assets-empty')).toBeTruthy());
    expect(screen.queryByTestId('project-assets-error')).toBeNull();
  });

  it('says the load FAILED when it failed, and reports the refusal', async () => {
    listProjectAssets.mockRejectedValue(
      new GeneratedApiError(403, 'not_a_member', 'Not a member'),
    );
    mount();

    const alert = await screen.findByTestId('project-assets-error');
    expect(alert.getAttribute('role')).toBe('alert');
    // The empty-state invitation must NOT be what a failed request renders.
    expect(screen.queryByTestId('project-assets-empty')).toBeNull();
    expect(addToast).toHaveBeenCalled();
  });
});

describe('ProjectAssetsPanel — unlink', () => {
  it('sends the ROW’s own scope, then refetches instead of splicing', async () => {
    // A row whose scope differs from the panel's resolved one: only the row
    // knows which library the asset actually lives in.
    const other = '727145299382534999';
    listProjectAssets.mockResolvedValueOnce([row({ scope_id: other })]).mockResolvedValueOnce([]);
    mount();

    await waitFor(() => expect(screen.getByTestId('unlink-asset')).toBeTruthy());
    fireEvent.click(screen.getByTestId('unlink-asset'));

    await waitFor(() =>
      expect(unlinkProject).toHaveBeenCalledWith(other, '727145299382534300', PROJECT),
    );
    await waitFor(() => expect(listProjectAssets).toHaveBeenCalledTimes(2));
    expect(await screen.findByTestId('project-assets-empty')).toBeTruthy();
  });

  it('says the asset is still in the library', async () => {
    listProjectAssets.mockResolvedValue([row()]);
    mount();

    await waitFor(() => expect(screen.getByTestId('unlink-asset')).toBeTruthy());
    fireEvent.click(screen.getByTestId('unlink-asset'));

    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith(
        'Removed From This Project — Still In Your Library',
        'success',
      ),
    );
  });

  it('reports a refusal and leaves the row where it is', async () => {
    listProjectAssets.mockResolvedValue([row()]);
    unlinkProject.mockRejectedValue(
      new GeneratedApiError(404, 'project_ref_not_found', 'Not linked'),
    );
    mount();

    await waitFor(() => expect(screen.getByTestId('unlink-asset')).toBeTruthy());
    fireEvent.click(screen.getByTestId('unlink-asset'));

    await waitFor(() => expect(addToast).toHaveBeenCalled());
    // One load: a failed unlink must not be followed by a refetch that would
    // read as the row having gone.
    expect(listProjectAssets).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('asset-card')).toBeTruthy();
  });
});

describe('ProjectAssetsPanel — + New', () => {
  it('creates, links to THIS project, refetches, and stays put', async () => {
    listProjectAssets.mockResolvedValueOnce([]).mockResolvedValueOnce([row()]);
    mount();

    await waitFor(() => expect(screen.getByTestId('new-asset')).toBeTruthy());
    fireEvent.click(screen.getByTestId('new-asset'));
    fireEvent.click(screen.getByText('stub-create'));

    await waitFor(() =>
      expect(linkProject).toHaveBeenCalledWith(SCOPE, '727145299382534311', PROJECT),
    );
    await waitFor(() => expect(listProjectAssets).toHaveBeenCalledTimes(2));
    // The shelf navigates to the new asset's sheet; here the user is building
    // a cast and expects to keep adding.
    expect(navigate).not.toHaveBeenCalled();
    expect(screen.queryByTestId('new-asset-dialog')).toBeNull();
  });

  it('links the COLLIDING asset when the name is already taken', async () => {
    mount();
    fireEvent.click(screen.getByTestId('new-asset'));
    fireEvent.click(screen.getByText('stub-existing'));

    await waitFor(() =>
      expect(linkProject).toHaveBeenCalledWith(SCOPE, '727145299382534322', PROJECT),
    );
    expect(navigate).not.toHaveBeenCalled();
  });

  it('reports a failed ref without pretending the create failed', async () => {
    linkProject.mockRejectedValue(
      new GeneratedApiError(422, 'project_scope_mismatch', 'Different team'),
    );
    mount();

    fireEvent.click(screen.getByTestId('new-asset'));
    fireEvent.click(screen.getByText('stub-create'));

    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith(
        'That Project Belongs To A Different Workspace',
        'error',
      ),
    );
  });

  it('turns the write actions off, with a reason, when no scope resolved', async () => {
    mount({ scopeId: null });
    await waitFor(() => expect(screen.getByTestId('project-assets-empty')).toBeTruthy());

    const create = screen.getByTestId('new-asset') as HTMLButtonElement;
    const link = screen.getByTestId('link-from-library') as HTMLButtonElement;
    expect(create.disabled).toBe(true);
    expect(link.disabled).toBe(true);
    expect(create.title).toBe('Workspace Not Resolved Yet');
    // The READ still works — that route needs no scope.
    expect(listProjectAssets).toHaveBeenCalledWith(PROJECT, 'character');
  });
});

describe('ProjectAssetsPanel — import from script', () => {
  const report = (over: Partial<ImportFromScriptResponse> = {}): ImportFromScriptResponse => ({
    items: [],
    created: 0,
    linked: 0,
    skipped: 0,
    ...over,
  });

  it('is offered on Characters and Locations only', async () => {
    for (const type of ['character', 'location'] as const) {
      const view = mount({ assetType: type });
      await waitFor(() => expect(screen.getByTestId('import-from-script')).toBeTruthy());
      view.unmount();
    }
    for (const type of ['prop', 'costume'] as const) {
      const view = mount({ assetType: type });
      await waitFor(() => expect(screen.getByTestId('project-assets-empty')).toBeTruthy());
      // Hidden, not disabled: nothing in a script derives these two, so a
      // greyed button would promise a feature that is not coming.
      expect(screen.queryByTestId('import-from-script')).toBeNull();
      view.unmount();
    }
  });

  it('renders the tallies and refetches', async () => {
    importFromScript.mockResolvedValue(
      report({
        created: 2,
        linked: 1,
        items: [
          { name: 'Sang Yao', asset_type: 'character', action: 'created', asset_id: '1', linked: true },
          { name: 'Lin Mo', asset_type: 'character', action: 'created', asset_id: '2', linked: true },
          { name: 'Rooftop', asset_type: 'location', action: 'linked', asset_id: '3', linked: true },
        ],
      }),
    );
    mount();

    fireEvent.click(screen.getByTestId('import-from-script'));
    const box = await screen.findByTestId('import-report');
    expect(box.textContent).toContain('Imported — 2 Created, 1 Linked, 0 Skipped');
    expect(importFromScript).toHaveBeenCalledWith(PROJECT);
    await waitFor(() => expect(listProjectAssets).toHaveBeenCalledTimes(2));
    expect(screen.queryAllByTestId('import-failure')).toHaveLength(0);

    // The run lands BOTH types in one call, so the tally alone would let a
    // Characters panel that gained 2 cards look like it lost one of the 3.
    expect(screen.getByTestId('import-by-type').textContent).toBe('Characters 2Locations 1');
  });

  it('counts the breakdown by `linked`, not by `action`', async () => {
    importFromScript.mockResolvedValue(
      report({
        created: 2,
        items: [
          { name: 'Sang Yao', asset_type: 'character', action: 'created', asset_id: '1', linked: true },
          // Created, but the REF failed — no card will appear for it. Counting
          // it in the breakdown would promise one that never arrives; it gets
          // a failure line instead.
          {
            name: 'Lin Mo',
            asset_type: 'character',
            action: 'created',
            asset_id: '2',
            linked: false,
            code: 'project_scope_mismatch',
            detail: 'Different team',
          },
        ],
      }),
    );
    mount();

    fireEvent.click(screen.getByTestId('import-from-script'));
    await screen.findByTestId('import-report');
    expect(screen.getByTestId('import-by-type').textContent).toBe('Characters 1');
    expect(screen.getAllByTestId('import-failure')).toHaveLength(1);
  });

  it('renders the same chips on an idempotent re-run, beside a zero-write summary', async () => {
    // The re-run shape, pinned because the two lines say different things and
    // both are true. `already_linked` comes back `action: 'skipped'` with
    // `linked: true` — the name IS on the project, this run just did not put
    // it there. So the summary reports 0 written and the chips still report
    // where the reader will find the cards.
    //
    // It is also the shape that decides what the chips MEAN: excluding
    // `already_linked` would make a second import of an unchanged script show
    // no chips at all, which reads as "these names are not here" — the exact
    // opposite of the truth.
    importFromScript.mockResolvedValue(
      report({
        skipped: 2,
        items: [
          {
            name: 'Sang Yao',
            asset_type: 'character',
            action: 'skipped',
            asset_id: '1',
            linked: true,
            code: 'already_linked',
            detail: 'Already referenced by this project',
          },
          {
            name: 'Rooftop',
            asset_type: 'location',
            action: 'skipped',
            asset_id: '3',
            linked: true,
            code: 'already_linked',
            detail: 'Already referenced by this project',
          },
        ],
      }),
    );
    mount();

    fireEvent.click(screen.getByTestId('import-from-script'));
    const box = await screen.findByTestId('import-report');
    expect(box.textContent).toContain('Imported — 0 Created, 0 Linked, 2 Skipped');
    expect(screen.getByTestId('import-by-type').textContent).toBe('Characters 1Locations 1');
    // `already_linked` is benign: it is the idempotent answer, not a refusal,
    // so it must not produce a failure line beside the chips.
    expect(screen.queryAllByTestId('import-failure')).toHaveLength(0);
  });

  it('shows no breakdown when nothing landed on the project', async () => {
    importFromScript.mockResolvedValue(
      report({
        skipped: 1,
        items: [
          {
            name: '(blank)',
            asset_type: 'character',
            action: 'skipped',
            code: 'empty_name',
            detail: 'Blank name in script',
          },
        ],
      }),
    );
    mount();

    fireEvent.click(screen.getByTestId('import-from-script'));
    await screen.findByTestId('import-report');
    // An empty chip row would read as "0 of everything", which is a different
    // claim from "this run put nothing here".
    expect(screen.queryByTestId('import-by-type')).toBeNull();
  });

  it('names every refused item, including one that was created but not linked', async () => {
    importFromScript.mockResolvedValue(
      report({
        created: 1,
        skipped: 1,
        items: [
          // The asset landed; the REF did not. `action` is `created`, so a
          // filter keyed on "skipped" would drop this one silently.
          {
            name: 'Sang Yao',
            asset_type: 'character',
            action: 'created',
            asset_id: '1',
            linked: false,
            code: 'project_scope_mismatch',
            detail: 'Project belongs to a different team than this asset',
          },
          {
            name: '(blank)',
            asset_type: 'character',
            action: 'skipped',
            code: 'empty_name',
            detail: 'Blank name in script',
          },
          // Benign: the idempotent re-run answering honestly. Not a failure.
          {
            name: 'Rooftop',
            asset_type: 'location',
            action: 'skipped',
            asset_id: '3',
            linked: true,
            code: 'already_linked',
            detail: 'Asset already exists and is already referenced',
          },
        ],
      }),
    );
    mount();

    fireEvent.click(screen.getByTestId('import-from-script'));
    await screen.findByTestId('import-report');

    const lines = screen.getAllByTestId('import-failure').map((el) => el.textContent);
    expect(lines).toHaveLength(2);
    expect(lines[0]).toBe('Sang Yao (Character) — That Project Belongs To A Different Workspace');
    expect(lines[1]).toBe('(blank) (Character) — The Script Left This Name Blank');
  });

  it('falls back to the server’s own detail for a code with no string', async () => {
    importFromScript.mockResolvedValue(
      report({
        skipped: 1,
        items: [
          {
            name: 'Ghost',
            asset_type: 'character',
            action: 'skipped',
            code: 'some_new_code',
            detail: 'A reason only the server knows',
          },
        ],
      }),
    );
    mount();

    fireEvent.click(screen.getByTestId('import-from-script'));
    const line = await screen.findByTestId('import-failure');
    expect(line.textContent).toBe('Ghost (Character) — A reason only the server knows');
  });

  it('says so when the script named nothing at all', async () => {
    importFromScript.mockResolvedValue(report());
    mount();

    fireEvent.click(screen.getByTestId('import-from-script'));
    expect(await screen.findByTestId('import-nothing')).toBeTruthy();
  });

  it('reports a whole-request failure and shows no report', async () => {
    importFromScript.mockRejectedValue(
      new GeneratedApiError(403, 'not_a_member', 'Not a member'),
    );
    mount();

    fireEvent.click(screen.getByTestId('import-from-script'));
    await waitFor(() => expect(addToast).toHaveBeenCalled());
    expect(screen.queryByTestId('import-report')).toBeNull();
  });
});
