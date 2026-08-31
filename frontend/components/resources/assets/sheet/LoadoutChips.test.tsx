/**
 * Loadout chips: selection, and the four writes the API allows.
 *
 * The load-bearing assertion is that the DEFAULT loadout has no delete
 * control. `DELETE /assets/{id}/loadouts/{lid}` refuses the default with 422
 * `cannot_delete_default`, so a delete button there exists only to be
 * refused - and a control whose every use fails teaches the user the page is
 * unreliable rather than teaching them the rule.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { i18nMock, SCOPE_ID } from './sheetTestUtils';

vi.mock('react-i18next', () => i18nMock);

const createLoadout = vi.fn();
const updateLoadout = vi.fn();
const deleteLoadout = vi.fn();
vi.mock('../../../../services/assetsService', () => ({
  createLoadout: (...a: unknown[]) => createLoadout(...a),
  updateLoadout: (...a: unknown[]) => updateLoadout(...a),
  deleteLoadout: (...a: unknown[]) => deleteLoadout(...a),
}));

import { LoadoutChips } from './LoadoutChips';
import { CHARACTER_DETAIL } from './assetSheetFixtures';

const ASSET_ID = CHARACTER_DETAIL.id;
const DEFAULT_ID = '727145299382534400';
const NIGHT_ID = '727145299382534401';

function renderChips(overrides: Partial<React.ComponentProps<typeof LoadoutChips>> = {}) {
  const props = {
    scopeId: SCOPE_ID,
    assetId: ASSET_ID,
    loadouts: CHARACTER_DETAIL.loadouts,
    selectedId: DEFAULT_ID,
    readOnly: false,
    onSelect: vi.fn(),
    onChanged: vi.fn(),
    onError: vi.fn(),
    ...overrides,
  };
  return { ...render(<LoadoutChips {...props} />), props };
}

const chip = (id: string) =>
  screen.getAllByTestId('loadout-chip').find((el) => el.dataset.loadoutId === id) as HTMLElement;

beforeEach(() => {
  createLoadout.mockReset().mockResolvedValue({});
  updateLoadout.mockReset().mockResolvedValue({});
  deleteLoadout.mockReset().mockResolvedValue(undefined);
});

describe('selection', () => {
  it('marks the selected chip and reports a switch', () => {
    const { props } = renderChips();
    expect(chip(DEFAULT_ID)).toHaveAttribute('data-active', 'true');
    expect(chip(NIGHT_ID)).toHaveAttribute('data-active', 'false');
    fireEvent.click(within(chip(NIGHT_ID)).getByText('Night raid'));
    expect(props.onSelect).toHaveBeenCalledWith(NIGHT_ID);
  });
});

describe('writes', () => {
  it('creates a loadout by name', async () => {
    const { props } = renderChips();
    fireEvent.click(screen.getByTestId('loadout-new'));
    fireEvent.change(screen.getByTestId('loadout-name-input'), {
      target: { value: 'Rain gear' },
    });
    fireEvent.click(screen.getByTestId('loadout-name-save'));
    await waitFor(() => expect(createLoadout).toHaveBeenCalled());
    expect(createLoadout).toHaveBeenCalledWith(SCOPE_ID, ASSET_ID, { name: 'Rain gear' });
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled());
  });

  it('renames one', async () => {
    renderChips();
    fireEvent.click(within(chip(NIGHT_ID)).getByTestId('loadout-rename'));
    fireEvent.change(screen.getByTestId('loadout-name-input'), { target: { value: 'Night run' } });
    fireEvent.keyDown(screen.getByTestId('loadout-name-input'), { key: 'Enter' });
    await waitFor(() => expect(updateLoadout).toHaveBeenCalled());
    expect(updateLoadout).toHaveBeenCalledWith(SCOPE_ID, ASSET_ID, NIGHT_ID, {
      name: 'Night run',
    });
  });

  it('an emptied name cancels rather than sending a 422', async () => {
    renderChips();
    fireEvent.click(within(chip(NIGHT_ID)).getByTestId('loadout-rename'));
    fireEvent.change(screen.getByTestId('loadout-name-input'), { target: { value: '   ' } });
    fireEvent.click(screen.getByTestId('loadout-name-save'));
    expect(updateLoadout).not.toHaveBeenCalled();
    expect(screen.queryByTestId('loadout-name-input')).toBeNull();
  });

  it('promotes another loadout to default', async () => {
    renderChips();
    fireEvent.click(within(chip(NIGHT_ID)).getByTestId('loadout-set-default'));
    await waitFor(() => expect(updateLoadout).toHaveBeenCalled());
    expect(updateLoadout).toHaveBeenCalledWith(SCOPE_ID, ASSET_ID, NIGHT_ID, {
      is_default: true,
    });
  });

  it('deletes a non-default loadout', async () => {
    renderChips();
    fireEvent.click(within(chip(NIGHT_ID)).getByTestId('loadout-delete'));
    await waitFor(() => expect(deleteLoadout).toHaveBeenCalled());
    expect(deleteLoadout).toHaveBeenCalledWith(SCOPE_ID, ASSET_ID, NIGHT_ID);
  });

  it('offers no delete on the default one', () => {
    renderChips();
    expect(within(chip(DEFAULT_ID)).queryByTestId('loadout-delete')).toBeNull();
    expect(within(chip(DEFAULT_ID)).queryByTestId('loadout-set-default')).toBeNull();
  });

  it('reports a refusal instead of swallowing it', async () => {
    deleteLoadout.mockRejectedValueOnce(new Error('nope'));
    const { props } = renderChips();
    fireEvent.click(within(chip(NIGHT_ID)).getByTestId('loadout-delete'));
    await waitFor(() => expect(props.onError).toHaveBeenCalledTimes(1));
    expect(props.onChanged).not.toHaveBeenCalled();
  });
});

describe('read-only', () => {
  it('a preset shows the chips and none of the controls', () => {
    renderChips({ readOnly: true });
    expect(screen.getAllByTestId('loadout-chip')).toHaveLength(2);
    expect(screen.queryByTestId('loadout-new')).toBeNull();
    expect(screen.queryByTestId('loadout-rename')).toBeNull();
    expect(screen.queryByTestId('loadout-delete')).toBeNull();
  });

  it('renders nothing at all for a preset with no loadouts', () => {
    const { container } = renderChips({ readOnly: true, loadouts: [] });
    expect(container.firstChild).toBeNull();
  });
});
