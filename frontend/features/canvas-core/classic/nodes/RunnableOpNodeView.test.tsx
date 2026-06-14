import { ReactFlowProvider } from '@xyflow/react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import {
  beginAbortable,
  clearAllAbortControllers,
  hasAbortController,
} from '../abortRegistry';
import { CLASSIC_NODE_TYPES } from '../ClassicNodeViews';

const ComfyNodeView = CLASSIC_NODE_TYPES.comfy;

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: '4242',
    kind: 'classic',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-06-14T12:00:00+00:00',
  });
});

afterEach(() => {
  clearAllAbortControllers();
  useCanvasCoreStore.getState().reset();
});

function Wrap({ children }: { children: ReactNode }) {
  return <ReactFlowProvider>{children}</ReactFlowProvider>;
}

const baseProps = {
  type: 'comfy',
  dragHandle: undefined,
  draggable: true,
  selectable: true,
  deletable: true,
  selected: false,
  dragging: false,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  width: 170,
  height: 100,
  zIndex: 0,
} as const;

function seedComfy(id: string, data: Record<string, unknown>) {
  const fullData = {
    run_status: 'idle',
    run_started_at: null,
    run_error: null,
    ...data,
  };
  useCanvasCoreStore.setState({
    nodes: [{ id, type: 'comfy', data: fullData, position: { x: 0, y: 0 } }],
  });
  return fullData;
}

describe('ComfyNodeView — elapsed timer', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-06-14T00:00:00.000Z'));
  });
  afterEach(() => vi.useRealTimers());

  it('ticks increasing elapsed seconds while running', () => {
    const data = seedComfy('c1', {
      run_status: 'running',
      run_started_at: '2026-06-14T00:00:00.000Z',
    });
    render(
      <Wrap>
        <ComfyNodeView {...baseProps} id="c1" type="comfy" data={data} />
      </Wrap>,
    );
    expect(screen.getByTestId('comfy-elapsed')).toHaveTextContent('0s');

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByTestId('comfy-elapsed')).toHaveTextContent('3s');

    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.getByTestId('comfy-elapsed')).toHaveTextContent('1m 03s');
  });

  it('does not render the timer when not running (stopped)', () => {
    const data = seedComfy('c1', {
      run_status: 'succeeded',
      run_started_at: '2026-06-14T00:00:00.000Z',
    });
    render(
      <Wrap>
        <ComfyNodeView {...baseProps} id="c1" type="comfy" data={data} />
      </Wrap>,
    );
    expect(screen.queryByTestId('comfy-elapsed')).not.toBeInTheDocument();
    // advancing time changes nothing — no live counter while stopped.
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.queryByTestId('comfy-elapsed')).not.toBeInTheDocument();
  });
});

describe('ComfyNodeView — cancel', () => {
  it('Cancel aborts the in-flight request and flips the node to idle', () => {
    const data = seedComfy('c1', {
      run_status: 'running',
      run_started_at: '2026-06-14T00:00:00.000Z',
    });
    const controller = beginAbortable('c1');
    render(
      <Wrap>
        <ComfyNodeView {...baseProps} id="c1" type="comfy" data={data} />
      </Wrap>,
    );

    fireEvent.click(screen.getByTestId('comfy-cancel'));

    expect(controller.signal.aborted).toBe(true);
    expect(hasAbortController('c1')).toBe(false);
    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    expect(node.data.run_status).toBe('idle');
    expect(node.data.run_started_at).toBeNull();
  });

  it('Cancel is hidden when the node is not running', () => {
    const data = seedComfy('c1', { run_status: 'idle' });
    render(
      <Wrap>
        <ComfyNodeView {...baseProps} id="c1" type="comfy" data={data} />
      </Wrap>,
    );
    expect(screen.queryByTestId('comfy-cancel')).not.toBeInTheDocument();
  });
});

// ---- image_gen / video_gen share the same runnable-op view --------------

const ImageGenNodeView = CLASSIC_NODE_TYPES.image_gen;
const VideoGenNodeView = CLASSIC_NODE_TYPES.video_gen;

function seedRunnable(id: string, type: string, data: Record<string, unknown>) {
  const fullData = {
    run_status: 'idle',
    run_started_at: null,
    run_error: null,
    ...data,
  };
  useCanvasCoreStore.setState({
    nodes: [{ id, type, data: fullData, position: { x: 0, y: 0 } }],
  });
  return fullData;
}

describe('image_gen / video_gen — runnable-op elapsed + cancel', () => {
  const cases = [
    { View: ImageGenNodeView, type: 'image_gen', prefix: 'image-gen' },
    { View: VideoGenNodeView, type: 'video_gen', prefix: 'video-gen' },
  ] as const;

  for (const { View, type, prefix } of cases) {
    it(`${type}: shows live elapsed + Cancel while running`, () => {
      const data = seedRunnable('r1', type, {
        run_status: 'running',
        run_started_at: '2026-06-14T00:00:00.000Z',
      });
      render(
        <Wrap>
          <View {...baseProps} id="r1" type={type} data={data} />
        </Wrap>,
      );
      expect(screen.getByTestId(`${prefix}-elapsed`)).toBeInTheDocument();
      expect(screen.getByTestId(`${prefix}-cancel`)).toBeInTheDocument();
    });

    it(`${type}: Cancel aborts the in-flight request and flips to idle`, () => {
      const data = seedRunnable('r1', type, {
        run_status: 'running',
        run_started_at: '2026-06-14T00:00:00.000Z',
      });
      const controller = beginAbortable('r1');
      render(
        <Wrap>
          <View {...baseProps} id="r1" type={type} data={data} />
        </Wrap>,
      );
      fireEvent.click(screen.getByTestId(`${prefix}-cancel`));
      expect(controller.signal.aborted).toBe(true);
      expect(hasAbortController('r1')).toBe(false);
      const node = useCanvasCoreStore.getState().nodes[0] as Record<
        string,
        Record<string, unknown>
      >;
      expect(node.data.run_status).toBe('idle');
    });
  }

  it('image_gen renders an inline result thumbnail on success', () => {
    const data = seedRunnable('r1', 'image_gen', {
      run_status: 'succeeded',
      run_result: { image_url: 'https://x/out.png' },
    });
    render(
      <Wrap>
        <ImageGenNodeView {...baseProps} id="r1" type="image_gen" data={data} />
      </Wrap>,
    );
    const frame = screen.getByTestId('image-gen-result');
    expect(frame).toBeInTheDocument();
    expect(frame.querySelector('img')?.getAttribute('src')).toBe('https://x/out.png');
  });

  it('video_gen prefers thumbnail_url for its result preview', () => {
    const data = seedRunnable('r1', 'video_gen', {
      run_status: 'succeeded',
      run_result: { video_url: 'https://x/clip.mp4', thumbnail_url: 'https://x/thumb.jpg' },
    });
    render(
      <Wrap>
        <VideoGenNodeView {...baseProps} id="r1" type="video_gen" data={data} />
      </Wrap>,
    );
    expect(screen.getByTestId('video-gen-result').querySelector('img')?.getAttribute('src')).toBe(
      'https://x/thumb.jpg',
    );
  });

  it('no result frame before success (idle)', () => {
    const data = seedRunnable('r1', 'image_gen', {
      run_status: 'idle',
      run_result: { image_url: 'https://x/out.png' },
    });
    render(
      <Wrap>
        <ImageGenNodeView {...baseProps} id="r1" type="image_gen" data={data} />
      </Wrap>,
    );
    expect(screen.queryByTestId('image-gen-result')).not.toBeInTheDocument();
  });
});
