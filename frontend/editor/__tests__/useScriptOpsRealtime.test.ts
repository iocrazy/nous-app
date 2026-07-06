import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useScriptOpsRealtime } from '../collab/useScriptOpsRealtime';
import type { RemoteOpRow } from '../useSceneSync';
import { getSupabaseClient } from '../../supabaseClient';

vi.mock('../../supabaseClient', () => ({ getSupabaseClient: vi.fn() }));

function buildFakes() {
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
  const fakeSupabase = {
    channel: vi.fn(() => fakeChannel),
    removeChannel: vi.fn(),
  };
  return {
    fakeChannel,
    fakeSupabase,
    onCalls,
    emitInsert: (record: Record<string, unknown>) => changeHandler?.({ new: record }),
    fireSubscribed: () => subscribeCb?.('SUBSCRIBED'),
  };
}

describe('useScriptOpsRealtime', () => {
  let f: ReturnType<typeof buildFakes>;
  let getSceneIds: ReturnType<typeof vi.fn<() => string[]>>;
  let dispatchToScene: ReturnType<typeof vi.fn<(sceneId: string, row: RemoteOpRow) => void>>;
  let onReconcile: ReturnType<typeof vi.fn<() => void>>;

  beforeEach(() => {
    f = buildFakes();
    getSceneIds = vi.fn<() => string[]>(() => ['100', '200']);
    dispatchToScene = vi.fn<(sceneId: string, row: RemoteOpRow) => void>();
    onReconcile = vi.fn<() => void>();
    vi.mocked(getSupabaseClient).mockReturnValue(
      f.fakeSupabase as unknown as ReturnType<typeof getSupabaseClient>,
    );
  });
  afterEach(() => vi.restoreAllMocks());

  const handlers = () => ({ getSceneIds, dispatchToScene, onReconcile });

  it('scriptId=null → opens no channel', () => {
    renderHook(() => useScriptOpsRealtime(null, handlers()));
    expect(f.fakeSupabase.channel).not.toHaveBeenCalled();
  });

  it('opens `script-ops-<id>` and binds postgres_changes before subscribe', () => {
    renderHook(() => useScriptOpsRealtime('sc1', handlers()));
    expect(f.fakeSupabase.channel).toHaveBeenCalledWith('script-ops-sc1');
    expect(f.onCalls).toEqual(['postgres_changes']);
    const onOrder = (f.fakeChannel.on as ReturnType<typeof vi.fn>).mock.invocationCallOrder[0];
    const subOrder = (f.fakeChannel.subscribe as ReturnType<typeof vi.fn>).mock.invocationCallOrder[0];
    expect(onOrder).toBeLessThan(subOrder);
  });

  it('dispatches an INSERT for a scene in the open set, normalising the row', () => {
    renderHook(() => useScriptOpsRealtime('sc1', handlers()));
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

  it('drops an INSERT whose scene_id is not in the open script', () => {
    renderHook(() => useScriptOpsRealtime('sc1', handlers()));
    act(() => f.emitInsert({ scene_id: 777, op_seq: 2, actor: 'x', op_json: { ops: [] } }));
    expect(dispatchToScene).not.toHaveBeenCalled();
  });

  it('triggers a full reconcile on SUBSCRIBED', () => {
    renderHook(() => useScriptOpsRealtime('sc1', handlers()));
    act(() => f.fireSubscribed());
    expect(onReconcile).toHaveBeenCalledTimes(1);
  });

  it('removes the channel on unmount', () => {
    const { unmount } = renderHook(() => useScriptOpsRealtime('sc1', handlers()));
    act(() => unmount());
    expect(f.fakeSupabase.removeChannel).toHaveBeenCalledWith(f.fakeChannel);
  });

  it('reads the freshest scene set on each event (no re-subscribe needed)', () => {
    getSceneIds.mockReturnValue(['100']);
    renderHook(() => useScriptOpsRealtime('sc1', handlers()));
    // Scene set grows after mount; the ref-based handler must see it.
    getSceneIds.mockReturnValue(['100', '300']);
    act(() => f.emitInsert({ scene_id: 300, op_seq: 2, actor: 'x', op_json: { ops: [] } }));
    expect(dispatchToScene).toHaveBeenCalledWith('300', expect.objectContaining({ scene_id: '300' }));
  });
});
