import { describe, expect, it } from 'vitest';

import { resolveNotificationLink } from './notificationLink';

describe('resolveNotificationLink', () => {
  it('maps an issue link to the team-scoped issues route by identifier', () => {
    expect(
      resolveNotificationLink({ linkKind: 'issue', linkId: 'MH-42', teamId: '900' }),
    ).toBe('/team/900/issues/MH-42');
  });

  it('maps a resource link to the file detail route', () => {
    expect(
      resolveNotificationLink({ linkKind: 'resource', linkId: '12345', teamId: '900' }),
    ).toBe('/team/900/resources/file/12345');
  });

  it('maps a publish_batch link to the distribution records list', () => {
    expect(
      resolveNotificationLink({ linkKind: 'publish_batch', linkId: '77', teamId: '900' }),
    ).toBe('/team/900/distribution/records');
  });

  it('returns null when the team scope is missing (no navigable target)', () => {
    expect(
      resolveNotificationLink({ linkKind: 'issue', linkId: 'MH-42', teamId: '' }),
    ).toBeNull();
    expect(
      resolveNotificationLink({ linkKind: 'issue', linkId: 'MH-42', teamId: null }),
    ).toBeNull();
  });

  it('returns null for absent or unknown link kinds', () => {
    expect(resolveNotificationLink({ teamId: '900' })).toBeNull();
    expect(
      resolveNotificationLink({ linkKind: null, linkId: '1', teamId: '900' }),
    ).toBeNull();
    expect(
      resolveNotificationLink({ linkKind: 'canvas', linkId: '1', teamId: '900' }),
    ).toBeNull();
  });

  it('returns null when the link id is missing', () => {
    expect(
      resolveNotificationLink({ linkKind: 'resource', linkId: null, teamId: '900' }),
    ).toBeNull();
  });

  it('url-encodes identifiers that contain unsafe characters', () => {
    expect(
      resolveNotificationLink({ linkKind: 'issue', linkId: 'a b/c', teamId: '900' }),
    ).toBe('/team/900/issues/a%20b%2Fc');
  });
});
