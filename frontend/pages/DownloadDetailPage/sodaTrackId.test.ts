import { describe, it, expect } from 'vitest';
import { sodaTrackId } from './sodaTrackId';

describe('sodaTrackId', () => {
  it('reads track_id from a qishui share/track url', () => {
    expect(
      sodaTrackId({
        original_url: 'https://music.douyin.com/qishui/share/track?track_id=7382280354118355007&x=1',
        platform_id: 'pf-ignored',
      }),
    ).toBe('7382280354118355007');
  });

  it('reads ugc_video_id for a soda ugc video url', () => {
    expect(
      sodaTrackId({
        original_url: 'https://music.douyin.com/qishui/share/ugc_video?ugc_video_id=uv99',
        platform_id: 'pf',
      }),
    ).toBe('uv99');
  });

  it('falls back to platform_id when the url has no track id (e.g. short link)', () => {
    expect(
      sodaTrackId({ original_url: 'https://v.douyin.com/abc123/', platform_id: '7777' }),
    ).toBe('7777');
  });

  it('falls back to platform_id when there is no url', () => {
    expect(sodaTrackId({ platform_id: '555' })).toBe('555');
  });

  it('returns null when neither is available', () => {
    expect(sodaTrackId({})).toBeNull();
  });
});
