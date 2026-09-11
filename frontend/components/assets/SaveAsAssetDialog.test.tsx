import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

// A stand-in bundle, not a bare `d ?? k`: the failure path resolves
// `saveAsAsset.err.<code>` with `saveAsAsset.err.generic` AS its default, so a
// mock that always returned the default could not tell a mapped code from an
// unmapped one.
const BUNDLE: Record<string, string> = {
  'saveAsAsset.err.invalid_slot': 'That slot is not valid for this asset type',
  'saveAsAsset.err.generic': 'Something went wrong',
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
vi.mock('../Toast', () => ({ useToast: () => ({ addToast }) }));

vi.mock('../../services/generatedMediaService', () => ({
  generatedMediaCoverUrl: (id: string) => `https://api.test/gen/${id}/cover`,
  generatedMediaStreamUrl: (id: string) => `https://api.test/gen/${id}/stream`,
}));

const searchAssets = vi.fn();
const createAsset = vi.fn();
const fetchAsset = vi.fn();
vi.mock('../../services/assetsService', async () => {
  const { GeneratedApiError } = await import('../../services/apiEnvelope');
  return {
    searchAssets: (...a: unknown[]) => searchAssets(...a),
    createAsset: (...a: unknown[]) => createAsset(...a),
    fetchAsset: (...a: unknown[]) => fetchAsset(...a),
    GeneratedApiError,
  };
});

const saveGenerationAsAsset = vi.fn();
const batchGenerated = vi.fn();
vi.mock('../../services/generatedService', async () => {
  const { GeneratedApiError } = await import('../../services/apiEnvelope');
  return {
    saveGenerationAsAsset: (...a: unknown[]) => saveGenerationAsAsset(...a),
    batchGenerated: (...a: unknown[]) => batchGenerated(...a),
    GeneratedApiError,
  };
});

import { SaveAsAssetDialog } from './SaveAsAssetDialog';
import { GeneratedApiError } from '../../services/apiEnvelope';
import type { GeneratedItem } from '../../services/generatedService';
import type { AssetDetail, AssetSummary } from '../../services/assetsService';

// ─── Fixtures: wire shapes, string ids (wire-fixtures.json) ─────────────────

const SCOPE = '727145299382534200';

const ITEM_A: GeneratedItem = {
  id: '727145299382534145',
  scope_id: SCOPE,
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
    // Null on every non-`agent_run` row (3a): `describe_source` reads the
    // run_deliverables provenance in that one arm only.
    issue_id: null,
    run_id: null,
    step: null,
  },
  title: 'Prompt 0',
};

/** No `source_asset_id` — the "nothing to suggest" branch. */
const ITEM_B: GeneratedItem = {
  ...ITEM_A,
  id: '727145299382534146',
  title: 'Prompt 1',
  source_asset_id: null,
};

const ITEM_C: GeneratedItem = { ...ITEM_B, id: '727145299382534147', title: 'Prompt 2' };

const summary = (over: Partial<AssetSummary> = {}): AssetSummary => ({
  id: '727145299382534202',
  scope_id: SCOPE,
  asset_type: 'character',
  name: 'Yi Heng',
  role_tag: 'lead',
  readiness: { state: 'ready', missing: [] },
  cover_file_id: null,
  is_system_preset: false,
  ...over,
});

const SUGGESTED: AssetDetail = {
  ...summary({ id: '727145299382534201', name: 'Sang Yao' }),
  loadouts: [
    { id: '727145299382534501', name: 'Default', is_default: true },
    { id: '727145299382534502', name: 'Night Raid', is_default: false },
  ],
};

/** Every character has at least one loadout, and a Default among them. */
const YI_HENG: AssetDetail = {
  ...summary(),
  loadouts: [{ id: '727145299382534511', name: 'Default', is_default: true }],
};

const LOCATION_ASSET = summary({
  id: '727145299382534210',
  asset_type: 'location',
  name: 'Tea Port',
});

const renderDialog = (items: GeneratedItem[] = [ITEM_A]) => {
  const onClose = vi.fn();
  const onDone = vi.fn();
  render(
    <SaveAsAssetDialog
      open
      scopeId={SCOPE}
      items={items}
      onClose={onClose}
      onDone={onDone}
    />,
  );
  return { onClose, onDone };
};

const typeSeg = () => screen.getByTestId('sa-type-seg');
const slotSeg = () => screen.getByTestId('sa-slot-seg');
const candidates = () => [...document.querySelectorAll('[data-testid="sa-candidate"]')];
const primary = () => screen.getByTestId('sa-primary') as HTMLButtonElement;

beforeEach(() => {
  addToast.mockReset();
  searchAssets.mockReset().mockResolvedValue([]);
  createAsset.mockReset();
  // Keyed by id: a mock that answered the same detail for every asset would
  // let the dialog read the SUGGESTION's loadouts while the user had picked
  // someone else — the exact confusion this seg exists to avoid.
  fetchAsset.mockReset().mockImplementation(async (_scope: string, id: string) => {
    if (id === SUGGESTED.id) return SUGGESTED;
    if (id === YI_HENG.id) return YI_HENG;
    return { ...summary({ id }), loadouts: [] };
  });
  saveGenerationAsAsset.mockReset();
  batchGenerated.mockReset();
});

describe('SaveAsAssetDialog — the suggestion', () => {
  it('pins the suggested asset first, tagged, and takes the default type from it', async () => {
    // The search deliberately does NOT contain the suggestion: pinning must
    // come from `source_asset_id`, not from the list happening to include it.
    searchAssets.mockResolvedValue([summary()]);
    renderDialog();

    await waitFor(() => expect(fetchAsset).toHaveBeenCalledWith(SCOPE, '727145299382534201'));
    await waitFor(() => expect(candidates().length).toBe(2));

    const first = candidates()[0];
    expect(within(first as HTMLElement).getByText('Sang Yao')).toBeTruthy();
    expect(within(first as HTMLElement).getByText('suggested · from canvas')).toBeTruthy();
    expect(within(candidates()[1] as HTMLElement).getByText('Yi Heng')).toBeTruthy();

    // …and the type seg followed the suggestion rather than staying on the
    // hardcoded default.
    expect(
      within(typeSeg()).getByRole('radio', { name: 'Character' }).getAttribute('aria-checked'),
    ).toBe('true');
  });

  it('does not pin the suggestion under a type it does not belong to', async () => {
    searchAssets.mockResolvedValue([LOCATION_ASSET]);
    renderDialog();

    await waitFor(() => expect(candidates().length).toBe(2));
    fireEvent.click(within(typeSeg()).getByRole('radio', { name: 'Location' }));

    await waitFor(() => expect(candidates().length).toBe(1));
    expect(screen.queryByText('suggested · from canvas')).toBeNull();
    expect(within(candidates()[0] as HTMLElement).getByText('Tea Port')).toBeTruthy();
  });

  it('falls back to character when there is nothing to suggest', async () => {
    renderDialog([ITEM_B]);

    await waitFor(() => expect(searchAssets).toHaveBeenCalled());
    expect(fetchAsset).not.toHaveBeenCalled();
    expect(
      within(typeSeg()).getByRole('radio', { name: 'Character' }).getAttribute('aria-checked'),
    ).toBe('true');
  });
});

describe('SaveAsAssetDialog — target, slot, loadout', () => {
  it('keeps the primary disabled until a target is chosen', async () => {
    searchAssets.mockResolvedValue([summary()]);
    renderDialog();

    await waitFor(() => expect(candidates().length).toBe(2));
    expect(primary().disabled).toBe(true);

    fireEvent.click(screen.getByText('Yi Heng'));
    await waitFor(() => expect(primary().disabled).toBe(false));
    expect(primary().textContent).toContain('Attach to Yi Heng');
  });

  it('defaults the slot to unsorted, rendered last', async () => {
    renderDialog();
    await waitFor(() => expect(searchAssets).toHaveBeenCalled());

    const slots = within(slotSeg()).getAllByRole('radio');
    expect(slots.map((b) => b.textContent)).toEqual([
      'Sheet',
      'Stills',
      'Expressions',
      'Extras',
      'Worn',
      'Unsorted',
    ]);
    expect(slots.at(-1)!.getAttribute('aria-checked')).toBe('true');
  });

  it('offers loadouts only for a character with an existing asset chosen', async () => {
    searchAssets.mockResolvedValue([summary()]);
    renderDialog();

    // Nothing chosen yet → no loadout seg.
    await waitFor(() => expect(candidates().length).toBe(2));
    expect(screen.queryByTestId('sa-loadout-seg')).toBeNull();

    fireEvent.click(screen.getByText('Sang Yao'));

    const seg = await screen.findByTestId('sa-loadout-seg');
    // Default loadout is the one flagged `is_default`, not merely the first.
    expect(
      within(seg).getByRole('radio', { name: 'Default' }).getAttribute('aria-checked'),
    ).toBe('true');
    expect(within(seg).getByRole('radio', { name: 'Night Raid' })).toBeTruthy();

    // The create row is not an existing asset — no loadouts to pick from.
    fireEvent.click(screen.getByTestId('sa-create-button'));
    await waitFor(() => expect(screen.queryByTestId('sa-loadout-seg')).toBeNull());
  });

  it('will not submit while the chosen character\'s loadouts are still loading', async () => {
    searchAssets.mockResolvedValue([summary()]);
    let release: (asset: AssetDetail) => void = () => {};
    fetchAsset.mockImplementation(
      () =>
        new Promise<AssetDetail>((resolve) => {
          release = resolve;
        }),
    );
    renderDialog([ITEM_B]);

    await waitFor(() => expect(candidates().length).toBe(1));
    fireEvent.click(screen.getByText('Yi Heng'));

    // A target IS chosen, but submitting now would send no `loadout_id` and
    // attach to the character without the loadout about to be shown as its
    // default — a wrong result nothing would report.
    await waitFor(() => expect(primary().textContent).toContain('Attach to Yi Heng'));
    expect(primary().disabled).toBe(true);

    await act(async () => {
      release(YI_HENG);
    });
    await waitFor(() => expect(primary().disabled).toBe(false));
  });

  it('never offers loadouts for a non-character type', async () => {
    searchAssets.mockResolvedValue([LOCATION_ASSET]);
    renderDialog([ITEM_B]);

    await waitFor(() => expect(candidates().length).toBe(1));
    fireEvent.click(within(typeSeg()).getByRole('radio', { name: 'Location' }));
    await waitFor(() => expect(candidates().length).toBe(1));
    fireEvent.click(screen.getByText('Tea Port'));

    await waitFor(() => expect(primary().disabled).toBe(false));
    expect(screen.queryByTestId('sa-loadout-seg')).toBeNull();
  });

  it('drops the previous type\'s candidates before the new ones arrive', async () => {
    searchAssets.mockResolvedValueOnce([summary()]);
    renderDialog([ITEM_B]);

    await waitFor(() => expect(candidates().length).toBe(1));
    expect(screen.getByText('Yi Heng')).toBeTruthy();

    // Hold the next search open, so the window between the click and the new
    // list is observable rather than a race we hope is short.
    let release: (list: AssetSummary[]) => void = () => {};
    searchAssets.mockImplementationOnce(
      () =>
        new Promise<AssetSummary[]>((resolve) => {
          release = resolve;
        }),
    );
    fireEvent.click(within(typeSeg()).getByRole('radio', { name: 'Location' }));

    // A character listed under Location is not merely stale: clicking it in
    // this window attaches the generation to an asset of the wrong type.
    await waitFor(() => expect(candidates().length).toBe(0));
    expect(screen.queryByText('Yi Heng')).toBeNull();

    await act(async () => {
      release([LOCATION_ASSET]);
    });
    await waitFor(() => expect(candidates().length).toBe(1));
    expect(screen.getByText('Tea Port')).toBeTruthy();
  });

  it('drops a chosen target when the type changes out from under it', async () => {
    searchAssets.mockResolvedValue([summary()]);
    renderDialog([ITEM_B]);

    await waitFor(() => expect(candidates().length).toBe(1));
    fireEvent.click(screen.getByText('Yi Heng'));
    await waitFor(() => expect(primary().disabled).toBe(false));

    fireEvent.click(within(typeSeg()).getByRole('radio', { name: 'Location' }));

    // Back to "nothing chosen": a character is not a target a location
    // attach could ever have used.
    await waitFor(() => expect(primary().disabled).toBe(true));
    expect(primary().textContent).toContain('Create & attach');
  });

  it('debounces the search rather than firing per keystroke', async () => {
    renderDialog([ITEM_B]);
    await waitFor(() => expect(searchAssets).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByTestId('sa-search'), { target: { value: 'sa' } });
    fireEvent.change(screen.getByTestId('sa-search'), { target: { value: 'san' } });
    // Still one call: the keystrokes have not settled yet.
    expect(searchAssets).toHaveBeenCalledTimes(1);

    await waitFor(() =>
      expect(searchAssets).toHaveBeenLastCalledWith(SCOPE, { type: 'character', q: 'san' }),
    );
    expect(searchAssets).toHaveBeenCalledTimes(2);
  });
});

describe('SaveAsAssetDialog — attaching', () => {
  it('states the contract that a file never moves', async () => {
    renderDialog();
    expect(
      screen.getByText('File stays where it is · attaching never moves or copies'),
    ).toBeTruthy();
  });

  it('attaches one generation to an existing asset and reports the slot', async () => {
    searchAssets.mockResolvedValue([summary()]);
    saveGenerationAsAsset.mockResolvedValue({
      generation: { ...ITEM_A, review_state: 'in_assets' },
      asset_id: '727145299382534201',
      resource_id: '727145299382534301',
    });
    const { onClose, onDone } = renderDialog();

    await waitFor(() => expect(candidates().length).toBe(2));
    fireEvent.click(screen.getByText('Sang Yao'));
    await waitFor(() => expect(primary().disabled).toBe(false));
    fireEvent.click(within(slotSeg()).getByRole('radio', { name: 'Stills' }));
    fireEvent.click(primary());

    await waitFor(() =>
      expect(saveGenerationAsAsset).toHaveBeenCalledWith(SCOPE, '727145299382534145', {
        asset_id: '727145299382534201',
        slot: 'stills',
        loadout_id: '727145299382534501',
      }),
    );
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('Attached to Sang Yao · Stills', 'success'),
    );
    expect(onDone).toHaveBeenCalledWith({
      attachedCount: 1,
      failed: [],
      assetId: '727145299382534201',
      assetName: 'Sang Yao',
      slot: 'stills',
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('creates the asset first, then attaches with the id it just got back', async () => {
    createAsset.mockResolvedValue(summary({ id: '727145299382534299', name: 'Prompt 0' }));
    saveGenerationAsAsset.mockResolvedValue({
      generation: { ...ITEM_A, review_state: 'in_assets' },
      asset_id: '727145299382534299',
      resource_id: '727145299382534301',
    });
    const { onDone } = renderDialog();
    await waitFor(() => expect(searchAssets).toHaveBeenCalled());

    // The name is prefilled from the generation's title and stays editable.
    fireEvent.click(screen.getByTestId('sa-create-button'));
    const nameInput = screen.getByTestId('sa-new-name') as HTMLInputElement;
    expect(nameInput.value).toBe('Prompt 0');
    expect(primary().textContent).toContain('Create & attach');

    fireEvent.click(primary());

    await waitFor(() =>
      expect(createAsset).toHaveBeenCalledWith(SCOPE, {
        asset_type: 'character',
        name: 'Prompt 0',
        // Provenance. Omitting it takes the server default `manual`, which
        // would label an asset born from a generation as hand-made — for
        // good, and indistinguishably.
        source: 'generated',
      }),
    );
    await waitFor(() =>
      expect(saveGenerationAsAsset).toHaveBeenCalledWith(SCOPE, '727145299382534145', {
        asset_id: '727145299382534299',
        slot: 'unsorted',
      }),
    );
    expect(onDone).toHaveBeenCalledWith(
      expect.objectContaining({ assetId: '727145299382534299' }),
    );
  });

  it('reports the name the server assigned, not the one that was typed', async () => {
    // The server is free to trim, normalise, or de-duplicate the name. Echoing
    // the typed one back would tell the user about an asset that does not
    // exist under that name.
    createAsset.mockResolvedValue(summary({ id: '727145299382534299', name: 'Prompt 0 (2)' }));
    saveGenerationAsAsset.mockResolvedValue({
      generation: { ...ITEM_A, review_state: 'in_assets' },
      asset_id: '727145299382534299',
      resource_id: '727145299382534301',
    });
    const { onDone } = renderDialog();
    await waitFor(() => expect(searchAssets).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('sa-create-button'));
    fireEvent.change(screen.getByTestId('sa-new-name'), { target: { value: '  Prompt 0  ' } });
    fireEvent.click(primary());

    await waitFor(() =>
      expect(createAsset).toHaveBeenCalledWith(SCOPE, {
        asset_type: 'character',
        name: 'Prompt 0',
        source: 'generated',
      }),
    );
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('Attached to Prompt 0 (2) · Unsorted', 'success'),
    );
    expect(onDone).toHaveBeenCalledWith(
      expect.objectContaining({ assetId: '727145299382534299', assetName: 'Prompt 0 (2)' }),
    );
  });

  it('offers the existing asset instead of dead-ending on a 409', async () => {
    createAsset.mockRejectedValue(
      new GeneratedApiError(409, 'asset_exists', 'already exists', {
        existing_asset_id: '727145299382534201',
      }),
    );
    saveGenerationAsAsset.mockResolvedValue({
      generation: { ...ITEM_A, review_state: 'in_assets' },
      asset_id: '727145299382534201',
      resource_id: '727145299382534301',
    });
    renderDialog();
    await waitFor(() => expect(searchAssets).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('sa-create-button'));
    fireEvent.click(primary());

    expect(
      await screen.findByText('An asset with this name exists — attach to it instead?'),
    ).toBeTruthy();
    // A recoverable collision is not an error toast: the recovery is on screen.
    expect(addToast).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('sa-use-existing'));

    // The switch resolves the name so the primary can name its target — and
    // the button is only the readiness signal once it is also ENABLED, which
    // waits on the loadout fetch the new target just kicked off.
    await waitFor(() => expect(primary().textContent).toContain('Attach to Sang Yao'));
    await waitFor(() => expect(primary().disabled).toBe(false));
    fireEvent.click(primary());

    await waitFor(() =>
      expect(saveGenerationAsAsset).toHaveBeenCalledWith(SCOPE, '727145299382534145', {
        asset_id: '727145299382534201',
        slot: 'unsorted',
        loadout_id: '727145299382534501',
      }),
    );
    expect(createAsset).toHaveBeenCalledTimes(1);
  });

  it('sends a batch as one call and names the codes that failed', async () => {
    searchAssets.mockResolvedValue([summary()]);
    batchGenerated.mockResolvedValue({
      ok: ['727145299382534145'],
      failed: [
        { id: '727145299382534146', code: 'invalid_slot', detail: "Slot 'flat' is not valid" },
      ],
    });
    const { onDone, onClose } = renderDialog([ITEM_A, ITEM_B, ITEM_C]);

    // Batch mode says how many more are coming along.
    expect(screen.getByText('+2 more')).toBeTruthy();

    await waitFor(() => expect(candidates().length).toBe(2));
    fireEvent.click(screen.getByText('Yi Heng'));
    await waitFor(() => expect(primary().disabled).toBe(false));
    fireEvent.click(primary());

    await waitFor(() =>
      expect(batchGenerated).toHaveBeenCalledWith(SCOPE, {
        ids: ['727145299382534145', '727145299382534146', '727145299382534147'],
        action: 'save_as_asset',
        save_as_asset: {
          asset_id: '727145299382534202',
          slot: 'unsorted',
          loadout_id: '727145299382534511',
        },
      }),
    );
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('1 attached, 1 failed (invalid_slot)', 'error'),
    );
    expect(onDone).toHaveBeenCalledWith({
      attachedCount: 1,
      failed: [{ id: '727145299382534146', code: 'invalid_slot' }],
      assetId: '727145299382534202',
      assetName: 'Yi Heng',
      slot: 'unsorted',
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('SaveAsAssetDialog — failures', () => {
  it('speaks a typed refusal through its own key and leaves the dialog usable', async () => {
    searchAssets.mockResolvedValue([summary()]);
    saveGenerationAsAsset.mockRejectedValue(
      new GeneratedApiError(400, 'invalid_slot', 'Slot is not valid'),
    );
    const { onDone, onClose } = renderDialog();

    await waitFor(() => expect(candidates().length).toBe(2));
    fireEvent.click(screen.getByText('Yi Heng'));
    await waitFor(() => expect(primary().disabled).toBe(false));
    fireEvent.click(primary());

    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith(
        'That slot is not valid for this asset type',
        'error',
      ),
    );
    expect(onDone).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(primary().disabled).toBe(false);
  });

  it('says the candidate list is missing rather than presenting it as empty', async () => {
    searchAssets.mockRejectedValue(
      new GeneratedApiError(403, 'not_a_member', 'You are not a member of this scope'),
    );
    renderDialog([ITEM_B]);

    expect(await screen.findByTestId('sa-search-error')).toBeTruthy();
    // Not toasted: this search re-runs per settled keystroke, so a toast per
    // failure would stack against a down endpoint.
    expect(addToast).not.toHaveBeenCalled();
    // …and the way forward is still there.
    expect(screen.getByTestId('sa-create-button')).toBeTruthy();
  });

  it('renders nothing at all when closed', () => {
    render(
      <SaveAsAssetDialog
        open={false}
        scopeId={SCOPE}
        items={[ITEM_A]}
        onClose={vi.fn()}
        onDone={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('save-as-asset-dialog')).toBeNull();
    expect(searchAssets).not.toHaveBeenCalled();
  });
});
