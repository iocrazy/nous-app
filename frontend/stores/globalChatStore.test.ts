/**
 * globalChatStore one-shot channels.
 *
 * Three independent "open the panel and do THIS once" channels live side
 * by side (chatRequest / pendingQuote / pendingResource). They share the
 * nonce + consume shape but must never clear each other — a Send to Agent
 * click while a quote is staged has to leave the quote alone.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { useGlobalChatStore } from './globalChatStore';

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
