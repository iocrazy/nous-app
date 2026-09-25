import { describe, expect, it, vi } from 'vitest';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

import { getProjectFileStreamUrl } from './projectsService';

describe('getProjectFileStreamUrl', () => {
  it('points at the stream route for the current content', () => {
    expect(getProjectFileStreamUrl('11', '22')).toBe(
      'https://api.test/api/v1/projects/11/files/22/stream',
    );
  });

  it('carries the version and the media token for a bare <video src>', () => {
    const url = new URL(getProjectFileStreamUrl('11', '22', { versionId: '32', token: 'a.b c' }));
    expect(url.pathname).toBe('/api/v1/projects/11/files/22/stream');
    expect(url.searchParams.get('version_id')).toBe('32');
    expect(url.searchParams.get('token')).toBe('a.b c');
  });

  it('omits a missing token rather than sending an empty one', () => {
    expect(getProjectFileStreamUrl('11', '22', { token: null })).not.toContain('token=');
  });
});
