import { describe, expect, it } from 'vitest';
import { buildShareMediaUrls } from './shareMediaUrls';

const BASE = 'https://api.test';
const GRANT = 'sg1.339710259795355.1790000000.ab+cd';

// `POST /shares/code/{code}` fields as the backend sends them: `resource_id`
// is a JSON number, `media_id` a string, `thumbnail_path` a storage path.
const share = {
  access_token: GRANT,
  resource_id: 339710259795001,
  media_id: '339710259795777' as string | null,
  thumbnail_path: null as string | null,
};

describe('buildShareMediaUrls', () => {
  it('puts the share grant (not the share code) on every URL', () => {
    const urls = buildShareMediaUrls(BASE, share);
    const q = `share_token=${encodeURIComponent(GRANT)}`;
    expect(urls).toEqual({
      resourceUrl: `${BASE}/api/v1/resources/339710259795001/file?${q}`,
      mediaUrl: `${BASE}/media/339710259795777?${q}`,
      coverUrl: `${BASE}/media/339710259795777/cover?${q}`,
    });
  });

  it('falls back to the resource file, and its cover only with a thumbnail', () => {
    const noMedia = buildShareMediaUrls(BASE, { ...share, media_id: null });
    expect(noMedia.mediaUrl).toBe(noMedia.resourceUrl);
    expect(noMedia.coverUrl).toBeNull();

    const withThumb = buildShareMediaUrls(BASE, {
      ...share,
      media_id: null,
      thumbnail_path: 'sb://library/t.jpg',
    });
    expect(withThumb.coverUrl).toMatch(/\/media\/339710259795001\/cover\?share_token=/);
  });

  it('has no file URLs for a share without a resource', () => {
    const urls = buildShareMediaUrls(BASE, { ...share, resource_id: null, media_id: null });
    expect(urls).toEqual({ resourceUrl: null, mediaUrl: null, coverUrl: null });
  });
});
