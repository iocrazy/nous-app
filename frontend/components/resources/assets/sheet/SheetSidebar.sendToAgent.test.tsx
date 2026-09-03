/**
 * Entry point ONE for Send To Agent: the asset sheet's sidebar.
 *
 * This file exists because of the resource-library precedent, not in spite of
 * it: that feature shipped wired to one of its two menus, and the other one
 * silently did nothing for a release. So each call site gets its own test that
 * the helper was actually reached, and the helper's own behaviour is pinned
 * separately in `utils/sendAssetToAgent.test.ts`.
 *
 * What is asserted here is the button — that it is LIVE (the P5 placeholder is
 * gone) and that clicking it puts THIS asset on the channel.
 */
import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { i18nMock } from './sheetTestUtils';

vi.mock('react-i18next', () => i18nMock);

// The sidebar's other neighbours: the history panel fetches, and Send To
// Canvas opens a dialog that lists canvases. Neither is what this asks about.
vi.mock('./GenerationHistoryPanel', () => ({
  GenerationHistoryPanel: () => null,
}));
vi.mock('./SendAssetToCanvasDialog', () => ({
  SendAssetToCanvasDialog: () => null,
}));
vi.mock('../../../../features/canvas-core/services/canvasService', () => ({
  createCanvas: vi.fn(),
}));

import { SheetSidebar } from './SheetSidebar';
import { CHARACTER_DETAIL } from './assetSheetFixtures';
import { useGlobalChatStore } from '../../../../stores/globalChatStore';

function renderSidebar() {
  return render(
    <SheetSidebar
      scopeId="727145299382534200"
      detail={CHARACTER_DETAIL}
      loadout={null}
      readOnly={false}
      projects={[]}
      projectsLoading={false}
      projectsFailed={false}
      projectNames={{}}
      onCopyLoadoutPrompt={() => {}}
      onDuplicate={() => {}}
      onDelete={() => {}}
      busy={false}
      onOpenCanvas={() => {}}
      onOpenInbox={() => {}}
      onError={() => {}}
    />,
  );
}

beforeEach(() => {
  useGlobalChatStore.setState({ open: false, pendingAsset: null });
});

describe('SheetSidebar — Send To Agent', () => {
  it('is enabled: the P5 placeholder is gone, not merely relabelled', () => {
    renderSidebar();
    const button = screen.getByTestId('send-to-agent') as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    // The old title was the whole promise ("Arrives with P5"). A live button
    // still carrying it would read as broken.
    expect(button.getAttribute('title')).toBeNull();
  });

  it('stages THIS asset on the pendingAsset channel and opens the chat', () => {
    renderSidebar();
    fireEvent.click(screen.getByTestId('send-to-agent'));

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

  it('sends again on a second click — the nonce is what re-fires it', () => {
    renderSidebar();
    fireEvent.click(screen.getByTestId('send-to-agent'));
    fireEvent.click(screen.getByTestId('send-to-agent'));
    expect(useGlobalChatStore.getState().pendingAsset?.nonce).toBe(2);
  });

  it('works on a read-only system preset too', () => {
    // Read-only bars EDITING. A preset is exactly the kind of asset a user
    // wants to hand an agent, so gating the send on it would be wrong.
    render(
      <SheetSidebar
        scopeId={null}
        detail={{ ...CHARACTER_DETAIL, scope_id: null, is_system_preset: true }}
        loadout={null}
        readOnly
        projects={[]}
        projectsLoading={false}
        projectsFailed={false}
        projectNames={{}}
        onCopyLoadoutPrompt={() => {}}
        onDuplicate={() => {}}
        onDelete={() => {}}
        busy={false}
        onOpenCanvas={() => {}}
        onOpenInbox={() => {}}
        onError={() => {}}
      />,
    );
    fireEvent.click(screen.getAllByTestId('send-to-agent')[0]);
    expect(useGlobalChatStore.getState().pendingAsset?.scopeId).toBeNull();
  });
});
