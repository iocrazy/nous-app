// IC SMART_IMAGE_DROP_TEXT_TYPES: drags from other browser tabs / asset
// managers carry URLs, not Files — parse uri-list, html <img>/<a>, and
// plain-text http(s) links into image-node candidates.
import { describe, expect, it } from 'vitest';

import { extractDropUrls } from './dropUrl';

function dt(data: Record<string, string>): DataTransfer {
  return {
    types: Object.keys(data),
    getData: (t: string) => data[t] ?? '',
  } as unknown as DataTransfer;
}

describe('extractDropUrls', () => {
  it('reads text/uri-list lines, skipping comments', () => {
    expect(
      extractDropUrls(dt({ 'text/uri-list': '# c\nhttps://a.com/x.png\r\nhttps://b.com/y.jpg' })),
    ).toEqual(['https://a.com/x.png', 'https://b.com/y.jpg']);
  });

  it('pulls img src out of text/html', () => {
    expect(
      extractDropUrls(dt({ 'text/html': '<div><img src="https://a.com/pic.webp" alt=""></div>' })),
    ).toEqual(['https://a.com/pic.webp']);
  });

  it('accepts a bare http link in text/plain and dedupes across types', () => {
    expect(
      extractDropUrls(
        dt({
          'text/uri-list': 'https://a.com/x.png',
          'text/plain': 'https://a.com/x.png',
        }),
      ),
    ).toEqual(['https://a.com/x.png']);
  });

  it('ignores non-http payloads', () => {
    expect(extractDropUrls(dt({ 'text/plain': 'hello world' }))).toEqual([]);
  });
});
