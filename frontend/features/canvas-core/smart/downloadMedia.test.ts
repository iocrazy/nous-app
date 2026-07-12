// features/canvas-core/smart/downloadMedia.test.ts
// Shared blob-download helper (extracted from OutputLightbox for the node
// toolbar, P2-3): fetch→blob→object-URL anchor click, because the anchor
// `download` attribute is ignored cross-origin and would navigate away.

import { afterEach, describe, expect, it, vi } from 'vitest';

import { downloadBlob, downloadName, downloadUrl } from './downloadMedia';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('downloadName', () => {
  it('prefers the explicit name, falls back to the url tail, then an index', () => {
    expect(downloadName({ url: '/gm/1/cover', name: 'cat.png' }, 0)).toBe('cat.png');
    expect(downloadName({ url: '/api/v1/generated-media/9/cover' }, 0)).toBe('cover');
    expect(downloadName({ url: '' }, 2)).toBe('output-3');
  });
});

describe('downloadUrl', () => {
  it('fetches the blob and clicks an object-URL anchor', async () => {
    const blob = new Blob(['x'], { type: 'image/png' });
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue({ ok: true, blob: async () => blob } as unknown as Response);
    const createSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock');
    const revokeSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {});

    await downloadUrl({ url: '/gm/1/cover', name: 'a.png' }, 0);

    expect(fetchSpy).toHaveBeenCalledWith('/gm/1/cover');
    expect(createSpy).toHaveBeenCalled();
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeSpy).toHaveBeenCalledWith('blob:mock');
  });

  it('throws on a non-ok response so callers can surface the error', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 404,
    } as unknown as Response);
    await expect(downloadUrl({ url: '/gm/404/cover' }, 0)).rejects.toThrow('404');
  });
});

describe('downloadBlob', () => {
  it('saves an in-memory blob via an object-URL anchor (P2-8 frame export)', () => {
    const createSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock');
    const revokeSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {});
    downloadBlob(new Blob(['x'], { type: 'image/png' }), 'frame.png');
    expect(createSpy).toHaveBeenCalled();
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeSpy).toHaveBeenCalledWith('blob:mock');
  });
});
