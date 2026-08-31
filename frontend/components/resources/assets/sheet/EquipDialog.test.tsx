/**
 * `EquipDialog`: search, multi-select, one batch call, and what happens when
 * the batch is refused.
 *
 * The refusal half is the point of this file. `POST /assets/{id}/files` with
 * `items` is ALL-OR-NOTHING server-side (one `unit_of_work()`), so there is no
 * such thing as a mixed envelope: a rejection means nothing was attached, and
 * the code names the reason for the BATCH, not for an item. A dialog that
 * cleared the selection or implied partial success on that path would be
 * describing a wire contract that does not exist.
 *
 * The search rows are real `/resources/search` shapes: string ids and a
 * `scope: {type, id}` whose id is `resource_items.scope_id` — the very column
 * `attach_file` compares against, which is why the out-of-scope row here is a
 * faithful reproduction of the 404 rather than a made-up state.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { i18nMock, SCOPE_ID, TEAM_ID, UiModalStub } from './sheetTestUtils';

vi.mock('react-i18next', () => i18nMock);
vi.mock('../../../ui/primitives', () => ({ UiModal: UiModalStub }));

const addToast = vi.fn();
vi.mock('../../../Toast', () => ({ useToast: () => ({ addToast }) }));

vi.mock('../../../../services/resourceService', () => ({
  getResourceCoverUrl: (id: string) => `/api/v1/resources/${id}/cover`,
}));

const searchState = {
  data: { results: [] as unknown[], counts: {}, next_cursor: null },
  loading: false,
  error: null as Error | null,
};
const useResourceSearch = vi.fn(() => searchState);
vi.mock('../../../../hooks/useResourceSearch', () => ({
  useResourceSearch: (...a: unknown[]) => useResourceSearch(...(a as [])),
}));

const attachFiles = vi.fn();
vi.mock('../../../../services/assetsService', () => ({
  attachFiles: (...a: unknown[]) => attachFiles(...a),
}));

import { EquipDialog } from './EquipDialog';
import { CHARACTER_DETAIL } from './assetSheetFixtures';

/** One `/resources/search` result, verbatim wire shape. */
function makeResult(over: Partial<Record<string, unknown>> & { id: string }) {
  return {
    name: 'harbour-wide.png',
    kind: 'image',
    mime: 'image/png',
    size: 84213,
    scope: { type: 'team', id: SCOPE_ID },
    updated_at: '2026-08-29T10:00:00Z',
    thumbnail_url: `/api/v1/resources/${over.id}/cover`,
    transcript_status: null,
    summary_status: null,
    ...over,
  };
}

const onClose = vi.fn();
const onAttached = vi.fn();
const onError = vi.fn();

function renderDialog(props: Partial<React.ComponentProps<typeof EquipDialog>> = {}) {
  return render(
    <EquipDialog
      open
      scopeId={SCOPE_ID}
      teamId={TEAM_ID}
      detail={CHARACTER_DETAIL}
      slot="expressions"
      loadoutId={null}
      loadoutName={null}
      onClose={onClose}
      onAttached={onAttached}
      onError={onError}
      {...props}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  searchState.data = {
    results: [makeResult({ id: '900000000000000001' }), makeResult({ id: '900000000000000002', name: 'harbour-tight.png' })],
    counts: {},
    next_cursor: null,
  };
  searchState.loading = false;
  searchState.error = null;
  attachFiles.mockResolvedValue([{}, {}]);
});

describe('picking', () => {
  it('searches the library and lists what came back', () => {
    renderDialog();
    fireEvent.change(screen.getByTestId('equip-search'), { target: { value: 'harbour' } });
    // Third positional arg is the team, which is what scopes the search.
    expect(useResourceSearch).toHaveBeenLastCalledWith('harbour', '', TEAM_ID);
    expect(screen.getAllByTestId('equip-candidate')).toHaveLength(2);
  });

  it('multi-selects and attaches every pick in ONE batch call', async () => {
    renderDialog();
    const rows = screen.getAllByTestId('equip-candidate');
    fireEvent.click(rows[0]);
    fireEvent.click(rows[1]);
    expect(screen.getByTestId('equip-submit')).not.toBeDisabled();

    fireEvent.click(screen.getByTestId('equip-submit'));

    await waitFor(() => expect(attachFiles).toHaveBeenCalledTimes(1));
    expect(attachFiles).toHaveBeenCalledWith(SCOPE_ID, CHARACTER_DETAIL.id, [
      { resource_id: '900000000000000001', slot: 'expressions', loadout_id: null },
      { resource_id: '900000000000000002', slot: 'expressions', loadout_id: null },
    ]);
    await waitFor(() => expect(onAttached).toHaveBeenCalledWith(2));
    expect(onClose).toHaveBeenCalled();
    // The i18n stub renders slot labels as their raw key value; the real
    // `saveAsAsset.slot.*` bundle Title-Cases them (pinned in i18nParity).
    expect(addToast).toHaveBeenCalledWith('Attached 2 To expressions', 'success');
  });

  it('deselects on a second click', () => {
    renderDialog();
    const row = screen.getAllByTestId('equip-candidate')[0];
    fireEvent.click(row);
    expect(row).toHaveAttribute('data-picked', 'true');
    fireEvent.click(row);
    expect(row).toHaveAttribute('data-picked', 'false');
    expect(screen.getByTestId('equip-submit')).toBeDisabled();
  });

  it('attaches nothing when nothing is selected', () => {
    renderDialog();
    fireEvent.click(screen.getByTestId('equip-submit'));
    expect(attachFiles).not.toHaveBeenCalled();
  });
});

describe('what cannot be picked, and why it is still shown', () => {
  it('a resource from another workspace is disabled with a reason, not hidden', () => {
    searchState.data.results = [
      makeResult({ id: '900000000000000003', scope: { type: 'personal', id: '111' } }),
    ];
    renderDialog();
    const row = screen.getByTestId('equip-candidate');
    // Present: a user who searched for this file by name must not conclude it
    // does not exist.
    expect(row).toBeDisabled();
    expect(within(row).getByTestId('equip-out-of-scope')).toHaveTextContent('Other Workspace');
  });

  it('a file already in this slot under this loadout is marked Attached', () => {
    // `727145299382534146` is the fixture's `sheet` file.
    searchState.data.results = [makeResult({ id: '727145299382534146' })];
    renderDialog({ slot: 'sheet' });
    const row = screen.getByTestId('equip-candidate');
    expect(row).toBeDisabled();
    expect(within(row).getByTestId('equip-already')).toBeTruthy();
  });

  it('the SAME file is pickable for a different slot', () => {
    // The negative control for the test above: "attached" is per (slot,
    // loadout), not per resource, and a check on the resource alone would make
    // a file that lives in one slot unusable in every other.
    searchState.data.results = [makeResult({ id: '727145299382534146' })];
    renderDialog({ slot: 'stills' });
    expect(screen.getByTestId('equip-candidate')).not.toBeDisabled();
  });
});

describe('attaching can be a MOVE, and it has to say so', () => {
  // The server's upsert conflicts on (asset_id, resource_id, slot) with
  // `loadout_id` in its SET clause, so re-attaching a file that is already on
  // this slot under another loadout REWRITES that column — the file leaves the
  // outfit it was in. Eligibility read per (slot, loadout) would have offered
  // it as a plain candidate and called the result an addition.
  //
  // `727145299382534160` is the fixture's `worn` file under loadout ...400.
  // `727145299382534162` is the fixture's `worn` file under NO loadout.
  const IN_400 = '727145299382534160';
  const IN_NONE = '727145299382534162';

  it('A to B: the row is labelled and the move is announced before the click that commits it', async () => {
    searchState.data.results = [makeResult({ id: IN_400, name: 'night-robe-back.png' })];
    renderDialog({ slot: 'worn', loadoutId: '727145299382534401', loadoutName: 'Night raid' });

    const row = screen.getByTestId('equip-candidate');
    // Offered, not hidden: moving a file between outfits is a real thing to
    // want. It is the SILENCE that was the defect.
    expect(row).not.toBeDisabled();
    expect(row).toHaveAttribute('data-moves', 'true');
    // Named, not "another loadout": the user has to know which outfit empties.
    expect(within(row).getByTestId('equip-elsewhere')).toHaveTextContent('In Default');
    expect(screen.queryByTestId('equip-move-warning')).toBeNull();

    fireEvent.click(row);

    const warning = screen.getByTestId('equip-move-warning');
    expect(warning).toHaveTextContent('night-robe-back.png will move from Default to Night raid');

    fireEvent.click(screen.getByTestId('equip-submit'));
    await waitFor(() => expect(attachFiles).toHaveBeenCalled());
    // The toast counts it as a move, not as an addition.
    expect(addToast).toHaveBeenCalledWith('Moved 1 To worn', 'success');
  });

  it('null to X: "shows under every outfit" becoming "this outfit only" is announced too', async () => {
    searchState.data.results = [makeResult({ id: IN_NONE, name: 'scar-detail.png' })];
    renderDialog({ slot: 'worn', loadoutId: '727145299382534401', loadoutName: 'Night raid' });

    const row = screen.getByTestId('equip-candidate');
    expect(row).toHaveAttribute('data-moves', 'true');
    // `loadout_id: null` is a PLACEMENT (shows under every outfit), not an
    // absence — reading it as "not attached" is what made this silent.
    expect(within(row).getByTestId('equip-elsewhere')).toHaveTextContent('In Every Loadout');

    fireEvent.click(row);
    expect(screen.getByTestId('equip-move-warning')).toHaveTextContent(
      'scar-detail.png will move from Every Loadout to Night raid',
    );
  });

  it('X to null: dropping the loadout is a move in the other direction', async () => {
    // Reachable whenever a `worn` attach happens with no loadout selected.
    searchState.data.results = [makeResult({ id: IN_400, name: 'night-robe-back.png' })];
    renderDialog({ slot: 'worn', loadoutId: null, loadoutName: null });

    fireEvent.click(screen.getByTestId('equip-candidate'));
    expect(screen.getByTestId('equip-move-warning')).toHaveTextContent(
      'night-robe-back.png will move from Default to Every Loadout',
    );
  });

  it('counts additions and moves separately in the toast', async () => {
    searchState.data.results = [
      makeResult({ id: IN_400, name: 'night-robe-back.png' }),
      makeResult({ id: '900000000000000009', name: 'brand-new.png' }),
    ];
    renderDialog({ slot: 'worn', loadoutId: '727145299382534401', loadoutName: 'Night raid' });
    const rows = screen.getAllByTestId('equip-candidate');
    fireEvent.click(rows[0]);
    fireEvent.click(rows[1]);
    fireEvent.click(screen.getByTestId('equip-submit'));

    await waitFor(() => expect(attachFiles).toHaveBeenCalled());
    // "Attached 2" would be a true number attached to a false claim.
    expect(addToast).toHaveBeenCalledWith('Attached 1 To worn, Moved 1', 'success');
  });

  it('an addition is still just an addition', async () => {
    // The negative control: without it, a bug that called EVERY attach a move
    // would pass every test above.
    searchState.data.results = [makeResult({ id: '900000000000000009' })];
    renderDialog({ slot: 'worn', loadoutId: '727145299382534401', loadoutName: 'Night raid' });
    const row = screen.getByTestId('equip-candidate');
    expect(row).toHaveAttribute('data-moves', 'false');
    fireEvent.click(row);
    expect(screen.queryByTestId('equip-move-warning')).toBeNull();
    fireEvent.click(screen.getByTestId('equip-submit'));
    await waitFor(() => expect(attachFiles).toHaveBeenCalled());
    expect(addToast).toHaveBeenCalledWith('Attached 1 To worn', 'success');
  });
});

describe('a pick never becomes invisible', () => {
  it('stays on screen, and deselectable, when the search no longer returns it', async () => {
    renderDialog();
    fireEvent.click(screen.getAllByTestId('equip-candidate')[0]);

    // A new query that returns something else entirely.
    searchState.data = {
      results: [makeResult({ id: '900000000000000007', name: 'unrelated.png' })],
      counts: {},
      next_cursor: null,
    };
    fireEvent.change(screen.getByTestId('equip-search'), { target: { value: 'unrelated' } });

    const pinned = await screen.findByTestId('equip-picked');
    const pick = within(pinned).getByTestId('equip-candidate');
    expect(pick).toHaveAttribute('data-resource-id', '900000000000000001');
    // The counter was already accurate; what was missing was any way to SEE
    // or undo the pick it was counting.
    expect(screen.getByText('1 Selected')).toBeTruthy();

    fireEvent.click(pick);
    expect(screen.getByText('0 Selected')).toBeTruthy();
    expect(screen.queryByTestId('equip-picked')).toBeNull();
  });

  it('does not pin a pick the current results already show', async () => {
    renderDialog();
    fireEvent.click(screen.getAllByTestId('equip-candidate')[0]);
    // Otherwise every pick would render twice.
    expect(screen.queryByTestId('equip-picked')).toBeNull();
  });
});

describe('the loadout only reaches `worn`', () => {
  it('stamps the selected loadout on a worn attach', async () => {
    renderDialog({ slot: 'worn', loadoutId: '727145299382534401', loadoutName: 'Night raid' });
    expect(screen.getByTestId('equip-target')).toHaveTextContent(
      'Attaching to worn of the Night raid loadout',
    );
    fireEvent.click(screen.getAllByTestId('equip-candidate')[0]);
    fireEvent.click(screen.getByTestId('equip-submit'));
    await waitFor(() => expect(attachFiles).toHaveBeenCalled());
    expect(attachFiles.mock.calls[0][2][0].loadout_id).toBe('727145299382534401');
  });

  it('leaves every other slot loadout-free even while an outfit is selected', async () => {
    // Stamping it would make the file the user just attached vanish the moment
    // they switched outfits, with nothing on screen saying where it went.
    renderDialog({ slot: 'stills', loadoutId: '727145299382534401', loadoutName: 'Night raid' });
    expect(screen.getByTestId('equip-target')).toHaveTextContent('Attaching to stills');
    fireEvent.click(screen.getAllByTestId('equip-candidate')[0]);
    fireEvent.click(screen.getByTestId('equip-submit'));
    await waitFor(() => expect(attachFiles).toHaveBeenCalled());
    expect(attachFiles.mock.calls[0][2][0].loadout_id).toBeNull();
  });
});

describe('a refused batch', () => {
  class ApiError extends Error {
    code: string;
    constructor(code: string) {
      super(code);
      this.code = code;
    }
  }

  it('says NOTHING was attached, names the code, and keeps the selection', async () => {
    attachFiles.mockRejectedValue(new ApiError('resource_not_found'));
    renderDialog();
    const rows = screen.getAllByTestId('equip-candidate');
    fireEvent.click(rows[0]);
    fireEvent.click(rows[1]);
    fireEvent.click(screen.getByTestId('equip-submit'));

    const alert = await screen.findByTestId('equip-refused');
    expect(alert).toHaveTextContent('Nothing Was Attached');
    // The CODE, not just a sentence: the locale may have no string for it,
    // and a generic line with nothing behind it leaves nothing to diagnose.
    expect(alert).toHaveAttribute('data-code', 'resource_not_found');
    // The dialog stays open on the same picks: the user has to change
    // something, and re-choosing five files they already chose is not that.
    expect(onClose).not.toHaveBeenCalled();
    expect(onAttached).not.toHaveBeenCalled();
    expect(screen.getAllByTestId('equip-candidate')[0]).toHaveAttribute('data-picked', 'true');
    // Still reported through the shared reporter (console + toast).
    expect(onError).toHaveBeenCalled();
  });

  it('can be retried after the refusal', async () => {
    attachFiles.mockRejectedValueOnce(new ApiError('resource_not_found'));
    renderDialog();
    fireEvent.click(screen.getAllByTestId('equip-candidate')[0]);
    fireEvent.click(screen.getByTestId('equip-submit'));
    await screen.findByTestId('equip-refused');

    attachFiles.mockResolvedValueOnce([{}]);
    fireEvent.click(screen.getByTestId('equip-submit'));
    await waitFor(() => expect(onAttached).toHaveBeenCalledWith(1));
    expect(attachFiles).toHaveBeenCalledTimes(2);
  });

  it('a failed SEARCH is not an empty library', () => {
    searchState.error = new Error('offline');
    searchState.data.results = [];
    renderDialog();
    expect(screen.getByTestId('equip-search-failed')).toBeTruthy();
    expect(screen.queryByText('No Files Match That Search')).toBeNull();
  });
});
