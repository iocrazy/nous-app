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

// The real player decodes the file with `fetch` + `AudioContext` to draw its
// waveform, and jsdom has neither. Stubbed to a marker that records the two
// props the lightbox is responsible for handing it.
vi.mock('../../../AudioWaveformPlayer', () => ({
  AudioWaveformPlayer: ({ src, filename }: { src: string; filename: string }) => (
    <div data-testid="waveform-player" data-src={src} data-filename={filename} />
  ),
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

  // A kind with nothing to draw must not fall through to the <img>: pointing
  // one at an opaque blob renders a broken-image icon and calls it a preview.
  it.each([['file', 'lucide-file']] as const)(
    'places an icon, not an <img>, for a %s item',
    (kind, iconClass) => {
      render(<PinLightbox {...base} kindFor={() => kind} />);
      const placeholder = screen.getByTestId('pin-lightbox-placeholder');
      expect(placeholder.getAttribute('data-media-kind')).toBe(kind);
      expect(placeholder.querySelector(`.${iconClass}`)).toBeTruthy();
      expect(placeholder.textContent).toBe('No Preview');
      expect(screen.queryByTestId('pin-lightbox-image')).toBeNull();
      expect(screen.queryByTestId('pin-lightbox-video')).toBeNull();
    },
  );

  it('keeps navigating when the current item has no preview', () => {
    const onIndexChange = vi.fn();
    render(
      <PinLightbox
        {...base}
        onIndexChange={onIndexChange}
        kindFor={(id) => (id === 'r1' ? 'file' : 'image')}
      />,
    );
    fireEvent.click(screen.getByTestId('pin-lightbox-next'));
    expect(onIndexChange).toHaveBeenCalledWith(1);
  });
});

describe('PinLightbox — audio', () => {
  // Audio used to land on the placeholder with the rest of the non-visual
  // kinds. It is the one of those that CAN be played, and the lightbox is
  // where the card tile's zoom gesture leads — a player inside the tile's
  // own button would nest interactive elements.
  it('plays an audio item instead of showing the no-preview icon', () => {
    render(<PinLightbox {...base} kindFor={() => 'audio'} />);

    expect(screen.getByTestId('pin-lightbox-audio')).toBeTruthy();
    expect(screen.queryByTestId('pin-lightbox-placeholder')).toBeNull();
    expect(screen.queryByTestId('pin-lightbox-image')).toBeNull();
    expect(screen.queryByTestId('pin-lightbox-video')).toBeNull();
  });

  it('hands the player the current item’s URL', () => {
    render(
      <PinLightbox
        {...base}
        index={1}
        kindFor={() => 'audio'}
        srcFor={(id) => `https://api.test/generated-media/${id}/stream`}
      />,
    );
    expect(screen.getByTestId('waveform-player').getAttribute('data-src')).toBe(
      'https://api.test/generated-media/r2/stream',
    );
    expect(screen.getByTestId('pin-lightbox-audio').getAttribute('data-resource-id')).toBe(
      'r2',
    );
  });

  it('titles the player from titleFor, falling back to the slot label', () => {
    const { unmount } = render(
      <PinLightbox {...base} kindFor={() => 'audio'} titleFor={(id) => `Take ${id}`} />,
    );
    expect(screen.getByTestId('waveform-player').getAttribute('data-filename')).toBe(
      'Take r1',
    );
    unmount();

    render(<PinLightbox {...base} kindFor={() => 'audio'} />);
    expect(screen.getByTestId('waveform-player').getAttribute('data-filename')).toBe(
      'Portrait',
    );
  });

  // An untitled generation really does produce '', and `??` would pass it
  // straight through — the player would render a blank name rather than fall
  // back. `||` is load-bearing here, not a style choice.
  it('falls back to the slot label when the title is empty', () => {
    render(<PinLightbox {...base} kindFor={() => 'audio'} titleFor={() => ''} />);
    expect(screen.getByTestId('waveform-player').getAttribute('data-filename')).toBe(
      'Portrait',
    );
  });

  it('still navigates away from an audio item', () => {
    const onIndexChange = vi.fn();
    render(
      <PinLightbox
        {...base}
        onIndexChange={onIndexChange}
        kindFor={(id) => (id === 'r1' ? 'audio' : 'image')}
      />,
    );
    fireEvent.click(screen.getByTestId('pin-lightbox-next'));
    expect(onIndexChange).toHaveBeenCalledWith(1);
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
