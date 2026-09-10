/**
 * OutputLightbox — unified editor entry (IC ⑧): the preview modal carries
 * an editing tab bar (Crop/Expand/Mask/Split); picking a tool closes the
 * lightbox and opens that editor. Absent editActions → preview-only.
 */

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { OutputLightbox } from './OutputLightbox';

afterEach(cleanup);

const ITEMS = [{ url: '/api/v1/generated-media/1/cover', name: 'a.png' }];

describe('OutputLightbox edit bar', () => {
  it('renders the editing tabs and routes a pick through onClose', () => {
    const onClose = vi.fn();
    const crop = vi.fn();
    const mask = vi.fn();
    render(
      <OutputLightbox
        items={ITEMS}
        index={0}
        kind="image"
        onIndexChange={() => {}}
        onClose={onClose}
        editActions={{ crop, expand: vi.fn(), mask, split: vi.fn() }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Crop' }));
    expect(crop).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Mask' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Expand' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Split' })).toBeInTheDocument();
  });

  it('without editActions the bar is absent (preview-only lightbox)', () => {
    render(
      <OutputLightbox
        items={ITEMS}
        index={0}
        kind="image"
        onIndexChange={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.queryByTestId('lightbox-edit-bar')).toBeNull();
  });
});

describe('OutputLightbox edit bar — carries the viewed item', () => {
  it('hands the picked tool the item on screen, not the first one', () => {
    const crop = vi.fn();
    const items = [
      { url: '/api/v1/generated-media/1/cover', name: 'a.png' },
      { url: '/api/v1/generated-media/2/cover', name: 'b.png' },
    ];
    render(
      <OutputLightbox
        items={items}
        index={1}
        kind="image"
        onIndexChange={() => {}}
        onClose={() => {}}
        editActions={{ crop }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Crop' }));
    expect(crop).toHaveBeenCalledWith(items[1]);
  });
});
