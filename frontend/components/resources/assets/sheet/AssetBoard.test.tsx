/**
 * The Board: what it draws, and what a reorder actually writes.
 *
 * The persistence assertion is the load-bearing one. `attrs` is a whole-column
 * PATCH, so a save that sent only `{board_layout}` would silently delete every
 * other key the asset carries - a data loss with no error, visible only the
 * next time something read one of those keys.
 *
 * Reordering is driven through the KEYBOARD path on purpose: JSDOM cannot
 * produce a real pointer stream, and a synthetic one would be pinning the test
 * harness rather than the behaviour. Both inputs call the same `moveSlot` +
 * `commitOrder` pair, and `moveSlot` itself is pinned in
 * `assetSheetModel.test.ts`.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { i18nMock, SCOPE_ID } from './sheetTestUtils';

vi.mock('react-i18next', () => i18nMock);

const updateAsset = vi.fn();
vi.mock('../../../../services/assetsService', () => ({
  updateAsset: (...args: unknown[]) => updateAsset(...args),
}));

vi.mock('../../../../services/resourceService', () => ({
  getResourceCoverUrl: (id: string) => `/api/v1/resources/${id}/cover`,
  getResourceFileUrl: (id: string) => `/api/v1/resources/${id}/file`,
}));

import { AssetBoard } from './AssetBoard';
import { CHARACTER_DETAIL, makeDetail, makeFile } from './assetSheetFixtures';

/** The grid's slots, in render order. Read off `data-board-slot` rather than
 *  a test-id regex: `board-pins` (the container) would match one too. */
function slotOrder(): string[] {
  return [...screen.getByTestId('board-pins').querySelectorAll('[data-board-slot]')].map(
    (el) => (el as HTMLElement).dataset.boardSlot as string,
  );
}

function renderBoard(overrides: Partial<React.ComponentProps<typeof AssetBoard>> = {}) {
  const onAssetUpdated = vi.fn();
  const onError = vi.fn();
  const utils = render(
    <AssetBoard
      scopeId={SCOPE_ID}
      detail={CHARACTER_DETAIL}
      loadoutId={null}
      readOnly={false}
      onAssetUpdated={onAssetUpdated}
      onError={onError}
      {...overrides}
    />,
  );
  return { ...utils, onAssetUpdated, onError };
}

beforeEach(() => {
  updateAsset.mockReset();
  updateAsset.mockResolvedValue({ ...CHARACTER_DETAIL });
});

describe('AssetBoard rendering', () => {
  it('shows the primary slot in its own frame, outside the pin grid', () => {
    renderBoard();
    const main = screen.getByTestId('board-main');
    expect(main).toHaveAttribute('data-slot', 'sheet');
    expect(within(main).getByRole('img')).toHaveAttribute('data-resource-id', '727145299382534146');

    expect(slotOrder()).not.toContain('sheet');
  });

  it('badges a slot that holds more than one file', () => {
    renderBoard();
    const stills = screen
      .getAllByTestId('board-pin')
      .find((el) => el.getAttribute('data-slot') === 'stills');
    expect(within(stills as HTMLElement).getByTestId('pin-count')).toHaveTextContent('2');
  });

  it('draws empty slots dashed, with Equip and Generate', () => {
    renderBoard();
    const empty = screen
      .getAllByTestId('board-empty-pin')
      .map((el) => el.getAttribute('data-slot'));
    expect(empty).toContain('expressions');
    expect(empty).toContain('extras');
    expect(empty).toContain('unsorted');
  });

  it('disables Equip and Generate until Task 8 supplies the handlers', () => {
    // A live button that swallows the click is the silent no-op; a disabled
    // one with a title is a promise the user can read.
    renderBoard();
    const equip = screen.getAllByTestId('pin-equip')[0];
    expect(equip).toBeDisabled();
    expect(equip).toHaveAttribute('title', 'Arrives shortly');
  });

  it('calls the Task 8 hooks with the slot when they are supplied', () => {
    const onEquip = vi.fn();
    const onGenerate = vi.fn();
    renderBoard({ onEquip, onGenerate });
    const expressions = screen
      .getAllByTestId('board-empty-pin')
      .find((el) => el.getAttribute('data-slot') === 'expressions') as HTMLElement;
    fireEvent.click(within(expressions).getByTestId('pin-equip'));
    fireEvent.click(within(expressions).getByTestId('pin-generate'));
    expect(onEquip).toHaveBeenCalledWith('expressions');
    expect(onGenerate).toHaveBeenCalledWith('expressions');
  });

  it('a preset board offers no Arrange and no Equip at all', () => {
    renderBoard({ readOnly: true });
    expect(screen.queryByTestId('board-arrange')).toBeNull();
    expect(screen.queryByTestId('pin-equip')).toBeNull();
  });
});

describe('loadout filtering', () => {
  it('the worn pin follows the selected loadout', () => {
    const { rerender } = renderBoard({ loadoutId: '727145299382534400' });
    const wornImage = () =>
      (
        screen
          .getAllByTestId('board-pin')
          .find((el) => el.getAttribute('data-slot') === 'worn') as HTMLElement
      ).querySelector('img');

    expect(wornImage()).toHaveAttribute('data-resource-id', '727145299382534160');

    rerender(
      <AssetBoard
        scopeId={SCOPE_ID}
        detail={CHARACTER_DETAIL}
        loadoutId="727145299382534401"
        readOnly={false}
        onAssetUpdated={vi.fn()}
        onError={vi.fn()}
      />,
    );
    // This loadout's own file leads; the unassigned one still counts, because
    // it belongs to no outfit and therefore to every one.
    expect(wornImage()).toHaveAttribute('data-resource-id', '727145299382534161');
    const worn = screen
      .getAllByTestId('board-pin')
      .find((el) => el.getAttribute('data-slot') === 'worn') as HTMLElement;
    expect(within(worn).getByTestId('pin-count')).toHaveTextContent('2');
  });
});

describe('Arrange', () => {
  it('persists the new order onto attrs.board_layout, keeping the other keys', async () => {
    const detail = makeDetail({
      ...CHARACTER_DETAIL,
      id: CHARACTER_DETAIL.id,
      // A key the board knows nothing about. `AssetUpdate.attrs` REPLACES the
      // column, so a PATCH that dropped this would be silent data loss.
      attrs: { height_cm: 168 },
    });
    renderBoard({ detail });

    fireEvent.click(screen.getByTestId('board-arrange'));
    const handle = screen
      .getAllByTestId('pin-drag-handle')
      .find((el) => el.getAttribute('data-slot') === 'stills') as HTMLElement;
    fireEvent.keyDown(handle, { key: 'ArrowRight' });

    await waitFor(() => expect(updateAsset).toHaveBeenCalledTimes(1));
    expect(updateAsset).toHaveBeenCalledWith(SCOPE_ID, CHARACTER_DETAIL.id, {
      attrs: {
        height_cm: 168,
        board_layout: { slot_order: ['expressions', 'stills', 'extras', 'worn', 'unsorted'] },
      },
    });
  });

  it('a refused save snaps back instead of showing an order the server does not have', async () => {
    updateAsset.mockRejectedValueOnce(new Error('nope'));
    const { onError } = renderBoard();

    fireEvent.click(screen.getByTestId('board-arrange'));
    const handle = screen
      .getAllByTestId('pin-drag-handle')
      .find((el) => el.getAttribute('data-slot') === 'stills') as HTMLElement;
    fireEvent.keyDown(handle, { key: 'ArrowRight' });

    await waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    expect(slotOrder()[0]).toBe('stills');
  });

  it('coalesces a burst of arrow keys into ONE write of the final order', async () => {
    // Held-down arrows would otherwise fire one whole-order PATCH per repeat,
    // all concurrent and unordered - the last to LAND wins, which is not
    // necessarily the last one the user made. The pointer path settles on
    // release; the keyboard path settles after a pause.
    renderBoard();
    fireEvent.click(screen.getByTestId('board-arrange'));
    const handle = () =>
      screen
        .getAllByTestId('pin-drag-handle')
        .find((el) => el.getAttribute('data-slot') === 'stills') as HTMLElement;

    fireEvent.keyDown(handle(), { key: 'ArrowRight' });
    fireEvent.keyDown(handle(), { key: 'ArrowRight' });
    fireEvent.keyDown(handle(), { key: 'ArrowRight' });

    await waitFor(() => expect(updateAsset).toHaveBeenCalled());
    expect(updateAsset).toHaveBeenCalledTimes(1);
    expect(updateAsset).toHaveBeenCalledWith(SCOPE_ID, CHARACTER_DETAIL.id, {
      attrs: {
        board_layout: {
          slot_order: ['expressions', 'extras', 'worn', 'stills', 'unsorted'],
        },
      },
    });
  });

  it('shows the moved pin immediately, before the write settles', () => {
    renderBoard();
    fireEvent.click(screen.getByTestId('board-arrange'));
    fireEvent.keyDown(
      screen
        .getAllByTestId('pin-drag-handle')
        .find((el) => el.getAttribute('data-slot') === 'stills') as HTMLElement,
      { key: 'ArrowRight' },
    );
    // The pin moves on the keypress; only the PATCH waits.
    expect(slotOrder()[0]).toBe('expressions');
    expect(updateAsset).not.toHaveBeenCalled();
  });

  it('a move that would leave the grid is not a request', () => {
    renderBoard();
    fireEvent.click(screen.getByTestId('board-arrange'));
    const first = screen
      .getAllByTestId('pin-drag-handle')
      .find((el) => el.getAttribute('data-slot') === 'stills') as HTMLElement;
    fireEvent.keyDown(first, { key: 'ArrowLeft' });
    expect(updateAsset).not.toHaveBeenCalled();
  });

  it('reads the saved order back out of attrs', () => {
    const detail = makeDetail({
      ...CHARACTER_DETAIL,
      id: CHARACTER_DETAIL.id,
      attrs: { board_layout: { slot_order: ['worn', 'stills'] } },
    });
    renderBoard({ detail });
    expect(slotOrder()).toEqual(['worn', 'stills', 'expressions', 'extras', 'unsorted']);
  });
});

describe('lightbox', () => {
  it('opens on a pin and closes again', () => {
    renderBoard();
    fireEvent.click(
      screen.getAllByTestId('board-pin').find((el) => el.getAttribute('data-slot') === 'stills') as HTMLElement,
    );
    const box = screen.getByTestId('pin-lightbox');
    expect(within(box).getByTestId('pin-lightbox-image')).toHaveAttribute(
      'data-resource-id',
      '727145299382534151',
    );
    fireEvent.click(screen.getByTestId('pin-lightbox-next'));
    expect(screen.getByTestId('pin-lightbox-image')).toHaveAttribute(
      'data-resource-id',
      '727145299382534150',
    );
    fireEvent.click(screen.getByTestId('pin-lightbox-close'));
    expect(screen.queryByTestId('pin-lightbox')).toBeNull();
  });
});

describe('types other than character', () => {
  it('a prop board leads with its turnaround and grids the rest', () => {
    const detail = makeDetail({
      id: '900',
      asset_type: 'prop',
      name: 'Lacquered Dagger',
      files: [makeFile({ resource_id: 'p1', slot: 'turnaround', asset_id: '900' })],
    });
    renderBoard({ detail });
    expect(screen.getByTestId('board-main')).toHaveAttribute('data-slot', 'turnaround');
    expect(slotOrder()).toEqual(['in_scene', 'details', 'unsorted']);
  });
});
