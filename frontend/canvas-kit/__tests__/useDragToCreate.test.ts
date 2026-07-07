/**
 * useDragToCreate — drag-to-create branch logic (canvas-kit).
 */
import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useDragToCreate, type UseDragToCreateOptions } from '../useDragToCreate';
import type { SnapPort } from '../portSnap';

const PORTS: SnapPort[] = [{ id: 'in', nodeId: 'target', x: 100, y: 100 }];

function setup(over: Partial<UseDragToCreateOptions> = {}) {
  const onMagneticConnect = vi.fn();
  const onOpenCreateMenu = vi.fn();
  const getPorts = vi.fn(() => PORTS);
  const { result } = renderHook(() =>
    useDragToCreate({ getPorts, onMagneticConnect, onOpenCreateMenu, ...over }),
  );
  return { result, onMagneticConnect, onOpenCreateMenu, getPorts };
}

const startSource = (h: { onConnectStart: unknown }) =>
  (h.onConnectStart as (e: unknown, p: unknown) => void)(
    {},
    { nodeId: 'from', handleId: 'out', handleType: 'source' },
  );

const end = (h: { onConnectEnd: unknown }, state: unknown) =>
  (h.onConnectEnd as (e: unknown, s: unknown) => void)({}, state);

afterEach(() => vi.clearAllMocks());

describe('useDragToCreate', () => {
  it('opens the create menu when released in empty canvas', () => {
    const { result, onOpenCreateMenu, onMagneticConnect } = setup();
    startSource(result.current);
    end(result.current, { isValid: false, to: { x: 500, y: 500 } });
    expect(onMagneticConnect).not.toHaveBeenCalled();
    expect(onOpenCreateMenu).toHaveBeenCalledWith({
      flowPosition: { x: 500, y: 500 },
      fromNodeId: 'from',
      fromHandle: 'out',
    });
  });

  it('magnetically connects when released near a candidate port', () => {
    const { result, onMagneticConnect, onOpenCreateMenu } = setup();
    startSource(result.current);
    // Release 10px from the port at (100,100).
    end(result.current, { isValid: false, to: { x: 108, y: 100 } });
    expect(onOpenCreateMenu).not.toHaveBeenCalled();
    expect(onMagneticConnect).toHaveBeenCalledWith({
      fromNodeId: 'from',
      fromHandle: 'out',
      toNodeId: 'target',
      toHandle: 'in',
    });
  });

  it('does nothing when xyflow already judged the drop valid', () => {
    const { result, onMagneticConnect, onOpenCreateMenu } = setup();
    startSource(result.current);
    end(result.current, { isValid: true, to: { x: 500, y: 500 } });
    expect(onMagneticConnect).not.toHaveBeenCalled();
    expect(onOpenCreateMenu).not.toHaveBeenCalled();
  });

  it('cancels on Escape mid-drag', () => {
    const { result, onMagneticConnect, onOpenCreateMenu } = setup();
    startSource(result.current);
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    end(result.current, { isValid: false, to: { x: 500, y: 500 } });
    expect(onMagneticConnect).not.toHaveBeenCalled();
    expect(onOpenCreateMenu).not.toHaveBeenCalled();
  });

  it('ignores a drag that starts from a target (input) handle', () => {
    const { result, onMagneticConnect, onOpenCreateMenu } = setup();
    (result.current.onConnectStart as (e: unknown, p: unknown) => void)(
      {},
      { nodeId: 'from', handleId: 'in', handleType: 'target' },
    );
    end(result.current, { isValid: false, to: { x: 500, y: 500 } });
    expect(onMagneticConnect).not.toHaveBeenCalled();
    expect(onOpenCreateMenu).not.toHaveBeenCalled();
  });
});
