/**
 * The staged ASSET chip in the composer's attachment row, and the v2 loadout
 * menu that now hangs off it.
 *
 * The chip label is the load-bearing part: once a user picks an outfit, the
 * row has to SAY which one. Without that, the only evidence of the choice is a
 * field on the wire — and a pick nobody can see is a pick nobody can correct.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown) =>
      typeof fallback === 'string' ? fallback : key,
  }),
}));

vi.mock('../hooks/useChatAttachmentUpload', () => ({
  useChatAttachmentUpload: () => ({ handleFiles: vi.fn(), uploading: false }),
}));

vi.mock('../hooks/useOptionalTaskManager', () => ({
  useOptionalTaskManager: () => null,
}));

const fetchAssetDetail = vi.fn();
vi.mock('../services/assetsService', () => ({
  fetchAssetDetail: (...args: unknown[]) => fetchAssetDetail(...args),
}));

import { ChatAttachmentPicker } from './ChatAttachmentPicker';
import type { StagedAssetRef } from './chat/stagedResources';

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
  cover_file_id: '',
  scope_id: '727145299382534200',
};

const LOADOUTS = {
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

function renderPicker(assets: StagedAssetRef[]) {
  const onAssetsChange = vi.fn();
  render(
    <ChatAttachmentPicker
      attachments={[]}
      onChange={vi.fn()}
      assets={assets}
      onAssetsChange={onAssetsChange}
    />,
  );
  return { onAssetsChange };
}

describe('the staged asset chip and its loadout menu', () => {
  beforeEach(() => {
    fetchAssetDetail.mockReset();
    fetchAssetDetail.mockResolvedValue(LOADOUTS);
  });

  it('labels the chip with the asset name alone while no outfit is picked', () => {
    renderPicker([CHARACTER]);
    const chip = screen.getByTestId('staged-asset-chip');
    expect(within(chip).getByText('Sang Yao')).toBeTruthy();
    expect(chip.textContent).not.toContain('·');
  });

  it('re-labels the chip "name · loadout" once one is chosen', async () => {
    const { onAssetsChange } = renderPicker([CHARACTER]);

    await click(screen.getByTestId('staged-asset-loadout-button'));
    await waitFor(() => expect(screen.getByText('Rainy Night')).toBeTruthy());
    await click(screen.getByText('Rainy Night'));

    // The picker owns no staged state — it reports upward, and the parent
    // re-renders. Assert on what it REPORTED, then on what that renders.
    expect(onAssetsChange).toHaveBeenCalledTimes(1);
    const next = onAssetsChange.mock.calls[0][0] as StagedAssetRef[];
    expect(next).toEqual([
      { ...CHARACTER, loadout_id: '727145299382534512', loadout_name: 'Rainy Night' },
    ]);
  });

  it('paints the picked loadout beside the name', () => {
    renderPicker([
      { ...CHARACTER, loadout_id: '727145299382534512', loadout_name: 'Rainy Night' },
    ]);
    const chip = screen.getByTestId('staged-asset-chip');
    expect(within(chip).getByText('Sang Yao · Rainy Night')).toBeTruthy();
  });

  it('changes only the chip that was edited', async () => {
    const other: StagedAssetRef = {
      ...CHARACTER,
      asset_id: '727145299382534399',
      name: 'Lin Wei',
    };
    const { onAssetsChange } = renderPicker([CHARACTER, other]);

    const buttons = screen.getAllByTestId('staged-asset-loadout-button');
    await click(buttons[0]);
    await waitFor(() => expect(screen.getByText('Rainy Night')).toBeTruthy());
    await click(screen.getByText('Rainy Night'));

    const next = onAssetsChange.mock.calls[0][0] as StagedAssetRef[];
    expect(next[0].loadout_id).toBe('727145299382534512');
    expect(next[1]).toEqual(other);
  });

  it('gives a location chip no loadout button', () => {
    renderPicker([{ ...CHARACTER, asset_type: 'location', name: 'Rooftop' }]);
    expect(screen.getByTestId('staged-asset-chip')).toBeTruthy();
    expect(screen.queryByTestId('staged-asset-loadout-button')).toBeNull();
  });

  it('still removes the whole chip from the × button', async () => {
    const { onAssetsChange } = renderPicker([CHARACTER]);
    await click(screen.getByLabelText('remove'));
    expect(onAssetsChange).toHaveBeenCalledWith([]);
  });
});
