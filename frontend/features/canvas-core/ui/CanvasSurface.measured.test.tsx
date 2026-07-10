/**
 * Measured-dimensions round-trip (xyflow v12 contract).
 *
 * In controlled mode RF only knows a node's size if the consumer echoes the
 * `measured` field written by applyNodeChanges back into the `nodes` prop.
 * NodeWrapper renders `visibility: hidden` until nodeHasDimensions(node) —
 * stripping `measured` in the store→RF mapping leaves every canvas node
 * permanently invisible in a real browser (jsdom never caught it because the
 * prop-capturing stub bypasses NodeWrapper).
 */

import { render, cleanup, act } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));

let capturedProps: Record<string, unknown> = {};
vi.mock('@xyflow/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@xyflow/react')>();
  return {
    ...actual,
    ReactFlow: (props: Record<string, unknown>) => {
      capturedProps = props;
      return null;
    },
  };
});

import { CanvasSurface } from './CanvasSurface';
import { useCanvasCoreStore } from '../store/canvasCoreStore';

afterEach(() => {
  cleanup();
  capturedProps = {};
  useCanvasCoreStore.getState().reset();
});

describe('CanvasSurface measured round-trip', () => {
  it('echoes RF dimension measurements back into the nodes prop', () => {
    useCanvasCoreStore.setState({
      kind: 'smart',
      nodes: [{ id: 'shot1', type: 'shot', position: { x: 0, y: 0 }, data: {} }],
      connections: [],
      selection: [],
    });
    render(<CanvasSurface />);

    act(() => {
      (capturedProps.onNodesChange as (c: unknown[]) => void)?.([
        {
          id: 'shot1',
          type: 'dimensions',
          dimensions: { width: 240, height: 143 },
          // RF emits setAttributes on the initial measure pass.
          setAttributes: true,
        },
      ]);
    });

    const rfNodes = capturedProps.nodes as Array<Record<string, unknown>>;
    const shot = rfNodes.find((n) => n.id === 'shot1')!;
    expect(shot.measured).toEqual({ width: 240, height: 143 });
  });
});
