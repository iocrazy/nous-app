/**
 * The v2 loadout picker, on the chip where the asset is already staged.
 *
 * What it has to get right is mostly about SILENCE: the button appears only
 * where a choice exists (a scoped character with more than one loadout), and
 * every other case renders nothing rather than a control that does nothing.
 * The one exception is a fetch that FAILS — a chip that just drops its button
 * would tell the user the character has no outfits, which is a claim no
 * answer supports.
 *
 * `fetchAssetDetail` is mocked at the SERVICE boundary, so the fixtures use
 * that function's own documented return type (string ids, `loadouts` already
 * normalised to an array) rather than a raw HTTP body — CLAUDE.md's rule is
 * "which layer are you mocking", and this is the normalised one.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown) =>
      typeof fallback === 'string' ? fallback : key,
  }),
}));

const fetchAssetDetail = vi.fn();
vi.mock('../../services/assetsService', () => ({
  fetchAssetDetail: (...args: unknown[]) => fetchAssetDetail(...args),
}));

import { StagedAssetLoadoutMenu } from './StagedAssetLoadoutMenu';
import type { StagedAssetRef } from './stagedResources';

/** A click that also flushes the promise the handler kicks off. `fireEvent`
 *  returns before an async onClick settles, so without the flush every
 *  assertion about what the fetch produced would race it. */
async function click(el: Element): Promise<void> {
  await act(async () => {
    fireEvent.click(el);
  });
}

const CHARACTER: StagedAssetRef = {
  asset_id: '727145299382534300',
  loadout_id: null,
  loadout_name: null,
  name: 'Sang Yao',
  asset_type: 'character',
  cover_file_id: '727145299382534146',
  scope_id: '727145299382534200',
};

/** Two loadouts — the only shape where a PICK is meaningful. */
const TWO_LOADOUTS = {
  loadouts: [
    {
      id: '727145299382534511',
      asset_id: CHARACTER.asset_id,
      name: 'Daywear',
      is_default: true,
      costume_ids: [],
      prop_ids: [],
      prompt_extra: null,
      sort_order: 0,
      created_at: '2026-09-01T00:00:00Z',
    },
    {
      id: '727145299382534512',
      asset_id: CHARACTER.asset_id,
      name: 'Rainy Night',
      is_default: false,
      costume_ids: [],
      prop_ids: [],
      prompt_extra: null,
      sort_order: 1,
      created_at: '2026-09-01T00:00:00Z',
    },
  ],
};

function renderMenu(over: Partial<StagedAssetRef> = {}) {
  const onChange = vi.fn();
  render(<StagedAssetLoadoutMenu asset={{ ...CHARACTER, ...over }} onChange={onChange} />);
  return { onChange };
}

describe('StagedAssetLoadoutMenu — where it appears at all', () => {
  beforeEach(() => {
    fetchAssetDetail.mockReset();
    fetchAssetDetail.mockResolvedValue(TWO_LOADOUTS);
  });

  it('offers the button for a scoped character', () => {
    renderMenu();
    expect(screen.getByTestId('staged-asset-loadout-button')).toBeTruthy();
  });

  it('renders nothing for a location — only characters wear outfits', () => {
    renderMenu({ asset_type: 'location' });
    expect(screen.queryByTestId('staged-asset-loadout-button')).toBeNull();
  });

  it('renders nothing for a system preset', () => {
    // `toStagedAsset` normalises the wire's `scope_id: null` to '', so the
    // component has to read the EMPTY STRING as "preset", not just null.
    // A preset is read-only to every scope; there is nothing here to pick.
    renderMenu({ scope_id: '' });
    expect(screen.queryByTestId('staged-asset-loadout-button')).toBeNull();
  });

  it('asks the server for nothing until the menu is opened', () => {
    // Lazy on purpose: a composer row can hold up to the asset cap, and
    // `fetchAssetDetail` is a real round trip per chip.
    renderMenu();
    expect(fetchAssetDetail).not.toHaveBeenCalled();
  });
});

describe('StagedAssetLoadoutMenu — choosing', () => {
  beforeEach(() => {
    fetchAssetDetail.mockReset();
    fetchAssetDetail.mockResolvedValue(TWO_LOADOUTS);
  });

  it('lists every loadout plus the default entry once opened', async () => {
    renderMenu();
    await click(screen.getByTestId('staged-asset-loadout-button'));

    await waitFor(() => expect(screen.getByText('Rainy Night')).toBeTruthy());
    expect(fetchAssetDetail).toHaveBeenCalledWith(
      CHARACTER.scope_id,
      CHARACTER.asset_id,
    );
    // The flagged default is marked, and "Default Loadout" (= null) is offered
    // as its own way back — a user who picked an outfit must be able to unpick
    // it without removing the chip.
    expect(screen.getByText('Default Loadout')).toBeTruthy();
    expect(screen.getByText('Daywear')).toBeTruthy();
  });

  it('reports the id AND the name, because the chip label needs both', async () => {
    const { onChange } = renderMenu();
    await click(screen.getByTestId('staged-asset-loadout-button'));
    await waitFor(() => expect(screen.getByText('Rainy Night')).toBeTruthy());

    await click(screen.getByText('Rainy Night'));

    expect(onChange).toHaveBeenCalledWith('727145299382534512', 'Rainy Night');
  });

  it('reports null for the default entry, the backend meaning of "no pick"', async () => {
    const { onChange } = renderMenu({
      loadout_id: '727145299382534512',
      loadout_name: 'Rainy Night',
    });
    await click(screen.getByTestId('staged-asset-loadout-button'));
    await waitFor(() => expect(screen.getByText('Default Loadout')).toBeTruthy());

    await click(screen.getByText('Default Loadout'));

    expect(onChange).toHaveBeenCalledWith(null, null);
  });

  it('closes the list after a pick', async () => {
    renderMenu();
    await click(screen.getByTestId('staged-asset-loadout-button'));
    await waitFor(() => expect(screen.getByText('Rainy Night')).toBeTruthy());

    await click(screen.getByText('Rainy Night'));

    await waitFor(() => expect(screen.queryByText('Rainy Night')).toBeNull());
  });

  it('withdraws the button when the character turns out to have one loadout', async () => {
    // One loadout is not a choice. The button can only go away AFTER the
    // answer arrives — the count is not knowable from the staged snapshot.
    fetchAssetDetail.mockResolvedValue({ loadouts: [TWO_LOADOUTS.loadouts[0]] });
    renderMenu();
    await click(screen.getByTestId('staged-asset-loadout-button'));

    await waitFor(() =>
      expect(screen.queryByTestId('staged-asset-loadout-button')).toBeNull(),
    );
  });
});

describe('StagedAssetLoadoutMenu — a failed fetch is visible, not silent', () => {
  beforeEach(() => {
    fetchAssetDetail.mockReset();
    fetchAssetDetail.mockRejectedValue(new Error('network down'));
  });

  it('keeps the button, titled with what went wrong, and opens nothing', async () => {
    // Removing the button would state "this character has no outfits" — a
    // claim the failed request cannot support. The typed, visible answer is
    // the button saying it could not read them.
    renderMenu();
    const button = screen.getByTestId('staged-asset-loadout-button');
    await click(button);

    await waitFor(() =>
      expect(button.getAttribute('title')).toBe('Could not load loadouts'),
    );
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('does not retry on every click once it has failed', async () => {
    renderMenu();
    const button = screen.getByTestId('staged-asset-loadout-button');
    await click(button);
    await waitFor(() => expect(fetchAssetDetail).toHaveBeenCalledTimes(1));

    await click(button);
    expect(fetchAssetDetail).toHaveBeenCalledTimes(1);
  });
});
