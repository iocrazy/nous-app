import { describe, expect, it, vi } from 'vitest';

vi.mock('../../../utils/apiConfig', () => ({
  getApiUrl: () => 'https://api.example.com',
}));

import { mediaSrc } from './mediaUrl';

describe('mediaSrc', () => {
  it('prefixes relative generated-media urls with the API base', () => {
    expect(mediaSrc('/api/v1/generated-media/1/cover')).toBe(
      'https://api.example.com/api/v1/generated-media/1/cover',
    );
  });
  it('leaves absolute and blob urls alone', () => {
    expect(mediaSrc('https://cdn/x.png')).toBe('https://cdn/x.png');
    expect(mediaSrc('blob:xyz')).toBe('blob:xyz');
  });
  it('empty-safe', () => {
    expect(mediaSrc(null)).toBe('');
    expect(mediaSrc(undefined)).toBe('');
  });
});
