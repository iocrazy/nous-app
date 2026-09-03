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

describe('OutputNodeView — the reserved box is only for slots that have a ratio', () => {
  // The grid branch is the placeholder/landed pair: both cells must agree on
  // ONE box or the swap reflows, so it takes the square fallback when nothing
  // is known (and the pending cell was hard `aspect-square` before this work
  // anyway — the fallback takes nothing away).
  //
  // The single-preview and video branches are different. Most output nodes
  // that reach them are not generation slots at all and will never carry a
  // ratio: the history archive holding the previous batch, an extend/outpaint
  // result (whose whole point is a CHANGED aspect), upscale tiles, timeline
  // films, loop outputs, entity templates. Those images used to size to their
  // own shape, and imposing a square on them permanently letterboxes them —
  // most visibly the history archive, which renders the SAME images as the
  // live slot directly above it. So here the box is applied only when a ratio
  // is actually known.

  it('the grid still falls back to a square when no ratio is known', () => {
    renderOutput({ gen_pending: 1, images: [IMG] });
    for (const cell of screen.getAllByTestId('output-cell'))
      expect(cell.style.aspectRatio).toBe('1 / 1');
  });

  it('a single preview with no ratio imposes no box at all', () => {
    renderOutput({ images: [IMG], preview_url: IMG.url });
    expect(screen.getByTestId('output-cell').style.aspectRatio).toBe('');
  });

  it("a single preview whose ratio is 'auto' imposes no box either", () => {
    // 'auto' reaching the view means the dispatch could not resolve it, so
    // the shape is genuinely unknown — square would be a guess, not a
    // fallback with a placeholder to agree with.
    renderOutput({ gen_ratio: 'auto', images: [IMG], preview_url: IMG.url });
    expect(screen.getByTestId('output-cell').style.aspectRatio).toBe('');
  });

  it('a single preview with a known ratio takes the box', () => {
    renderOutput({ gen_ratio: '16:9', images: [IMG], preview_url: IMG.url });
    expect(screen.getByTestId('output-cell').style.aspectRatio).toBe('16 / 9');
  });

  it('a video preview with no ratio imposes no box', () => {
    renderOutput({ kind: 'video', preview_url: '/api/v1/generated-media/5/stream' });
    expect(screen.getByTestId('output-cell').style.aspectRatio).toBe('');
  });

  it('a video preview takes the box its run asked for', () => {
    renderOutput({
      kind: 'video',
      gen_ratio: '9:16',
      preview_url: '/api/v1/generated-media/5/stream',
    });
    expect(screen.getByTestId('output-cell').style.aspectRatio).toBe('9 / 16');
  });

  it('a ratio-less multi-image archive imposes no box on its grid cells', () => {
    // outputHistory.replaceOutputImagesWithHistory mints exactly this shape:
    // `images` plus `history_for`, no `gen_ratio`, nothing pending. It
    // ACCUMULATES, so two runs of a count:1 prompt already take the grid
    // branch — and an archive never regenerates, so a square fallback here
    // is permanent letterboxing, not a placeholder waiting to be replaced.
    renderOutput({
      history_for: 'p1',
      images: [IMG, { ...IMG, url: '/api/v1/generated-media/2/cover' }],
    });
    const cells = screen.getAllByTestId('output-cell');
    expect(cells).toHaveLength(2);
    for (const cell of cells) expect(cell.style.aspectRatio).toBe('');
  });

  it('an unboxed grid cell does not stretch its image to a height it has not got', () => {
    // `h-full` inside a wrapper with no reserved box collapses the image —
    // the same pairing rule the single-preview branch already follows.
    renderOutput({
      history_for: 'p1',
      images: [IMG, { ...IMG, url: '/api/v1/generated-media/2/cover' }],
    });
    for (const img of screen.getAllByRole('img'))
      expect(img.className).not.toContain('h-full');
  });

  it('a pending grid with no ratio keeps the square its placeholder needs', () => {
    // The live-run case is unchanged: a shimmer cell and the image that
    // replaces it must resolve to ONE box.
    renderOutput({ gen_pending: 2, images: [IMG] });
    const cells = screen.getAllByTestId('output-cell');
    expect(cells).toHaveLength(3);
    for (const cell of cells) expect(cell.style.aspectRatio).toBe('1 / 1');
    for (const img of screen.getAllByRole('img'))
      expect(img.className).toContain('h-full');
  });
});
