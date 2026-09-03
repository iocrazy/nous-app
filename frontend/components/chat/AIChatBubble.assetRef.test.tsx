/**
 * The user bubble's `asset_ref` branch (P5).
 *
 * Without it an asset attachment falls through to the strip's grey filename
 * chip and reads as an unrecognised file. That failure does not crash and does
 * not log — which is exactly why it needs a test rather than a code review.
 *
 * The attachments here are the PERSISTED shape, not the composer's: the
 * reducer's key whitelist in `conversations_ai_store.py` keeps
 * `kind / asset_id / loadout_id / mime / alt_text / name` and drops the rest,
 * so `asset_type` and `cover_file_id` are absent on purpose. A fixture that
 * carried them would let a chip that depends on them pass here and render
 * blank in production.
 */
import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, arg2?: unknown) => (typeof arg2 === 'string' ? arg2 : key),
  }),
}));

const useRunToolActivityMock = vi.fn();
vi.mock('../agentActivity/useRunToolActivity', () => ({
  useRunToolActivity: (...args: unknown[]) => useRunToolActivityMock(...args),
}));

import { MessageBubble } from './AIChatBubble';

const ASSET_ATT = {
  kind: 'asset_ref',
  asset_id: '727145299382534300',
  loadout_id: null,
  name: 'Sang Yao',
  mime: '',
};

const RESOURCE_ATT = {
  kind: 'resource_ref',
  resource_id: '339710259795355',
  mime: 'video/mp4',
  alt_text: 'pitch.mp4',
};

beforeEach(() => {
  useRunToolActivityMock.mockReset();
  useRunToolActivityMock.mockReturnValue({ activities: [], denials: [], loaded: true });
});

function renderBubble(attachments: unknown[]) {
  return render(
    <MemoryRouter>
      <MessageBubble
        role="user"
        content="what would she wear here?"
        attachments={attachments as never}
      />
    </MemoryRouter>,
  );
}

describe('MessageBubble — asset_ref attachments', () => {
  it('renders an asset chip, not the unknown-file fallback', () => {
    renderBubble([ASSET_ATT]);
    expect(screen.getByTestId('bubble-asset-chip')).toBeTruthy();
    expect(screen.getByTestId('bubble-asset-chip').textContent).toContain('Sang Yao');
  });

  it('carries the asset id so the chip can be traced back to a row', () => {
    renderBubble([ASSET_ATT]);
    expect(screen.getByTestId('bubble-asset-chip').getAttribute('data-asset-id')).toBe(
      '727145299382534300',
    );
  });

  it('asks the cover route for nothing — an asset id is not a resource id', () => {
    // `cover_file_id` never crosses the boundary, so any <img> here would be
    // a guess, and a guess built from `asset_id` 404s on every render.
    const { container } = renderBubble([ASSET_ATT]);
    expect(container.querySelectorAll('img')).toHaveLength(0);
    expect(screen.getByTestId('bubble-asset-chip-icon')).toBeTruthy();
  });

  it('falls back to alt_text, then to a label — never to an empty chip', () => {
    const { unmount } = renderBubble([{ ...ASSET_ATT, name: undefined, alt_text: 'Fan Qi' }]);
    expect(screen.getByTestId('bubble-asset-chip').textContent).toContain('Fan Qi');
    unmount();

    renderBubble([{ ...ASSET_ATT, name: undefined, alt_text: undefined }]);
    expect(screen.getByTestId('bubble-asset-chip').textContent).toContain(
      'chat.attachments.assetRef',
    );
  });

  it('keeps assets and resources apart in a mixed turn', () => {
    renderBubble([RESOURCE_ATT, ASSET_ATT]);
    expect(screen.getByTestId('bubble-resource-chip').textContent).toContain('pitch.mp4');
    expect(screen.getByTestId('bubble-asset-chip').textContent).toContain('Sang Yao');
  });

  it('gives two assets in one turn distinct keys, so both render', () => {
    // The strip's react key used to be built from `resource_id`, which an
    // asset attachment does not carry: two assets would have collided on the
    // same `undefined-…` prefix had the index not been part of it.
    renderBubble([
      ASSET_ATT,
      { ...ASSET_ATT, asset_id: '727145299382534303', name: 'Fan Qi' },
    ]);
    expect(screen.getAllByTestId('bubble-asset-chip')).toHaveLength(2);
  });
});
