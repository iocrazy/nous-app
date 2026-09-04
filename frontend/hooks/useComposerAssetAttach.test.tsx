/**
 * useComposerAssetAttach — the pendingAsset channel's receiving end.
 *
 * The properties that matter are the ones the one-shot channels were built
 * around: consume exactly once, do not clobber the user's agent choice, do not
 * touch the sibling channels — and, ruling I, do not trigger the paid AI
 * processing top-up the resource channel does.
 */
import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';

const ensureResourceProcessed = vi.fn(async () => ({ action: 'ready' }));
vi.mock('../utils/ensureResourceProcessed', () => ({
  ensureResourceProcessed: () => ensureResourceProcessed(),
}));

import { useGlobalChatStore } from '../stores/globalChatStore';
import { useComposerAssetAttach } from './useComposerAssetAttach';
import type { AssetRefInsertItem } from '../components/chat/stagedResources';

const ASSET = {
  assetId: '727145299382534300',
  loadoutId: null,
  name: 'Sang Yao',
  assetType: 'character',
  coverFileId: '727145299382534146',
  scopeId: '727145299382534200',
};

interface HostProps {
  stageAsset: (item: AssetRefInsertItem) => void;
  focusComposer?: () => void;
  selectedAgentSlug?: string | null;
  setSelectedAgentSlug?: (slug: string) => void;
  lockedAgent?: string | null;
}

const Host: React.FC<HostProps> = ({
  stageAsset,
  focusComposer,
  selectedAgentSlug = null,
  setSelectedAgentSlug = () => {},
  lockedAgent = null,
}) => {
  useComposerAssetAttach({
    stageAsset,
    focusComposer,
    selectedAgentSlug,
    setSelectedAgentSlug,
    lockedAgent,
  });
  return null;
};

beforeEach(() => {
  vi.clearAllMocks();
  useGlobalChatStore.setState({
    open: false,
    pendingAsset: null,
    pendingResource: null,
    pendingQuote: null,
    chatRequest: null,
  });
});

describe('useComposerAssetAttach', () => {
  it('stages the pending asset in the wire field names, not the camelCase ones', async () => {
    const stageAsset = vi.fn();
    render(<Host stageAsset={stageAsset} />);

    useGlobalChatStore.getState().sendAssetToChat(ASSET);

    await waitFor(() => expect(stageAsset).toHaveBeenCalledTimes(1));
    expect(stageAsset).toHaveBeenCalledWith({
      id: '727145299382534300',
      name: 'Sang Yao',
      asset_type: 'character',
      loadout_id: null,
      cover_file_id: '727145299382534146',
      scope_id: '727145299382534200',
    });
  });

  it('consumes the channel so a re-render cannot stage the same asset twice', async () => {
    const stageAsset = vi.fn();
    const { rerender } = render(<Host stageAsset={stageAsset} />);

    useGlobalChatStore.getState().sendAssetToChat(ASSET);
    await waitFor(() => expect(stageAsset).toHaveBeenCalledTimes(1));
    expect(useGlobalChatStore.getState().pendingAsset).toBeNull();

    rerender(<Host stageAsset={stageAsset} />);
    expect(stageAsset).toHaveBeenCalledTimes(1);
  });

  it('re-fires for a second send of the SAME asset', async () => {
    // The nonce is the only thing distinguishing them; without it the second
    // Send To Agent on one asset would do nothing.
    const stageAsset = vi.fn();
    render(<Host stageAsset={stageAsset} />);

    useGlobalChatStore.getState().sendAssetToChat(ASSET);
    await waitFor(() => expect(stageAsset).toHaveBeenCalledTimes(1));
    useGlobalChatStore.getState().sendAssetToChat(ASSET);
    await waitFor(() => expect(stageAsset).toHaveBeenCalledTimes(2));
  });

  it('puts the caret back in the composer', async () => {
    const focusComposer = vi.fn();
    render(<Host stageAsset={vi.fn()} focusComposer={focusComposer} />);

    useGlobalChatStore.getState().sendAssetToChat(ASSET);
    await waitFor(() => expect(focusComposer).toHaveBeenCalled());
  });

  it('fills an empty agent slot but never overrides a chosen agent', async () => {
    const setSelectedAgentSlug = vi.fn();
    const { unmount } = render(
      <Host stageAsset={vi.fn()} setSelectedAgentSlug={setSelectedAgentSlug} />,
    );
    useGlobalChatStore.getState().sendAssetToChat(ASSET);
    await waitFor(() => expect(setSelectedAgentSlug).toHaveBeenCalledWith('analyze'));
    unmount();

    const setAgain = vi.fn();
    render(
      <Host
        stageAsset={vi.fn()}
        selectedAgentSlug="script_ai"
        setSelectedAgentSlug={setAgain}
      />,
    );
    useGlobalChatStore.getState().sendAssetToChat(ASSET);
    await waitFor(() => expect(useGlobalChatStore.getState().pendingAsset).toBeNull());
    expect(setAgain).not.toHaveBeenCalled();
  });

  it('leaves an agent-locked panel alone', async () => {
    const setSelectedAgentSlug = vi.fn();
    render(
      <Host
        stageAsset={vi.fn()}
        lockedAgent="script_ai"
        setSelectedAgentSlug={setSelectedAgentSlug}
      />,
    );
    useGlobalChatStore.getState().sendAssetToChat(ASSET);
    await waitFor(() => expect(useGlobalChatStore.getState().pendingAsset).toBeNull());
    expect(setSelectedAgentSlug).not.toHaveBeenCalled();
  });

  it('never triggers the paid processing top-up (ruling I)', async () => {
    const stageAsset = vi.fn();
    render(<Host stageAsset={stageAsset} />);
    useGlobalChatStore.getState().sendAssetToChat(ASSET);
    await waitFor(() => expect(stageAsset).toHaveBeenCalled());
    expect(ensureResourceProcessed).not.toHaveBeenCalled();
  });

  it('ignores the resource channel entirely', async () => {
    // One effect per channel: a resource arriving must not stage an asset,
    // and must not consume the asset channel on its way past.
    const stageAsset = vi.fn();
    render(<Host stageAsset={stageAsset} />);

    useGlobalChatStore.getState().sendResourceToChat({
      resourceId: '7301234567890123456',
      name: 'Interview Take 3.mp4',
      kind: 'video',
      mime: 'video/mp4',
      scope: { type: 'personal', id: 'u-1' },
    });

    await waitFor(() =>
      expect(useGlobalChatStore.getState().pendingResource).not.toBeNull(),
    );
    expect(stageAsset).not.toHaveBeenCalled();
  });
});
