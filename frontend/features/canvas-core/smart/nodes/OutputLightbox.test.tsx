// features/canvas-core/smart/nodes/OutputLightbox.test.tsx
// Fullscreen media lightbox (Infinite parity G7): multi-image navigation,
// resolution readout, download / download-all, previous-version compare
// slider, and a Regenerate hook. Pure controlled component.

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { OutputLightbox } from './OutputLightbox';

const ITEMS = [
  { url: '/gm/1/cover', name: 'first.png' },
  { url: '/gm/2/cover', name: 'second.png' },
];

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderBox(overrides: Partial<Parameters<typeof OutputLightbox>[0]> = {}) {
  const onClose = vi.fn();
  const onIndexChange = vi.fn();
  const utils = render(
    <OutputLightbox
      items={ITEMS}
      index={0}
      kind="image"
      onIndexChange={onIndexChange}
      onClose={onClose}
      {...overrides}
    />,
  );
  return { onClose, onIndexChange, ...utils };
}

describe('OutputLightbox', () => {
  it('shows the current image with a counter and navigates with arrows', () => {
    const { onIndexChange } = renderBox();
    expect(screen.getByTestId('lightbox-image')).toHaveProperty(
      'src',
      expect.stringContaining('/gm/1/cover') as unknown as string,
    );
    expect(screen.getByText('1 / 2')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(onIndexChange).toHaveBeenCalledWith(1);
  });

  it('Escape and backdrop click close it; ArrowRight advances', () => {
    const { onClose, onIndexChange } = renderBox();
    fireEvent.keyDown(screen.getByTestId('output-lightbox'), { key: 'ArrowRight' });
    expect(onIndexChange).toHaveBeenCalledWith(1);
    fireEvent.keyDown(screen.getByTestId('output-lightbox'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('single item hides navigation and Download All', () => {
    renderBox({ items: [ITEMS[0]] });
    expect(screen.queryByRole('button', { name: 'Next' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Download All' })).toBeNull();
  });

  it('Download fetches the blob and clicks an object-URL anchor', async () => {
    const blob = new Blob(['x'], { type: 'image/png' });
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue({ ok: true, blob: async () => blob } as unknown as Response);
    const createSpy = vi
      .spyOn(URL, 'createObjectURL')
      .mockReturnValue('blob:mock');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});

    renderBox();
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    await vi.waitFor(() => expect(createSpy).toHaveBeenCalled());
    expect(fetchSpy).toHaveBeenCalledWith('/gm/1/cover');
  });

  it('compare mode renders both layers and the divider clips the result', () => {
    renderBox({ compareSources: [{ url: '/gm/0/cover' }] });
    fireEvent.click(screen.getByRole('button', { name: 'Compare' }));
    const result = screen.getByTestId('compare-result');
    expect(screen.getByTestId('compare-original')).toBeTruthy();
    fireEvent.keyDown(screen.getByRole('slider'), { key: 'ArrowLeft' });
    expect((result as HTMLElement).style.clipPath).toContain('52%');
  });

  it('no compare sources → no Compare button; onRegenerate wires the button', () => {
    const onRegenerate = vi.fn();
    renderBox({ onRegenerate });
    expect(screen.queryByRole('button', { name: 'Compare' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate' }));
    expect(onRegenerate).toHaveBeenCalled();
  });
});

describe('OutputLightbox portal + key isolation', () => {
  it('portals to document.body so RF transform ancestors cannot trap fixed', () => {
    const { container } = renderBox();
    const box = screen.getByTestId('output-lightbox');
    // Must NOT live inside the render container (the RF node subtree).
    expect(container.contains(box)).toBe(false);
    expect(box.parentElement).toBe(document.body);
  });

  it('arrow keys on the compare divider do not switch images', () => {
    const { onIndexChange } = renderBox({ compareSources: [{ url: '/gm/0/cover' }] });
    fireEvent.click(screen.getByRole('button', { name: 'Compare' }));
    fireEvent.keyDown(screen.getByRole('slider'), { key: 'ArrowRight' });
    expect(onIndexChange).not.toHaveBeenCalled();
  });
});

describe('OutputLightbox meta line (P3-B)', () => {
  it('shows a truncated prompt in the meta line when provided', () => {
    const long = 'a very detailed prompt about a cat sitting on a windowsill at golden hour with soft light';
    render(
      <OutputLightbox
        items={ITEMS}
        index={0}
        kind="image"
        onIndexChange={vi.fn()}
        onClose={vi.fn()}
        meta={long}
      />,
    );
    const meta = screen.getByTestId('lightbox-meta');
    expect(meta.textContent).toContain('a very detailed prompt');
    expect(meta.textContent?.endsWith('…')).toBe(true);
    expect(meta.getAttribute('title')).toBe(long);
  });

  it('renders no meta line without a meta prop', () => {
    render(
      <OutputLightbox
        items={ITEMS}
        index={0}
        kind="image"
        onIndexChange={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('lightbox-meta')).toBeNull();
  });
});
