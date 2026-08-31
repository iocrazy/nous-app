/**
 * `GenerationHistoryPanel`: the panel Task 7 could not build.
 *
 * The filter IS the feature. Without `source_asset_id` the same endpoint
 * answers with the scope's whole inbox, which renders identically — a page
 * full of thumbnails under this asset's name — so the assertion that matters
 * is on the ARGUMENTS, not on the fact that something rendered.
 *
 * `state: 'all'` is the second half of that: the endpoint defaults to
 * `unreviewed`, and a history that dropped a generation the moment somebody
 * saved it would answer "what did this asset produce" with "what has nobody
 * looked at yet".
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

import { i18nMock, SCOPE_ID } from './sheetTestUtils';

vi.mock('react-i18next', () => i18nMock);

const fetchGenerated = vi.fn();
vi.mock('../../../../services/generatedService', () => ({
  fetchGenerated: (...a: unknown[]) => fetchGenerated(...a),
}));

vi.mock('../../../../services/generatedMediaService', () => ({
  generatedMediaCoverUrl: (id: string) => `/api/v1/generated-media/${id}/cover`,
}));

import { GenerationHistoryPanel } from './GenerationHistoryPanel';

const ASSET_ID = '727145299382534300';

/** A real `/generated` item — string ids throughout, `source_asset_id` set. */
function makeItem(over: Partial<Record<string, unknown>> & { id: string }) {
  return {
    scope_id: SCOPE_ID,
    media_kind: 'image',
    mime: 'image/png',
    prompt: 'character sheet…',
    model: 'seedream-4',
    provider: 'volcengine',
    origin_kind: 'agent_run',
    canvas_id: null,
    node_id: `asset:${ASSET_ID}:sheet`,
    created_at: '2026-08-30T09:00:00Z',
    promoted_resource_id: null,
    review_state: 'unreviewed',
    source_asset_id: ASSET_ID,
    source: {
      kind: 'agent_run',
      label: 'Agent Run',
      canvas_id: null,
      node_id: `asset:${ASSET_ID}:sheet`,
      shot_id: null,
      conversation_id: null,
      deep_link: null,
    },
    title: 'character sheet',
    ...over,
  };
}

const onOpenInbox = vi.fn();

function renderPanel() {
  return render(
    <GenerationHistoryPanel scopeId={SCOPE_ID} assetId={ASSET_ID} onOpenInbox={onOpenInbox} />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchGenerated.mockResolvedValue({
    items: [makeItem({ id: '800000000000000001' }), makeItem({ id: '800000000000000002' })],
    next_cursor: null,
  });
});

describe('the fetch', () => {
  it('asks for THIS asset, in every state', async () => {
    renderPanel();
    await waitFor(() => expect(fetchGenerated).toHaveBeenCalled());
    expect(fetchGenerated).toHaveBeenCalledWith(SCOPE_ID, {
      sourceAssetId: ASSET_ID,
      state: 'all',
      limit: 8,
    });
  });

  it('renders one tile per item, keyed on the generation id', async () => {
    renderPanel();
    const tiles = await screen.findAllByTestId('history-item');
    expect(tiles.map((tile) => tile.getAttribute('data-generation-id'))).toEqual([
      '800000000000000001',
      '800000000000000002',
    ]);
    expect(tiles[0].querySelector('img')).toHaveAttribute(
      'src',
      '/api/v1/generated-media/800000000000000001/cover',
    );
  });

  it('is fetched ONCE, not once per render', async () => {
    // Same hazard class as the `t`-identity loop next door in
    // `GenerateMissingDialog`: this panel sits in a sidebar that re-renders on
    // every detail load, and an effect whose deps were not both primitive
    // (or that reached for `t`) would re-request forever. Rerendering with a
    // FRESH element is what makes the guard non-vacuous — a stable one would
    // not re-run the component body at all.
    const { rerender } = renderPanel();
    await screen.findAllByTestId('history-item');
    for (let i = 0; i < 3; i += 1) {
      rerender(
        <GenerationHistoryPanel
          scopeId={SCOPE_ID}
          assetId={ASSET_ID}
          onOpenInbox={onOpenInbox}
        />,
      );
    }
    await waitFor(() => expect(fetchGenerated).toHaveBeenCalledTimes(1));
  });

  it('opens the inbox from a tile and from the link', async () => {
    renderPanel();
    const tiles = await screen.findAllByTestId('history-item');
    tiles[0].click();
    screen.getByTestId('history-open-inbox').click();
    expect(onOpenInbox).toHaveBeenCalledTimes(2);
  });
});

describe('the two empty-looking states are not the same state', () => {
  it('says nothing has been generated when the answer really is none', async () => {
    fetchGenerated.mockResolvedValue({ items: [], next_cursor: null });
    renderPanel();
    await waitFor(() => expect(screen.queryByText('Loading...')).toBeNull());
    expect(screen.getByText('Nothing Generated Yet')).toBeTruthy();
    expect(screen.queryByTestId('history-failed')).toBeNull();
  });

  it('says the history is unavailable when the fetch failed', async () => {
    // "This asset has generated nothing" is a CLAIM. We only get to make it
    // when we know — a failed request knows nothing.
    fetchGenerated.mockRejectedValue(new Error('offline'));
    renderPanel();
    expect(await screen.findByTestId('history-failed')).toBeTruthy();
    expect(screen.queryByText('Nothing Generated Yet')).toBeNull();
  });
});
