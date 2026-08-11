import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import PermissionChangeLog, { flattenDiff } from './PermissionChangeLog';
import type { AgentPermissionAudit } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

afterEach(cleanup);

function makeAudit(overrides: Partial<AgentPermissionAudit> = {}): AgentPermissionAudit {
  return {
    id: 'audit-1',
    changed_by: '12345678-abcd-4444-8888-abcdefabcdef',
    before: { chat: { enabled: false } },
    after: { chat: { enabled: true } },
    reason: null,
    created_at: '2026-08-10T12:00:00Z',
    ...overrides,
  };
}

describe('PermissionChangeLog — empty state', () => {
  it('shows the empty-state copy when there are no audits and not loading', () => {
    render(<PermissionChangeLog audits={[]} loading={false} />);
    expect(screen.getByText('aiLibrary.permissions.changeLogEmpty')).toBeInTheDocument();
  });

  it('does not show the empty-state copy while loading', () => {
    render(<PermissionChangeLog audits={[]} loading />);
    expect(screen.queryByText('aiLibrary.permissions.changeLogEmpty')).not.toBeInTheDocument();
  });
});

describe('PermissionChangeLog — row rendering', () => {
  it('renders changed_by truncated to 8 chars, the created_at, and the diff summary', () => {
    const audit = makeAudit();
    render(<PermissionChangeLog audits={[audit]} loading={false} />);
    expect(screen.getByText('12345678')).toBeInTheDocument();
    expect(screen.queryByText(audit.changed_by)).not.toBeInTheDocument();
    expect(screen.getByText('chat.enabled: false → true')).toBeInTheDocument();
  });

  it('renders the reason when present', () => {
    render(<PermissionChangeLog audits={[makeAudit({ reason: 'Testing write access' })]} loading={false} />);
    expect(screen.getByText('Testing write access')).toBeInTheDocument();
  });

  it('omits reason text when null', () => {
    render(<PermissionChangeLog audits={[makeAudit({ reason: null })]} loading={false} />);
    // Only the empty-state / diff text should be present, no stray "null".
    expect(screen.queryByText('null')).not.toBeInTheDocument();
  });

  it('renders one row per audit, most-recent-first order as given by the caller', () => {
    const audits = [
      makeAudit({ id: 'a', changed_by: '11111111-0000-0000-0000-000000000000' }),
      makeAudit({ id: 'b', changed_by: '22222222-0000-0000-0000-000000000000' }),
    ];
    render(<PermissionChangeLog audits={audits} loading={false} />);
    expect(screen.getByText('11111111')).toBeInTheDocument();
    expect(screen.getByText('22222222')).toBeInTheDocument();
  });
});

describe('flattenDiff — pure function', () => {
  it('reports a leaf value that changed, prefixed with its dotted path', () => {
    expect(flattenDiff({ chat: { enabled: false } }, { chat: { enabled: true } })).toEqual([
      'chat.enabled: false → true',
    ]);
  });

  it('reports a key that only exists in after (newly granted) as a diff', () => {
    expect(
      flattenDiff({ capabilities: {} }, { capabilities: { write_level: 'write' } }),
    ).toEqual(['capabilities.write_level: — → write']);
  });

  it('walks nested dicts (e.g. capabilities.media.image) to a flattened path', () => {
    expect(
      flattenDiff(
        { capabilities: { media: { image: false } } },
        { capabilities: { media: { image: true } } },
      ),
    ).toEqual(['capabilities.media.image: false → true']);
  });

  it('returns an empty array when before and after are identical', () => {
    expect(
      flattenDiff({ chat: { enabled: true }, capabilities: { write_level: 'write' } }, {
        chat: { enabled: true },
        capabilities: { write_level: 'write' },
      }),
    ).toEqual([]);
  });

  it('returns an empty array for two empty objects', () => {
    expect(flattenDiff({}, {})).toEqual([]);
  });

  it('reports a leaf key present in before and absent in after (revoked) as value → —', () => {
    expect(flattenDiff({ enabled: true }, {})).toEqual(['enabled: true → —']);
  });

  it('reports a nested subtree removed entirely — each of its leaves surfaces, not one opaque line', () => {
    expect(
      flattenDiff({ capabilities: { write_level: 'write', delete: true } }, {}),
    ).toEqual([
      'capabilities.write_level: write → —',
      'capabilities.delete: true → —',
    ]);
  });

  it('revoking one nested key (chat.enabled) leaves an unchanged sibling (chat.read_team_resources) out of the diff', () => {
    expect(
      flattenDiff(
        { chat: { enabled: true, read_team_resources: false } },
        { chat: { read_team_resources: false } },
      ),
    ).toEqual(['chat.enabled: true → —']);
  });
});
