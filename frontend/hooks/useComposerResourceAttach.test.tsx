/**
 * useComposerResourceAttach — both ways a library asset reaches the chat
 * composer: the context menu's "Send to Agent" (via globalChatStore's
 * one-shot channel) and the @ picker's selection.
 *
 * The insertion side is a known race: the composer editor mounts a frame or
 * two after the panel opens (RECON#20), which is why the quote channel
 * already retries over frames. The trigger side is where the money is —
 * every path that attaches an asset must also make sure the agent will
 * have something to read, and must say out loud what that cost.
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

const insertResourceRef = vi.fn(() => true);
const focusRun = vi.fn();

function fakeEditor() {
  return {
    commands: { insertResourceRef },
    chain: () => ({ focus: () => ({ run: focusRun }) }),
  } as any;
}

const notify = vi.fn();
const t = (_k: string, def: string, opts?: Record<string, unknown>) =>
  def.replace(/\{\{(\w+)\}\}/g, (_m, n) => String(opts?.[n] ?? ''));

function setup(over: Record<string, unknown> = {}) {
  const editorRef = { current: fakeEditor() } as React.RefObject<any>;
  const setSelectedAgentSlug = vi.fn();
  const opts = {
    editorRef,
    selectedAgentSlug: 'script_ai',
    setSelectedAgentSlug,
    lockedAgent: null,
    notify,
    t,
    ...over,
  };
  const view = renderHook((p: any) => useComposerResourceAttach(p), { initialProps: opts });
  return { view, editorRef, setSelectedAgentSlug, opts };
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
  insertResourceRef.mockClear();
  focusRun.mockClear();
  notify.mockClear();
  ensureMock.mockReset().mockResolvedValue({ action: 'skipped' });
  useGlobalChatStore.setState({ pendingResource: null, pendingQuote: null, chatRequest: null });
});

describe('pendingResource consumption', () => {
  it('inserts the staged resource as a chip and consumes the channel', () => {
    setup();

    act(() => { useGlobalChatStore.getState().sendResourceToChat(pending()); });

    expect(insertResourceRef).toHaveBeenCalledTimes(1);
    expect(useGlobalChatStore.getState().pendingResource).toBeNull();
  });

  it('carries the status snapshot onto the chip under the insert-item field names', () => {
    setup();

    act(() => { useGlobalChatStore.getState().sendResourceToChat(pending()); });

    // PendingResource is camelCase; insertResourceRef takes the search-row
    // names — `id`, not `resourceId`. Getting this wrong renders a chip
    // with no resource at all.
    expect(insertResourceRef).toHaveBeenCalledWith(
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
    expect(insertResourceRef).toHaveBeenCalledTimes(1);
  });

  it('waits for the composer to mount instead of dropping the resource', () => {
    // cancelAnimationFrame is mocked to REALLY drop the frame. Mocking only
    // requestAnimationFrame makes this probe unfalsifiable: a cancel against
    // a fake handle is a no-op, so the queued callback survives and a
    // retry arm that was cancelled still looks alive.
    const frames = new Map<number, FrameRequestCallback>();
    let nextHandle = 1;
    const rafSpy = vi
      .spyOn(window, 'requestAnimationFrame')
      .mockImplementation((cb: FrameRequestCallback) => {
        const handle = nextHandle++;
        frames.set(handle, cb);
        return handle;
      });
    const cafSpy = vi
      .spyOn(window, 'cancelAnimationFrame')
      .mockImplementation((handle: number) => { frames.delete(handle); });
    const editorRef = { current: null } as React.RefObject<any>;
    setup({ editorRef });

    act(() => { useGlobalChatStore.getState().sendResourceToChat(pending()); });
    expect(insertResourceRef).not.toHaveBeenCalled();
    // The consume that just happened re-runs the effect; the retry frame
    // must survive that, or the asset is dropped rather than awaited.
    expect(frames.size).toBe(1);

    editorRef.current = fakeEditor();
    act(() => { [...frames.values()][0]?.(0); });

    expect(insertResourceRef).toHaveBeenCalledTimes(1);
    rafSpy.mockRestore();
    cafSpy.mockRestore();
  });

  it('cancels its pending retry when the panel unmounts', () => {
    const frames = new Map<number, FrameRequestCallback>();
    let nextHandle = 1;
    const rafSpy = vi
      .spyOn(window, 'requestAnimationFrame')
      .mockImplementation((cb: FrameRequestCallback) => {
        const handle = nextHandle++;
        frames.set(handle, cb);
        return handle;
      });
    const cafSpy = vi
      .spyOn(window, 'cancelAnimationFrame')
      .mockImplementation((handle: number) => { frames.delete(handle); });
    const editorRef = { current: null } as React.RefObject<any>;
    const { view } = setup({ editorRef });

    act(() => { useGlobalChatStore.getState().sendResourceToChat(pending()); });
    expect(frames.size).toBe(1);

    view.unmount();

    expect(frames.size).toBe(0);
    rafSpy.mockRestore();
    cafSpy.mockRestore();
  });

  it('fires again for a repeat send of the same resource', () => {
    setup();

    act(() => { useGlobalChatStore.getState().sendResourceToChat(pending()); });
    act(() => { useGlobalChatStore.getState().sendResourceToChat(pending()); });

    expect(insertResourceRef).toHaveBeenCalledTimes(2);
  });
});

describe('attachResource (@ picker path)', () => {
  it('inserts the chip and tops up whatever processing the asset is missing', async () => {
    ensureMock.mockResolvedValue({ action: 'triggered_transcribe', pointsCharged: 5 });
    const { view } = setup();

    await act(async () => {
      view.result.current.attachResource({
        id: 'r-9', name: 'talk.mp4', kind: 'video', mime: 'video/mp4',
        transcript_status: 'none', summary_status: 'none',
      });
    });

    expect(insertResourceRef).toHaveBeenCalledTimes(1);
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
