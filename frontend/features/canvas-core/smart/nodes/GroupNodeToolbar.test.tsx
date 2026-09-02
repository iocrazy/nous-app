// features/canvas-core/smart/nodes/GroupNodeToolbar.test.tsx
// IC-parity group toolbar (smartGroupToolbarHtml): Arrange / Preview /
// Stitch / Download / Ungroup with IC's exact enablement rules —
// arrange needs content, preview+download need ≥1 image, stitch needs ≥2,
// ungroup is always on. Write actions (arrange/stitch/ungroup) are
// withheld in a read-only session; preview/download are pure reads.

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { GroupNodeToolbar } from './GroupNodeToolbar';

afterEach(cleanup);

function renderBar(over: Partial<Parameters<typeof GroupNodeToolbar>[0]> = {}) {
  const handlers = {
    onArrange: vi.fn(),
    onPreview: vi.fn(),
    onStitch: vi.fn(),
    onDownload: vi.fn(),
    onUngroup: vi.fn(),
  };
  render(
    <GroupNodeToolbar
      imageCount={3}
      memberCount={2}
      pinned
      {...handlers}
      {...over}
    />,
  );
  return handlers;
}

describe('GroupNodeToolbar', () => {
  it('renders all five IC actions and dispatches them', () => {
    const h = renderBar();
    fireEvent.click(screen.getByRole('button', { name: 'Arrange' }));
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stitch' }));
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    fireEvent.click(screen.getByRole('button', { name: 'Ungroup' }));
    expect(h.onArrange).toHaveBeenCalled();
    expect(h.onPreview).toHaveBeenCalled();
    expect(h.onStitch).toHaveBeenCalled();
    expect(h.onDownload).toHaveBeenCalled();
    expect(h.onUngroup).toHaveBeenCalled();
  });

  it('empty group: only Ungroup stays enabled', () => {
    renderBar({ imageCount: 0, memberCount: 0 });
    expect(screen.getByRole('button', { name: 'Arrange' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Stitch' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Download' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Ungroup' })).toBeEnabled();
  });

  it('single image: stitch needs at least two', () => {
    renderBar({ imageCount: 1, memberCount: 0 });
    expect(screen.getByRole('button', { name: 'Preview' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Download' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Stitch' })).toBeDisabled();
  });

  it('read-only: writes disabled, reads stay', () => {
    renderBar({ readOnly: true });
    expect(screen.getByRole('button', { name: 'Arrange' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Stitch' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Ungroup' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Preview' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Download' })).toBeEnabled();
  });
});

describe('GroupNodeToolbar — mounted only when it can be used (fluency T5)', () => {
  // Same downshift as the output bar: a frosted island that is merely
  // faded out still repaints on every frame of a drag or a pan.
  it('is absent from the DOM until hovered or pinned', () => {
    renderBar({ pinned: false, hovered: false });
    expect(screen.queryByTestId('group-node-toolbar')).toBeNull();
    cleanup();
    renderBar({ pinned: false, hovered: true });
    expect(screen.getByTestId('group-node-toolbar')).toBeInTheDocument();
  });

  it('mounts while pinned even with no pointer over the node', () => {
    renderBar({ pinned: true, hovered: false });
    expect(screen.getByTestId('group-node-toolbar')).toBeInTheDocument();
  });
});
