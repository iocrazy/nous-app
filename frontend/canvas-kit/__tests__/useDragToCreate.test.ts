/**
 * useDragToCreate — drag-to-create branch logic (canvas-kit).
 *
 * The release point is taken from the raw pointer event's client coords and run
 * through `toFlowPosition`; it must NOT use `connectionState.to`, which is
 * screen-space on the invalid branch (the regression these tests pin).
 */
import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useDragToCreate, type UseDragToCreateOptions } from '../useDragToCreate';
import type { SnapPort } from '../portSnap';

const PORTS: SnapPort[] = [{ id: 'in', nodeId: 'target', x: 100, y: 100 }];

// Simulate a non-identity viewport: screen → flow subtracts a 50px pan. Any
// code that wrongly used the raw screen point would land 50px off.
const PAN = 50;
const toFlowPosition = (p: { x: number; y: number }) => ({ x: p.x - PAN, y: p.y - PAN });

function setup(over: Partial<UseDragToCreateOptions> = {}) {
  const onMagneticConnect = vi.fn();
  const onOpenCreateMenu = vi.fn();
  const getPorts = vi.fn(() => PORTS);
  const toFlow = vi.fn(toFlowPosition);
  const { result } = renderHook(() =>
    useDragToCreate({
      getPorts,
      toFlowPosition: toFlow,
      onMagneticConnect,
      onOpenCreateMenu,
      ...over,
    }),
  );
  return { result, onMagneticConnect, onOpenCreateMenu, getPorts, toFlow };
}

const startSource = (h: { onConnectStart: unknown }) =>
  (h.onConnectStart as (e: unknown, p: unknown) => void)(
    {},
    { nodeId: 'from', handleId: 'out', handleType: 'source' },
  );

// End the drag at a given SCREEN (client) point. `state.to` is deliberately set
// to a wrong value to prove the hook ignores it.
const endAt = (
  h: { onConnectEnd: unknown },
  client: { x: number; y: number },
  isValid = false,
) =>
  (h.onConnectEnd as (e: unknown, s: unknown) => void)(
    { clientX: client.x, clientY: client.y },
    { isValid, to: { x: -9999, y: -9999 } },
  );

afterEach(() => vi.clearAllMocks());

describe('useDragToCreate', () => {
  it('converts the release point through toFlowPosition (ignores screen-space to)', () => {
    const { result, onOpenCreateMenu, toFlow } = setup();
    startSource(result.current);
    // Drop far from any port, at screen (500,500).
    endAt(result.current, { x: 500, y: 500 });
    expect(toFlow).toHaveBeenCalledWith({ x: 500, y: 500 });
    expect(onOpenCreateMenu).toHaveBeenCalledWith({
      flowPosition: { x: 450, y: 450 }, // 500 − PAN, NOT state.to (−9999)
      fromNodeId: 'from',
      fromHandle: 'out',
    });
  });

  it('magnetically connects when the converted point is near a candidate port', () => {
    const { result, onMagneticConnect, onOpenCreateMenu } = setup();
    startSource(result.current);
    // Screen (158,150) → flow (108,100) = 8px from the port at (100,100).
    endAt(result.current, { x: 158, y: 150 });
    expect(onOpenCreateMenu).not.toHaveBeenCalled();
    expect(onMagneticConnect).toHaveBeenCalledWith({
      fromNodeId: 'from',
      fromHandle: 'out',
      toNodeId: 'target',
      toHandle: 'in',
    });
  });

  it('excludes the origin node from candidates (no self-loop)', () => {
    // Port belongs to the SAME node the wire started from.
    const selfPorts: SnapPort[] = [{ id: 'in', nodeId: 'from', x: 100, y: 100 }];
    const { result, onMagneticConnect, onOpenCreateMenu } = setup({
      getPorts: () => selfPorts,
    });
    startSource(result.current);
    endAt(result.current, { x: 158, y: 150 }); // would hit if not excluded
    expect(onMagneticConnect).not.toHaveBeenCalled();
    expect(onOpenCreateMenu).toHaveBeenCalled(); // falls through to menu
  });

  it('falls through to the create menu when the snap is not a valid target', () => {
    const { result, onMagneticConnect, onOpenCreateMenu } = setup({
      isValidTarget: () => false,
    });
    startSource(result.current);
    endAt(result.current, { x: 158, y: 150 }); // near the port, but illegal
    expect(onMagneticConnect).not.toHaveBeenCalled();
    expect(onOpenCreateMenu).toHaveBeenCalledWith({
      flowPosition: { x: 108, y: 100 },
      fromNodeId: 'from',
      fromHandle: 'out',
    });
  });

  it('connects when isValidTarget approves the snap', () => {
    const isValidTarget = vi.fn(() => true);
    const { result, onMagneticConnect } = setup({ isValidTarget });
    startSource(result.current);
    endAt(result.current, { x: 158, y: 150 });
    expect(isValidTarget).toHaveBeenCalledWith({
      fromNodeId: 'from',
      fromHandle: 'out',
      toNodeId: 'target',
      toHandle: 'in',
    });
    expect(onMagneticConnect).toHaveBeenCalled();
  });

  it('does nothing when xyflow already judged the drop valid', () => {
    const { result, onMagneticConnect, onOpenCreateMenu } = setup();
    startSource(result.current);
    endAt(result.current, { x: 500, y: 500 }, /* isValid */ true);
    expect(onMagneticConnect).not.toHaveBeenCalled();
    expect(onOpenCreateMenu).not.toHaveBeenCalled();
  });

  it('cancels on Escape mid-drag', () => {
    const { result, onMagneticConnect, onOpenCreateMenu } = setup();
    startSource(result.current);
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    endAt(result.current, { x: 500, y: 500 });
    expect(onMagneticConnect).not.toHaveBeenCalled();
    expect(onOpenCreateMenu).not.toHaveBeenCalled();
  });

  it('ignores a drag that starts from a target (input) handle', () => {
    const { result, onMagneticConnect, onOpenCreateMenu } = setup();
    (result.current.onConnectStart as (e: unknown, p: unknown) => void)(
      {},
      { nodeId: 'from', handleId: 'in', handleType: 'target' },
    );
    endAt(result.current, { x: 500, y: 500 });
    expect(onMagneticConnect).not.toHaveBeenCalled();
    expect(onOpenCreateMenu).not.toHaveBeenCalled();
  });

  it('no-ops when the end event carries no pointer coordinates', () => {
    const { result, onOpenCreateMenu, onMagneticConnect } = setup();
    startSource(result.current);
    // e.g. a synthetic end with neither clientX nor touches.
    (result.current.onConnectEnd as (e: unknown, s: unknown) => void)(
      {},
      { isValid: false, to: { x: 1, y: 1 } },
    );
    expect(onOpenCreateMenu).not.toHaveBeenCalled();
    expect(onMagneticConnect).not.toHaveBeenCalled();
  });
});
