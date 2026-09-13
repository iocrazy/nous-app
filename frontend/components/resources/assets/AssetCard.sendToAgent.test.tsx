/**
 * Entry point TWO for Send To Agent: the shelf card's action menu.
 *
 * Separate file from `AssetCard.test.tsx` so the card's presentation suite
 * keeps its own subject, and separate from the sheet's so neither can stand in
 * for the other — the whole reason `utils/sendAssetToAgent.ts` exists is that
 * "the helper works" has already once been true while one of its two menus
 * never called it.
 *
 * The row is a real `GET /assets` payload: ids are STRINGS and the preset's
 * `scope_id` is null.
 */
import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

const translate = (key: string, opts?: string | Record<string, unknown>): string => {
  const fallback = typeof opts === 'string' ? opts : (opts?.defaultValue as string | undefined);
  let out = fallback ?? key;
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

vi.mock('../../../services/resourceService', () => ({
  getResourceCoverUrl: (id: string) => `https://api.test/resources/${id}/cover`,
}));

import { AssetCard } from './AssetCard';
import type { AssetRow } from '../../../services/assetsService';
import { useGlobalChatStore } from '../../../stores/globalChatStore';

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
  cover_file_id: null,
  is_system_preset: true,
};

beforeEach(() => {
  useGlobalChatStore.setState({ open: false, pendingAsset: null });
});

describe('AssetCard — action menu', () => {
  it('keeps the menu closed until the trigger is clicked', () => {
    render(<AssetCard asset={CHARACTER} onOpen={() => {}} />);
    expect(screen.queryByTestId('asset-card-menu-popover')).toBeNull();
    fireEvent.click(screen.getByTestId('asset-card-menu'));
    expect(screen.getByTestId('asset-card-menu-popover')).toBeTruthy();
  });

  it('opening the menu does NOT open the asset', () => {
    // The trigger is a sibling of the card button, not a child, precisely so
    // one click cannot mean two things.
    const onOpen = vi.fn();
    render(<AssetCard asset={CHARACTER} onOpen={onOpen} />);
    fireEvent.click(screen.getByTestId('asset-card-menu'));
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('stages the asset on the pendingAsset channel and opens the chat', () => {
    render(<AssetCard asset={CHARACTER} onOpen={() => {}} />);
    fireEvent.click(screen.getByTestId('asset-card-menu'));
    fireEvent.click(screen.getByTestId('asset-card-send-to-agent'));

    const state = useGlobalChatStore.getState();
    expect(state.open).toBe(true);
    expect(state.pendingAsset).toMatchObject({
      assetId: '727145299382534300',
      name: 'Sang Yao',
      assetType: 'character',
      coverFileId: '727145299382534146',
      scopeId: '727145299382534200',
      loadoutId: null,
    });
  });

  it('sends the row it was given, not the first one on the shelf', () => {
    // Two cards mounted together: the menu must close over its OWN asset.
    render(
      <>
        <AssetCard asset={CHARACTER} onOpen={() => {}} />
        <AssetCard asset={PRESET} onOpen={() => {}} />
      </>,
    );
    fireEvent.click(screen.getAllByTestId('asset-card-menu')[1]);
    fireEvent.click(screen.getByTestId('asset-card-send-to-agent'));

    expect(useGlobalChatStore.getState().pendingAsset).toMatchObject({
      assetId: '727145299382534303',
      assetType: 'prompt',
      coverFileId: null,
      scopeId: null,
    });
  });

  it('closes the menu after sending', () => {
    render(<AssetCard asset={CHARACTER} onOpen={() => {}} />);
    fireEvent.click(screen.getByTestId('asset-card-menu'));
    fireEvent.click(screen.getByTestId('asset-card-send-to-agent'));
    expect(screen.queryByTestId('asset-card-menu-popover')).toBeNull();
  });

  it('closes on an outside click without sending anything', () => {
    render(<AssetCard asset={CHARACTER} onOpen={() => {}} />);
    fireEvent.click(screen.getByTestId('asset-card-menu'));
    fireEvent.mouseDown(document.body);
    expect(screen.queryByTestId('asset-card-menu-popover')).toBeNull();
    expect(useGlobalChatStore.getState().pendingAsset).toBeNull();
  });

  it('names the trigger for a screen reader — it has no visible text', () => {
    render(<AssetCard asset={CHARACTER} onOpen={() => {}} />);
    expect(screen.getByTestId('asset-card-menu').getAttribute('aria-label')).toBe(
      'Actions for Sang Yao',
    );
  });
});

describe('AssetCard — the menu keyboard model', () => {
  // `role="menu"` announces a contract: arrows move, Escape closes and gives
  // focus back. Declaring the role without honouring it strands a
  // screen-reader user in a widget that just told them how it behaves and
  // then does not — worse than a plain popover, which promises nothing.

  it('moves focus into the first item when it opens', () => {
    render(<AssetCard asset={CHARACTER} onOpen={() => {}} />);
    fireEvent.click(screen.getByTestId('asset-card-menu'));
    expect(document.activeElement).toBe(screen.getByTestId('asset-card-send-to-agent'));
  });

  it('opens from the trigger on ArrowDown, the standard way in', () => {
    render(<AssetCard asset={CHARACTER} onOpen={() => {}} />);
    fireEvent.keyDown(screen.getByTestId('asset-card-menu'), { key: 'ArrowDown' });
    expect(screen.getByTestId('asset-card-menu-popover')).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByTestId('asset-card-send-to-agent'));
  });

  it('Escape closes it AND puts focus back on the trigger', () => {
    // Without the restore, closing drops the caret at the top of the document
    // and the user tabs back through the whole shelf to get here again.
    render(<AssetCard asset={CHARACTER} onOpen={() => {}} />);
    const trigger = screen.getByTestId('asset-card-menu');
    fireEvent.click(trigger);
    fireEvent.keyDown(screen.getByTestId('asset-card-menu-popover'), { key: 'Escape' });

    expect(screen.queryByTestId('asset-card-menu-popover')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('wraps on ArrowDown / ArrowUp and jumps with Home / End', () => {
    // One item today, so every move lands back on it. The assertion is that
    // focus STAYS inside the menu: the failure this guards is arrow keys
    // falling through to the page and scrolling it instead.
    render(<AssetCard asset={CHARACTER} onOpen={() => {}} />);
    fireEvent.click(screen.getByTestId('asset-card-menu'));
    const item = screen.getByTestId('asset-card-send-to-agent');
    const popover = screen.getByTestId('asset-card-menu-popover');

    for (const key of ['ArrowDown', 'ArrowUp', 'Home', 'End']) {
      fireEvent.keyDown(popover, { key });
      expect(document.activeElement, key).toBe(item);
    }
  });

  it('Enter on the focused item sends, exactly as a click does', () => {
    render(<AssetCard asset={CHARACTER} onOpen={() => {}} />);
    fireEvent.click(screen.getByTestId('asset-card-menu'));
    // A native <button> turns Enter into a click; asserting through the click
    // is asserting the same path the keyboard user takes.
    fireEvent.click(document.activeElement as HTMLElement);
    expect(useGlobalChatStore.getState().pendingAsset?.assetId).toBe('727145299382534300');
  });

  it('Tab closes it without stealing focus back', () => {
    // The user aimed their focus somewhere; a restore here would fight them.
    render(<AssetCard asset={CHARACTER} onOpen={() => {}} />);
    const trigger = screen.getByTestId('asset-card-menu');
    fireEvent.click(trigger);
    fireEvent.keyDown(screen.getByTestId('asset-card-menu-popover'), { key: 'Tab' });

    expect(screen.queryByTestId('asset-card-menu-popover')).toBeNull();
    expect(document.activeElement).not.toBe(trigger);
  });

  it('sending with the mouse also returns focus to the trigger', () => {
    render(<AssetCard asset={CHARACTER} onOpen={() => {}} />);
    const trigger = screen.getByTestId('asset-card-menu');
    fireEvent.click(trigger);
    fireEvent.click(screen.getByTestId('asset-card-send-to-agent'));
    expect(document.activeElement).toBe(trigger);
  });
});

describe('AssetCard — grid cell fill', () => {
  // The shelf lays cards out in a stretching grid, and the wrapper div added
  // for the action menu is now the grid ITEM. Without `h-full` on the button
  // inside it, each card shrinks to its own content and a row of cards with
  // different name / chip heights stops lining up along the bottom — the one
  // thing the stretching grid was doing for free.
  //
  // BOTH axes, and the width one is not symmetry for its own sake: a `<button>`
  // sizes to fit-content even at `display:flex`, so a block wrapper does NOT
  // stretch it the way the grid used to. Measured in Chrome on the real class
  // list, three cards in one 170px column came out 72 / 170 / 103 wide — each
  // card as wide as its own name — and because the cover is `aspect-[4/5]` of
  // that width, the covers came out 88 / 211 / 127 tall, so the titles landed
  // at three different heights inside a row of equal-height cells. The action
  // menu, absolutely positioned against the full-width WRAPPER, floated in the
  // empty gutter beside every card narrower than its cell.
  it('the wrapper fills its grid cell and the card button fills the wrapper', () => {
    render(<AssetCard asset={CHARACTER} onOpen={() => {}} />);
    const card = screen.getByTestId('asset-card');
    const wrapper = card.parentElement as HTMLElement;

    expect(wrapper.className).toContain('relative');
    expect(wrapper.className).toContain('h-full');
    expect(card.className).toContain('h-full');
    expect(card.className).toContain('w-full');
  });

  it('the menu is a sibling of the card, so it rides that same cell', () => {
    render(<AssetCard asset={CHARACTER} onOpen={() => {}} />);
    const card = screen.getByTestId('asset-card');
    expect(screen.getByTestId('asset-card-menu').closest('div')?.parentElement).toBe(
      card.parentElement,
    );
  });
});
