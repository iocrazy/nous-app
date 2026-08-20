// η2: ruler seek + trim handles on the selected clip (IC playhead/trim).
import { fireEvent, render, screen } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { TimelineNodeView } from './TimelineNodeView';

vi.mock('../timelineRun', () => ({
  startTimelineRun: vi.fn(),
  useTimelineRunStore: (sel: (s: { running: Record<string, boolean> }) => unknown) =>
    sel({ running: {} }),
}));
vi.mock('../clipRun', () => ({ runSegmentClip: vi.fn() }));

function seed() {
  useCanvasCoreStore.setState({
    canvasId: 'c1',
    nodes: [
      {
        id: 't1',
        type: 'timeline',
        position: { x: 0, y: 0 },
        data: {
          segments: [
            { id: 's1', prompt: 'a', seconds: 5, result_url: '/api/v1/generated-media/1/stream' },
            { id: 's2', prompt: 'b', seconds: 5 },
          ],
        },
      },
    ] as never,
    connections: [] as never,
  });
}

const props = () =>
  ({
    id: 't1',
    data: (useCanvasCoreStore.getState().nodes[0] as { data: unknown }).data,
    selected: false,
  }) as unknown as Parameters<typeof TimelineNodeView>[0];

describe('TimelineNodeView η2 ruler + trim', () => {
  beforeEach(seed);

  it('renders the ruler with a playhead and seeks on click', () => {
    render(<ReactFlowProvider><TimelineNodeView {...props()} /></ReactFlowProvider>);
    const ruler = screen.getByTestId('timeline-ruler');
    expect(screen.getByTestId('timeline-playhead')).toBeInTheDocument();
    // jsdom rects are 0×0 — pin a real box so the click maps to seconds.
    ruler.getBoundingClientRect = () =>
      ({ left: 0, width: 100, top: 0, height: 16, right: 100, bottom: 16, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
    fireEvent.click(ruler, { clientX: 10 }); // 10% of 10s → 1s → clip s1
    expect(screen.getByTestId('timeline-seg-s1').className).toContain('border-canvas-strong');
  });

  it('shows trim handles only on the selected clip WITH a result', () => {
    render(<ReactFlowProvider><TimelineNodeView {...props()} /></ReactFlowProvider>);
    fireEvent.click(screen.getByTestId('timeline-seg-s1'));
    expect(screen.getByTestId('timeline-trim-in-s1')).toBeInTheDocument();
    expect(screen.getByTestId('timeline-trim-out-s1')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('timeline-seg-s2'));
    expect(screen.queryByTestId('timeline-trim-in-s2')).toBeNull();
  });
});
