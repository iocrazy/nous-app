// features/canvas-core/smart/nodes/OutputNodeView.aspect.test.tsx
//
// Zero layout jump (canvas fluency Task 7). A generation slot changes shape
// three times on the way to a result — empty node, N shimmer cells, then N
// images whose intrinsic size only becomes known once the bytes decode. Each
// change reflowed the card and shoved every node below it, which is the jump
// users read as "the canvas is stuttering".
//
// The fix is to give every cell an INTRINSIC box before anything loads: the
// requested ratio is stamped onto the slot at dispatch (`gen_ratio`, see
// genSlots.beginGenerationSlot) and both the shimmer cell and the landed
// <img> take their `aspect-ratio` from it, so the image lands INTO the box
// the placeholder already reserved. A slot with no knowable ratio falls back
// to square rather than to "whatever the first image happens to be".

import { ReactFlowProvider } from '@xyflow/react';
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { OutputNodeView } from './OutputNodeView';

const baseProps = {
  selected: false,
  dragging: false,
  zIndex: 0,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  deletable: true,
  draggable: true,
  selectable: true,
} as const;

function renderOutput(data: Record<string, unknown>) {
  return render(
    <ReactFlowProvider>
      <OutputNodeView
        {...baseProps}
        id="out1"
        type="output"
        data={{
          kind: 'image',
          resource_id: null,
          preview_text: '',
          preview_url: null,
          crop_region: null,
          ...data,
        }}
      />
    </ReactFlowProvider>,
  );
}

afterEach(() => useCanvasCoreStore.getState().reset());

const IMG = { url: '/api/v1/generated-media/1/cover', kind: 'image' };

describe('OutputNodeView — intrinsic cell size (Task 7)', () => {
  it('shimmer cells and landed images share the aspect-ratio of the requested ratio', () => {
    renderOutput({ gen_ratio: '16:9', gen_pending: 1, images: [IMG] });
    const cells = screen.getAllByTestId('output-cell');
    // One landed image + one still-pending cell — the mixed state is exactly
    // where a jump would show, so both must already agree on the box.
    expect(cells).toHaveLength(2);
    for (const cell of cells) expect(cell.style.aspectRatio).toBe('16 / 9');
  });

  it('falls back to 1 / 1 when no ratio is known', () => {
    renderOutput({ gen_pending: 2, images: [] });
    const cells = screen.getAllByTestId('output-cell');
    expect(cells).toHaveLength(2);
    for (const cell of cells) expect(cell.style.aspectRatio).toBe('1 / 1');
  });

  it("treats 'auto' as unknown — the followed source is not knowable here", () => {
    renderOutput({ gen_ratio: 'auto', gen_pending: 1, images: [] });
    expect(screen.getByTestId('output-cell').style.aspectRatio).toBe('1 / 1');
  });

  it('falls back to 1 / 1 on an unparseable ratio rather than emitting bad CSS', () => {
    renderOutput({ gen_ratio: 'wide-ish', gen_pending: 1, images: [] });
    expect(screen.getByTestId('output-cell').style.aspectRatio).toBe('1 / 1');
  });

  it('the single-preview layout carries the same box as the grid', () => {
    // One image and nothing pending renders the legacy single-preview
    // branch — it needs the reserved box just as much, because that is the
    // shape a regenerate re-enters from.
    renderOutput({ gen_ratio: '3:4', images: [IMG], preview_url: IMG.url });
    const cells = screen.getAllByTestId('output-cell');
    expect(cells).toHaveLength(1);
    expect(cells[0].style.aspectRatio).toBe('3 / 4');
  });

  it('images are lazy and async-decoded', () => {
    renderOutput({ gen_ratio: '1:1', images: [IMG, { ...IMG, url: '/api/v1/generated-media/2/cover' }] });
    const imgs = screen.getAllByRole('img');
    expect(imgs).toHaveLength(2);
    for (const img of imgs) {
      expect(img.getAttribute('loading')).toBe('lazy');
      expect(img.getAttribute('decoding')).toBe('async');
    }
  });

  it('the single-preview image is lazy and async-decoded too', () => {
    renderOutput({ images: [IMG], preview_url: IMG.url });
    const img = screen.getByRole('img');
    expect(img.getAttribute('loading')).toBe('lazy');
    expect(img.getAttribute('decoding')).toBe('async');
  });
});
