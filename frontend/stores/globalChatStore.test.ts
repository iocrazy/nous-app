/**
 * globalChatStore one-shot channels.
 *
 * FOUR independent "open the panel and do THIS once" channels live side by
 * side (chatRequest / pendingQuote / pendingResource / pendingAsset). They
 * share the nonce + consume shape but must never clear each other — a Send
 * To Agent click while a quote is staged has to leave the quote alone.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { useGlobalChatStore } from './globalChatStore';

const ASSET = {
  // Real wire shapes: `assets.id` and `scope_id` are Snowflake BIGINTs
  // serialized as STRINGS by the assets router, and `loadoutId` is null in
  // v1 (no loadout picker at either entry point).
  assetId: '727145299382534300',
  loadoutId: null,
  name: 'Sang Yao',
  assetType: 'character',
  coverFileId: '727145299382534146',
  scopeId: '727145299382534200',
};

const RESOURCE = {
  resourceId: '7301234567890123456',
  name: 'Interview Take 3.mp4',
  kind: 'video' as const,
  mime: 'video/mp4',
  scope: { type: 'personal' as const, id: 'u-1' },
};

beforeEach(() => {
  useGlobalChatStore.setState({
    open: false,
    chatRequest: null,
    pendingQuote: null,
    pendingResource: null,
    pendingAsset: null,
  });
});

describe('pendingResource channel', () => {
  it('opens the panel and stages the resource with a nonce', () => {
    useGlobalChatStore.getState().sendResourceToChat(RESOURCE);

    const s = useGlobalChatStore.getState();
    expect(s.open).toBe(true);
    expect(s.pendingResource).toEqual({ ...RESOURCE, nonce: 1 });
  });

  it('bumps the nonce so repeat sends of the same resource re-fire', () => {
    const { sendResourceToChat } = useGlobalChatStore.getState();
    sendResourceToChat(RESOURCE);
    sendResourceToChat(RESOURCE);

    expect(useGlobalChatStore.getState().pendingResource?.nonce).toBe(2);
  });

  it('clears on consume', () => {
    useGlobalChatStore.getState().sendResourceToChat(RESOURCE);
    useGlobalChatStore.getState().consumePendingResource();

    expect(useGlobalChatStore.getState().pendingResource).toBeNull();
  });

  it('leaves the other two channels untouched', () => {
    const st = useGlobalChatStore.getState();
    st.requestChat('analyze');
    st.sendSelectionToChat({ text: 'quoted line', sceneLabel: 'S2' });

    useGlobalChatStore.getState().sendResourceToChat(RESOURCE);
    useGlobalChatStore.getState().consumePendingResource();

    const after = useGlobalChatStore.getState();
    expect(after.chatRequest?.agentSlug).toBe('analyze');
    expect(after.pendingQuote?.text).toBe('quoted line');
  });

  it('is not cleared by the other channels being consumed', () => {
    useGlobalChatStore.getState().sendResourceToChat(RESOURCE);
    useGlobalChatStore.getState().consumeChatRequest();
    useGlobalChatStore.getState().consumePendingQuote();

    expect(useGlobalChatStore.getState().pendingResource?.name).toBe(
      'Interview Take 3.mp4',
    );
  });

  it('is never persisted — it holds a one-shot intent, not window state', () => {
    // partialize decides what survives a reload; a resurrected
    // pendingResource would re-insert a chip on every page load.
    const persisted = JSON.parse(
      localStorage.getItem('mediahub.global_chat') ?? '{}',
    );
    useGlobalChatStore.getState().sendResourceToChat(RESOURCE);
    const after = JSON.parse(
      localStorage.getItem('mediahub.global_chat') ?? '{}',
    );

    expect(persisted.state?.pendingResource).toBeUndefined();
    expect(after.state?.pendingResource).toBeUndefined();
  });
});

describe('pendingAsset channel', () => {
  it('opens the panel and stages the asset with a nonce', () => {
    useGlobalChatStore.getState().sendAssetToChat(ASSET);

    const s = useGlobalChatStore.getState();
    expect(s.open).toBe(true);
    expect(s.pendingAsset).toEqual({ ...ASSET, nonce: 1 });
  });

  it('bumps the nonce so repeat sends of the same asset re-fire', () => {
    const { sendAssetToChat } = useGlobalChatStore.getState();
    sendAssetToChat(ASSET);
    sendAssetToChat(ASSET);

    expect(useGlobalChatStore.getState().pendingAsset?.nonce).toBe(2);
  });

  it('clears on consume', () => {
    useGlobalChatStore.getState().sendAssetToChat(ASSET);
    useGlobalChatStore.getState().consumePendingAsset();

    expect(useGlobalChatStore.getState().pendingAsset).toBeNull();
  });

  it('leaves the other three channels untouched', () => {
    const st = useGlobalChatStore.getState();
    st.requestChat('analyze');
    st.sendSelectionToChat({ text: 'quoted line', sceneLabel: 'S2' });
    st.sendResourceToChat(RESOURCE);

    useGlobalChatStore.getState().sendAssetToChat(ASSET);
    useGlobalChatStore.getState().consumePendingAsset();

    const after = useGlobalChatStore.getState();
    expect(after.chatRequest?.agentSlug).toBe('analyze');
    expect(after.pendingQuote?.text).toBe('quoted line');
    expect(after.pendingResource?.name).toBe('Interview Take 3.mp4');
  });

  it('is not cleared by the other channels being consumed', () => {
    useGlobalChatStore.getState().sendAssetToChat(ASSET);
    useGlobalChatStore.getState().consumeChatRequest();
    useGlobalChatStore.getState().consumePendingQuote();
    useGlobalChatStore.getState().consumePendingResource();

    expect(useGlobalChatStore.getState().pendingAsset?.name).toBe('Sang Yao');
  });

  it('is never persisted — it holds a one-shot intent, not window state', () => {
    // partialize decides what survives a reload; a resurrected pendingAsset
    // would re-stage a chip on every page load.
    const persisted = JSON.parse(
      localStorage.getItem('mediahub.global_chat') ?? '{}',
    );
    useGlobalChatStore.getState().sendAssetToChat(ASSET);
    const after = JSON.parse(
      localStorage.getItem('mediahub.global_chat') ?? '{}',
    );

    expect(persisted.state?.pendingAsset).toBeUndefined();
    expect(after.state?.pendingAsset).toBeUndefined();
  });

  it('does not clear a staged resource — the two chips ride the same turn', () => {
    // A user can Send To Agent a video AND a character before typing. If
    // either channel cleared the other, the second send would silently drop
    // the first chip.
    useGlobalChatStore.getState().sendResourceToChat(RESOURCE);
    useGlobalChatStore.getState().sendAssetToChat(ASSET);

    const after = useGlobalChatStore.getState();
    expect(after.pendingResource?.resourceId).toBe('7301234567890123456');
    expect(after.pendingAsset?.assetId).toBe('727145299382534300');
  });
});
