import { describe, it, expect } from 'vitest';
import { coalesceSystemStatus, COALESCE_THRESHOLD } from './coalesceSystemStatus';
import type { IssueMessage, IssueMessageKind } from '../../services/issueMessageService';

// Minimal IssueMessage factory — only the fields coalesceSystemStatus reads
// (id + kind) matter; the rest are filled to satisfy the type.
function msg(id: string, kind: IssueMessageKind): IssueMessage {
  return {
    id,
    issue_id: 1,
    kind,
    author_user_id: null,
    author_agent_id: null,
    body: null,
    meta: {},
    duration_seconds: null,
    agent_run_id: null,
    from_status: null,
    to_status: null,
    created_at: '2026-07-16T00:00:00Z',
  };
}

const status = (id: string) => msg(id, 'system_status');
const comment = (id: string) => msg(id, 'comment');

describe('coalesceSystemStatus', () => {
  it('threshold is 3', () => {
    expect(COALESCE_THRESHOLD).toBe(3);
  });

  it('returns an empty list for empty input', () => {
    expect(coalesceSystemStatus([])).toEqual([]);
  });

  it('leaves a thread with no system_status untouched', () => {
    const input = [comment('a'), comment('b'), msg('c', 'agent_run')];
    const out = coalesceSystemStatus(input);
    expect(out).toHaveLength(3);
    expect(out.every((i) => i.type === 'message')).toBe(true);
    expect(out.map((i) => i.key)).toEqual(['a', 'b', 'c']);
  });

  it('does NOT collapse a run of exactly 2 status events', () => {
    const input = [comment('a'), status('s1'), status('s2'), comment('b')];
    const out = coalesceSystemStatus(input);
    expect(out).toHaveLength(4);
    expect(out.every((i) => i.type === 'message')).toBe(true);
  });

  it('collapses a run of exactly 3 status events into one group', () => {
    const input = [comment('a'), status('s1'), status('s2'), status('s3'), comment('b')];
    const out = coalesceSystemStatus(input);
    expect(out).toHaveLength(3);
    expect(out[0]).toMatchObject({ type: 'message', key: 'a' });
    expect(out[1].type).toBe('status_group');
    if (out[1].type === 'status_group') {
      expect(out[1].messages.map((m) => m.id)).toEqual(['s1', 's2', 's3']);
    }
    expect(out[2]).toMatchObject({ type: 'message', key: 'b' });
  });

  it('collapses status runs at the very start and very end of the thread', () => {
    const input = [
      status('h1'), status('h2'), status('h3'),
      comment('c'),
      status('t1'), status('t2'), status('t3'), status('t4'),
    ];
    const out = coalesceSystemStatus(input);
    expect(out).toHaveLength(3);
    expect(out[0].type).toBe('status_group');
    expect(out[1]).toMatchObject({ type: 'message', key: 'c' });
    expect(out[2].type).toBe('status_group');
    if (out[0].type === 'status_group') {
      expect(out[0].messages.map((m) => m.id)).toEqual(['h1', 'h2', 'h3']);
    }
    if (out[2].type === 'status_group') {
      expect(out[2].messages.map((m) => m.id)).toEqual(['t1', 't2', 't3', 't4']);
    }
  });

  it('a comment in the middle breaks the run into two sub-runs', () => {
    // 2 status + comment + 3 status → first run stays inline (2 < 3),
    // second run collapses (3 >= 3).
    const input = [status('s1'), status('s2'), comment('mid'), status('s3'), status('s4'), status('s5')];
    const out = coalesceSystemStatus(input);
    // s1, s2 inline (2), mid inline (1), group(s3..s5)
    expect(out).toHaveLength(4);
    expect(out[0]).toMatchObject({ type: 'message', key: 's1' });
    expect(out[1]).toMatchObject({ type: 'message', key: 's2' });
    expect(out[2]).toMatchObject({ type: 'message', key: 'mid' });
    expect(out[3].type).toBe('status_group');
    if (out[3].type === 'status_group') {
      expect(out[3].messages.map((m) => m.id)).toEqual(['s3', 's4', 's5']);
    }
  });

  it('does not mutate the input array or its elements', () => {
    const input = [comment('a'), status('s1'), status('s2'), status('s3')];
    const snapshotOrder = input.map((m) => m.id);
    const frozen = input.map((m) => Object.freeze(m));
    const inputFrozen = Object.freeze(frozen.slice());
    // Object.freeze surfaces accidental writes as throws in strict mode.
    expect(() => coalesceSystemStatus(inputFrozen as IssueMessage[])).not.toThrow();
    expect(input.map((m) => m.id)).toEqual(snapshotOrder);
    expect(input).toHaveLength(4);
  });

  it('produces stable keys derived from group boundary ids', () => {
    const input = [status('s1'), status('s2'), status('s3')];
    const out = coalesceSystemStatus(input);
    expect(out).toHaveLength(1);
    expect(out[0].key).toBe('status-group-s1-s3');
  });
});
