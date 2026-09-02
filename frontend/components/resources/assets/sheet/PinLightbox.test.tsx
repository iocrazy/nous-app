// frontend/components/resources/assets/sheet/PinLightbox.test.tsx
//
// The four props the Generated inbox added, and — more importantly — that the
// pin board's existing call still behaves exactly as it did.
//
// The additive extension was chosen over a second modal. That choice is only
// worth anything if the defaults are genuinely unchanged, so the "no new
// props" cases below are the load-bearing half of this file.

import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, d?: string) => d ?? _k,
  }),
}));

vi.mock('../../../../services/resourceService', () => ({
  getResourceFileUrl: (id: string) => `https://api.test/resources/${id}/file`,
}));

import { PinLightbox } from './PinLightbox';

const base = {
  resourceIds: ['r1', 'r2'],
  index: 0,
  slotLabel: 'Portrait',
  onIndexChange: vi.fn(),
  onClose: vi.fn(),
};

describe('PinLightbox — unchanged defaults', () => {
  it('still resolves ids through the resource file route', () => {
    render(<PinLightbox {...base} />);
    expect(screen.getByTestId('pin-lightbox-image').getAttribute('src')).toBe(
      'https://api.test/resources/r1/file',
    );
  });

  it('renders neither a metadata panel nor an action row when none was given', () => {
    render(<PinLightbox {...base} />);
    expect(screen.queryByTestId('pin-lightbox-panel')).toBeNull();
  });

  it('treats an item as an image unless told otherwise', () => {
    render(<PinLightbox {...base} />);
    expect(screen.queryByTestId('pin-lightbox-video')).toBeNull();
  });
});

describe('PinLightbox — generic media', () => {
  it('uses a caller-supplied URL resolver', () => {
    render(<PinLightbox {...base} srcFor={(id) => `https://api.test/gen/${id}/file`} />);
    expect(screen.getByTestId('pin-lightbox-image').getAttribute('src')).toBe(
      'https://api.test/gen/r1/file',
    );
  });

  it('renders a controllable <video> for a video item', () => {
    render(<PinLightbox {...base} kindFor={() => 'video'} />);
    const video = screen.getByTestId('pin-lightbox-video');
    expect(video.hasAttribute('controls')).toBe(true);
    expect(screen.queryByTestId('pin-lightbox-image')).toBeNull();
  });
});

describe('PinLightbox — metadata and actions', () => {
  it('renders both for the CURRENT item', () => {
    render(
      <PinLightbox
        {...base}
        index={1}
        metadataFor={(id) => <span data-testid="meta">meta for {id}</span>}
        actionsFor={(id) => <button type="button">act on {id}</button>}
      />,
    );
    expect(screen.getByTestId('meta').textContent).toBe('meta for r2');
    expect(screen.getByRole('button', { name: 'act on r2' })).toBeTruthy();
  });

  it('does not close the viewer when the panel is clicked', () => {
    // The panel sits inside the backdrop's click target. Without the stop,
    // reading the metadata or pressing an action would dismiss the lightbox
    // mid-gesture — and a delete confirm would be unreachable.
    const onClose = vi.fn();
    render(
      <PinLightbox
        {...base}
        onClose={onClose}
        actionsFor={() => <button type="button">Delete</button>}
      />,
    );

    fireEvent.click(screen.getByTestId('pin-lightbox-panel'));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(onClose).not.toHaveBeenCalled();

    // The positive control: the backdrop itself still closes.
    fireEvent.click(screen.getByTestId('pin-lightbox'));
    expect(onClose).toHaveBeenCalled();
  });
});
