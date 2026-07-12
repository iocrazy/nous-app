// features/canvas-core/smart/nodes/TimelineNodeView.minimal.test.tsx
// Timeline minimal set (P2-1): drag-resize a segment's edge, drag-reorder
// blocks, per-segment thumbnails (tail frames), live "Segment i/N"
// progress, and the failed-segment mark.

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));
const startTimelineRun = vi.fn();
vi.mock('../timelineRun', () => ({
  startTimelineRun: (...a: unknown[]) => startTimelineRun(...a),
  useTimelineRunStore: (sel: (s: { running: Record<string, boolean> }) => unknown) =>
    sel({ running: {} }),
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { TimelineNodeView } from './TimelineNodeView';

const baseProps = {
  selected: false, dragging: false, zIndex: 0, isConnectable: true,
  positionAbsoluteX: 0, positionAbsoluteY: 0, deletable: true,
  draggable: true, selectable: true,
} as const;

const SEGMENTS = [
  { id: 's1', prompt: 'opening', seconds: 5 },
  { id: 's2', prompt: 'waves', seconds: 3 },
  { id: 's3', prompt: 'sunset', seconds: 2 },
];

function renderTimeline(extra: Record<string, unknown> = {}) {
  const data = { segments: SEGMENTS, model: '', aspect: '16:9', run_status: 'idle', ...extra };
  useCanvasCoreStore.setState({
    kind: 'smart', canvasId: '9',
    nodes: [{ id: 'tl1', type: 'timeline', position: { x: 0, y: 0 }, data }] as never,
    connections: [], selection: [],
  });
  render(
    <ReactFlowProvider>
      <TimelineNodeView {...baseProps} id="tl1" type="timeline" data={data} />
    </ReactFlowProvider>,
  );
}

function storeSegments(): Array<{ id: string; seconds: number }> {
  const node = useCanvasCoreStore
    .getState()
    .nodes.find((n) => (n as Record<string, unknown>).id === 'tl1');
  return ((node as Record<string, unknown>).data as { segments: Array<{ id: string; seconds: number }> })
    .segments;
}

/** Pin the strip to 200px wide (total 10s → 20px/s). */
function mockStripRect() {
  const strip = screen.getByTestId('timeline-strip');
  vi.spyOn(strip, 'getBoundingClientRect').mockReturnValue({
    left: 0, top: 0, right: 200, bottom: 56, width: 200, height: 56,
    x: 0, y: 0, toJSON: () => ({}),
  } as DOMRect);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useCanvasCoreStore.getState().reset();
});

describe('segment edge resize (P2-1)', () => {
  it('dragging the right edge changes the segment seconds', () => {
    renderTimeline();
    mockStripRect();
    const handle = screen.getByTestId('timeline-resize-s1');
    // 20px per second; +40px → 5s becomes 7s.
    fireEvent.pointerDown(handle, { pointerId: 1, clientX: 100, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 1, clientX: 140 });
    fireEvent.pointerUp(handle, { pointerId: 1 });
    expect(storeSegments().find((s) => s.id === 's1')?.seconds).toBe(7);
  });

  it('resize clamps to the 1..10 envelope', () => {
    renderTimeline();
    mockStripRect();
    const handle = screen.getByTestId('timeline-resize-s2');
    fireEvent.pointerDown(handle, { pointerId: 1, clientX: 160, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 1, clientX: -500 });
    fireEvent.pointerUp(handle, { pointerId: 1 });
    expect(storeSegments().find((s) => s.id === 's2')?.seconds).toBe(1);
  });
});

describe('segment drag reorder (P2-1)', () => {
  it('dragging a block over a sibling reorders the segments', () => {
    renderTimeline();
    mockStripRect();
    const block = screen.getByTestId('timeline-seg-s1');
    // s1 spans [0,100); drag its centre to 180 (inside s3) → order b,c,a.
    fireEvent.pointerDown(block, { pointerId: 1, clientX: 50, button: 0 });
    fireEvent.pointerMove(block, { pointerId: 1, clientX: 180 });
    fireEvent.pointerUp(block, { pointerId: 1, clientX: 180 });
    expect(storeSegments().map((s) => s.id)).toEqual(['s2', 's3', 's1']);
  });

  it('a plain click (no movement) still selects for editing', () => {
    renderTimeline();
    mockStripRect();
    const block = screen.getByTestId('timeline-seg-s2');
    fireEvent.pointerDown(block, { pointerId: 1, clientX: 130, button: 0 });
    fireEvent.pointerUp(block, { pointerId: 1, clientX: 130 });
    fireEvent.click(block);
    expect(screen.getByText('Segment 2')).toBeTruthy();
    expect(storeSegments().map((s) => s.id)).toEqual(['s1', 's2', 's3']);
  });
});

describe('progress + thumbnails + failed mark (P2-1)', () => {
  it('shows "Segment i/N" while the run reports progress', () => {
    renderTimeline({ run_status: 'running', run_progress: { done: 1, total: 3 } });
    expect(screen.getByTestId('timeline-progress').textContent).toContain('Segment 2/3');
  });

  it('all segments done → stitching note', () => {
    renderTimeline({ run_status: 'running', run_progress: { done: 3, total: 3 } });
    expect(screen.getByTestId('timeline-progress').textContent).toMatch(/stitching/i);
  });

  it('renders tail-frame thumbnails inside their blocks', () => {
    renderTimeline({ segment_thumbs: ['/gm/1/cover', '/gm/2/cover'] });
    expect(screen.getByTestId('timeline-thumb-0')).toHaveProperty(
      'src',
      expect.stringContaining('/gm/1/cover') as unknown as string,
    );
    expect(screen.queryByTestId('timeline-thumb-2')).toBeNull();
  });

  it('marks the failed segment', () => {
    renderTimeline({ run_status: 'failed', failed_index: 1 });
    expect(
      screen.getByTestId('timeline-seg-s2').getAttribute('data-failed'),
    ).toBe('true');
    expect(screen.getByTestId('timeline-seg-s1').getAttribute('data-failed')).toBeNull();
  });
});

describe('in-node Delete removes a segment (P3-B)', () => {
  it('Delete with a segment active removes it, not the whole node', () => {
    renderTimeline();
    const node = screen.getByTestId('smart-timeline-node');
    // s1 is active by default (first segment).
    fireEvent.keyDown(node, { key: 'Delete' });
    expect(storeSegments().map((s) => s.id)).toEqual(['s2', 's3']);
  });

  it('does not fire from inside the prompt textarea', () => {
    renderTimeline();
    const textarea = screen.getByLabelText('Segment prompt');
    fireEvent.keyDown(textarea, { key: 'Delete' });
    expect(storeSegments()).toHaveLength(3);
  });

  it('is a no-op with a single segment (keeps the node deletable)', () => {
    renderTimeline({ segments: [{ id: 'only', prompt: 'x', seconds: 5 }] });
    const node = screen.getByTestId('smart-timeline-node');
    fireEvent.keyDown(node, { key: 'Delete' });
    expect(storeSegments()).toHaveLength(1);
  });
});
