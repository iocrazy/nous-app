import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useScriptOpsRealtime } from '../collab/useScriptOpsRealtime';
import type { RemoteOpRow } from '../useSceneSync';
import { getSupabaseClient } from '../../supabaseClient';

vi.mock('../../supabaseClient', () => ({ getSupabaseClient: vi.fn() }));

// The subscribe path is async now (auth applied before join), so flush the
// getSession microtask(s) before asserting on the channel.
async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function buildFakes(config: { session?: { access_token: string } | null; defer?: boolean } = {}) {
  const session = 'session' in config ? config.session : { access_token: 'tok' };
  let changeHandler: ((payload: { new: Record<string, unknown> }) => void) | null = null;
  let subscribeCb: ((status: string) => void) | null = null;
  const onCalls: string[] = [];

  const fakeChannel: Record<string, unknown> = {
    on: vi.fn((type: string, _opts: unknown, handler: (p: { new: Record<string, unknown> }) => void) => {
      onCalls.push(type);
      changeHandler = handler;
      return fakeChannel;
    }),
    subscribe: vi.fn((cb: (status: string) => void) => {
      subscribeCb = cb;
      return fakeChannel;
    }),
  };

  let resolveSession: (() => void) | null = null;
  const sessionResult = { data: { session } };
  const getSession = vi.fn(() =>
    config.defer
      ? new Promise((res) => {
          resolveSession = () => res(sessionResult);
        })
      : Promise.resolve(sessionResult),
  );

  const fakeSupabase = {
    auth: { getSession },
    realtime: { setAuth: vi.fn() },
    channel: vi.fn(() => fakeChannel),
    removeChannel: vi.fn(),
  };
  return {
    fakeChannel,
    fakeSupabase,
    onCalls,
    emitInsert: (record: Record<string, unknown>) => changeHandler?.({ new: record }),
    fireSubscribed: () => subscribeCb?.('SUBSCRIBED'),
    resolveSession: () => resolveSession?.(),
  };
}

describe('useScriptOpsRealtime', () => {
  let f: ReturnType<typeof buildFakes>;
  let getSceneIds: ReturnType<typeof vi.fn<() => string[]>>;
  let dispatchToScene: ReturnType<typeof vi.fn<(sceneId: string, row: RemoteOpRow) => void>>;
  let onReconcile: ReturnType<typeof vi.fn<() => void>>;

  const wire = (fakes: ReturnType<typeof buildFakes>) => {
    f = fakes;
    vi.mocked(getSupabaseClient).mockReturnValue(
      f.fakeSupabase as unknown as ReturnType<typeof getSupabaseClient>,
    );
  };

  beforeEach(() => {
    getSceneIds = vi.fn<() => string[]>(() => ['100', '200']);
    dispatchToScene = vi.fn<(sceneId: string, row: RemoteOpRow) => void>();
    onReconcile = vi.fn<() => void>();
    wire(buildFakes());
  });
  afterEach(() => vi.restoreAllMocks());

  const handlers = () => ({ getSceneIds, dispatchToScene, onReconcile });

  it('scriptId=null → opens no channel', async () => {
    renderHook(() => useScriptOpsRealtime(null, handlers()));
    await flush();
    expect(f.fakeSupabase.auth.getSession).not.toHaveBeenCalled();
    expect(f.fakeSupabase.channel).not.toHaveBeenCalled();
  });

  it('applies realtime auth BEFORE opening the channel, and binds on() before subscribe', async () => {
    renderHook(() => useScriptOpsRealtime('sc1', handlers()));
    await flush();
    expect(f.fakeSupabase.channel).toHaveBeenCalledWith('script-ops-sc1');
    expect(f.fakeSupabase.realtime.setAuth).toHaveBeenCalledWith('tok');
    const setAuthOrder = f.fakeSupabase.realtime.setAuth.mock.invocationCallOrder[0];
    const channelOrder = f.fakeSupabase.channel.mock.invocationCallOrder[0];
    expect(setAuthOrder).toBeLessThan(channelOrder);
    expect(f.onCalls).toEqual(['postgres_changes']);
    const onOrder = (f.fakeChannel.on as ReturnType<typeof vi.fn>).mock.invocationCallOrder[0];
    const subOrder = (f.fakeChannel.subscribe as ReturnType<typeof vi.fn>).mock.invocationCallOrder[0];
    expect(onOrder).toBeLessThan(subOrder);
  });

  it('opens no channel when there is no session', async () => {
    wire(buildFakes({ session: null }));
    renderHook(() => useScriptOpsRealtime('sc1', handlers()));
    await flush();
    expect(f.fakeSupabase.realtime.setAuth).not.toHaveBeenCalled();
    expect(f.fakeSupabase.channel).not.toHaveBeenCalled();
  });

  it('does not subscribe when unmounted before getSession resolves (cancel guard)', async () => {
    wire(buildFakes({ defer: true }));
    const { unmount } = renderHook(() => useScriptOpsRealtime('sc1', handlers()));
    act(() => unmount()); // unmount BEFORE the session resolves
    f.resolveSession();
    await flush();
    expect(f.fakeSupabase.channel).not.toHaveBeenCalled();
    expect(f.fakeSupabase.removeChannel).not.toHaveBeenCalled();
  });

  it('dispatches an INSERT for a scene in the open set, normalising the row', async () => {
    renderHook(() => useScriptOpsRealtime('sc1', handlers()));
    await flush();
    act(() =>
      f.emitInsert({
        id: 999,
        scene_id: 100, // int8 arrives as a JS number
        op_seq: 5,
        actor: 'other-user',
        op_json: { ops: [{ op: 'update', element_id: 'e1', payload: { text: 'x' } }] },
      }),
    );
    expect(dispatchToScene).toHaveBeenCalledTimes(1);
    expect(dispatchToScene).toHaveBeenCalledWith('100', {
      scene_id: '100',
      op_seq: 5,
      actor: 'other-user',
      op_json: { ops: [{ op: 'update', element_id: 'e1', payload: { text: 'x' } }] },
    });
  });

  it('drops an INSERT whose scene_id is not in the open script', async () => {
    renderHook(() => useScriptOpsRealtime('sc1', handlers()));
    await flush();
    act(() => f.emitInsert({ scene_id: 777, op_seq: 2, actor: 'x', op_json: { ops: [] } }));
    expect(dispatchToScene).not.toHaveBeenCalled();
  });

  it('triggers a full reconcile on SUBSCRIBED', async () => {
    renderHook(() => useScriptOpsRealtime('sc1', handlers()));
    await flush();
    act(() => f.fireSubscribed());
    expect(onReconcile).toHaveBeenCalledTimes(1);
  });

  it('removes the channel on unmount', async () => {
    const { unmount } = renderHook(() => useScriptOpsRealtime('sc1', handlers()));
    await flush();
    act(() => unmount());
    expect(f.fakeSupabase.removeChannel).toHaveBeenCalledWith(f.fakeChannel);
  });

  it('reads the freshest scene set on each event (no re-subscribe needed)', async () => {
    getSceneIds.mockReturnValue(['100']);
    renderHook(() => useScriptOpsRealtime('sc1', handlers()));
    await flush();
    // Scene set grows after mount; the ref-based handler must see it.
    getSceneIds.mockReturnValue(['100', '300']);
    act(() => f.emitInsert({ scene_id: 300, op_seq: 2, actor: 'x', op_json: { ops: [] } }));
    expect(dispatchToScene).toHaveBeenCalledWith('300', expect.objectContaining({ scene_id: '300' }));
  });
});
