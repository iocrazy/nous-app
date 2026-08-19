/**
 * useComposerResourceAttach — both ways a library asset reaches the chat
 * composer: the context menu's "Send to Agent" (via globalChatStore's
 * one-shot channel) and the @ picker's selection.
 *
 * Both now STAGE the asset in the attachment row above the composer instead
 * of inserting a chip into the sentence. That is what retired the old
 * frame-by-frame retry: the editor could mount late (RECON#20), the panel's
 * own state cannot. What survives is the channel race — a resource sent
 * before the panel mounted must still be picked up — plus the trigger side,
 * which is where the money is: every path that attaches an asset must make
 * sure the agent will have something to read, and say what that cost.
 */
import React from 'react';
import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const ensureMock = vi.fn();
vi.mock('../utils/ensureResourceProcessed', () => ({
  ensureResourceProcessed: (input: unknown) => ensureMock(input),
}));

import { useComposerResourceAttach } from './useComposerResourceAttach';
import { useGlobalChatStore } from '../stores/globalChatStore';

const stageResource = vi.fn();
const notify = vi.fn();
const t = (_k: string, def: string, opts?: Record<string, unknown>) =>
  def.replace(/\{\{(\w+)\}\}/g, (_m, n) => String(opts?.[n] ?? ''));

function setup(over: Record<string, unknown> = {}) {
  const setSelectedAgentSlug = vi.fn();
  const opts = {
    stageResource,
    selectedAgentSlug: 'script_ai',
    setSelectedAgentSlug,
    lockedAgent: null,
    notify,
    t,
    ...over,
  };
  const view = renderHook((p: any) => useComposerResourceAttach(p), { initialProps: opts });
  return { view, setSelectedAgentSlug, opts };
}

/** What the resource context menu hands the store. */
function pending(over: Record<string, unknown> = {}) {
  return {
    resourceId: '339710259795355',
    name: 'clip.mp4',
    kind: 'video' as const,
    mime: 'video/mp4',
    scope: { type: 'personal' as const, id: 's1' },
    thumbnailUrl: '/api/v1/resources/339710259795355/cover',
    transcriptStatus: 'completed',
    summaryStatus: 'none',
    ...over,
  };
}

beforeEach(() => {
  stageResource.mockClear();
  notify.mockClear();
  ensureMock.mockReset().mockResolvedValue({ action: 'skipped' });
  useGlobalChatStore.setState({ pendingResource: null, pendingQuote: null, chatRequest: null });
});

describe('pendingResource consumption', () => {
  it('stages the resource and consumes the channel', () => {
    setup();

    act(() => { useGlobalChatStore.getState().sendResourceToChat(pending()); });

    expect(stageResource).toHaveBeenCalledTimes(1);
    expect(useGlobalChatStore.getState().pendingResource).toBeNull();
  });

  it('picks the resource up when it was sent BEFORE the panel mounted', () => {
    // The surviving race: "Send to Agent" opens the panel and stages in one
    // store write, so the first effect pass may be the first chance anyone
    // has to read the channel. Dropping it here loses the asset silently.
    act(() => { useGlobalChatStore.getState().sendResourceToChat(pending()); });
    expect(stageResource).not.toHaveBeenCalled();

    setup();

    expect(stageResource).toHaveBeenCalledTimes(1);
    expect(useGlobalChatStore.getState().pendingResource).toBeNull();
  });

  it('carries the status snapshot onto the chip under the insert-item field names', () => {
    setup();

    act(() => { useGlobalChatStore.getState().sendResourceToChat(pending()); });

    // PendingResource is camelCase; the staged item takes the search-row
    // names — `id`, not `resourceId`. Getting this wrong stages a chip
    // with no resource at all.
    expect(stageResource).toHaveBeenCalledWith(
      expect.objectContaining({
        id: '339710259795355',
        name: 'clip.mp4',
        kind: 'video',
        mime: 'video/mp4',
        thumbnail_url: '/api/v1/resources/339710259795355/cover',
        transcript_status: 'completed',
        summary_status: 'none',
      }),
    );
  });

  it('picks a default agent only when none is selected', () => {
    const a = setup({ selectedAgentSlug: null });
    act(() => { useGlobalChatStore.getState().sendResourceToChat(pending()); });
    expect(a.setSelectedAgentSlug).toHaveBeenCalledWith('analyze');

    const b = setup({ selectedAgentSlug: 'script_ai' });
    act(() => { useGlobalChatStore.getState().sendResourceToChat(pending({ resourceId: '2' })); });
    expect(b.setSelectedAgentSlug).not.toHaveBeenCalled();
  });

  it('never switches an agent-locked panel', () => {
    const { setSelectedAgentSlug } = setup({ selectedAgentSlug: null, lockedAgent: 'summarize' });

    act(() => { useGlobalChatStore.getState().sendResourceToChat(pending()); });

    expect(setSelectedAgentSlug).not.toHaveBeenCalled();
    expect(stageResource).toHaveBeenCalledTimes(1);
  });

  it('fires again for a repeat send of the same resource', () => {
    // The CHANNEL must not swallow the second send — whether that ends up
    // as one chip or two is the staging reducer's call (see
    // stagedResources.test.ts), not this hook's.
    setup();

    act(() => { useGlobalChatStore.getState().sendResourceToChat(pending()); });
    act(() => { useGlobalChatStore.getState().sendResourceToChat(pending()); });

    expect(stageResource).toHaveBeenCalledTimes(2);
  });
});

describe('attachResource (@ picker path)', () => {
  it('stages the asset and tops up whatever processing it is missing', async () => {
    ensureMock.mockResolvedValue({ action: 'triggered_transcribe', pointsCharged: 5 });
    const { view } = setup();

    await act(async () => {
      view.result.current.attachResource({
        id: 'r-9', name: 'talk.mp4', kind: 'video', mime: 'video/mp4',
        transcript_status: 'none', summary_status: 'none',
      });
    });

    expect(stageResource).toHaveBeenCalledTimes(1);
    expect(ensureMock).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'r-9', kind: 'video', transcript_status: 'none', summary_status: 'none',
      }),
    );
    expect(notify).toHaveBeenCalledWith(expect.stringContaining('5'), 'info');
  });

  it('tells the user when the trigger failed instead of failing silently', async () => {
    ensureMock.mockResolvedValue({
      action: 'failed', attempted: 'transcribe', error: 'HTTP 402 Insufficient points',
    });
    const { view } = setup();

    await act(async () => {
      view.result.current.attachResource({
        id: 'r-10', name: 'talk.mp4', kind: 'video', transcript_status: 'none',
      });
    });

    expect(notify).toHaveBeenCalledWith(
      expect.stringContaining('Insufficient points'),
      'error',
    );
  });

  it('stays quiet when there was nothing to do', async () => {
    ensureMock.mockResolvedValue({ action: 'ready' });
    const { view } = setup();

    await act(async () => {
      view.result.current.attachResource({ id: 'r-11', name: 'a.png', kind: 'image' });
    });

    expect(notify).not.toHaveBeenCalled();
  });
});
