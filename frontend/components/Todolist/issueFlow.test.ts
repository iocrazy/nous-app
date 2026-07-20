/**
 * issueFlow pure-helper tests — origin parse (incl. malformed), dot/position
 * math, and due-date bucketing. No network, no React.
 */

import { describe, expect, it } from 'vitest';

import {
  computeDots,
  dueBucket,
  flowPosition,
  parseStageMirror,
  readDueDate,
  stageIndex,
} from './issueFlow';
import type { ProjectStage } from '../../types';

const BIG_PROJECT = '9007199254740993'; // > 2^53
const BIG_STAGE = '9007199254740995';

function stage(id: string, slug: string, order: number): ProjectStage {
  return { id, slug, name: slug, sort_order: order, tools_recommended: [] };
}

const CATALOG: ProjectStage[] = [
  stage('10', 'planning', 1),
  stage('20', 'script', 2),
  stage('30', 'storyboard', 3),
  stage('40', 'render', 4),
];

describe('parseStageMirror', () => {
  it('extracts projectId + stageId, keeping snowflake ids as strings', () => {
    const ref = parseStageMirror('project_stage', `project_stage:${BIG_PROJECT}:${BIG_STAGE}`);
    expect(ref).toEqual({ projectId: BIG_PROJECT, stageId: BIG_STAGE });
    expect(typeof ref!.projectId).toBe('string');
  });

  it('returns null for a non-mirror issue', () => {
    expect(parseStageMirror('manual', 'canvas:123')).toBeNull();
    expect(parseStageMirror('publish', 'publish:123')).toBeNull();
    expect(parseStageMirror(null, null)).toBeNull();
  });

  it('does not crash on a malformed origin_id', () => {
    // project_stage kind but no second colon → not enough parts.
    expect(parseStageMirror('project_stage', 'project_stage:onlyproject')).toBeNull();
    expect(parseStageMirror('project_stage', 'project_stage:')).toBeNull();
    expect(parseStageMirror('project_stage', 'garbage')).toBeNull();
    expect(parseStageMirror('project_stage', undefined)).toBeNull();
  });
});

describe('stageIndex / computeDots / flowPosition', () => {
  it('locates the stage by id (string-safe)', () => {
    expect(stageIndex(CATALOG, '30')).toBe(2);
    expect(stageIndex(CATALOG, '999')).toBe(-1);
  });

  it('marks done / current / future dots around the current index', () => {
    expect(computeDots(4, 2)).toEqual(['done', 'done', 'current', 'future']);
    expect(computeDots(4, 0)).toEqual(['current', 'future', 'future', 'future']);
  });

  it('renders all-future when the current stage is unknown (-1)', () => {
    expect(computeDots(3, -1)).toEqual(['future', 'future', 'future']);
  });

  it('reports 1-based x / total y (0 when unknown)', () => {
    expect(flowPosition(4, 2)).toEqual({ x: 3, y: 4 });
    expect(flowPosition(4, -1)).toEqual({ x: 0, y: 4 });
  });
});

describe('dueBucket', () => {
  const now = new Date('2026-07-20T12:00:00');

  it('returns null when there is no due date (column not shipped yet)', () => {
    expect(dueBucket(null, now)).toBeNull();
    expect(dueBucket(undefined, now)).toBeNull();
  });

  it('returns null on an unparseable value instead of throwing', () => {
    expect(dueBucket('not-a-date', now)).toBeNull();
  });

  it('buckets an overdue date as rose "Overdue · <date>"', () => {
    expect(dueBucket('2026-07-17T12:00:00', now)).toEqual({
      kind: 'overdue',
      label: 'Overdue · Jul 17',
    });
  });

  it('buckets a within-24h date as amber "Due tomorrow"', () => {
    expect(dueBucket('2026-07-21T10:00:00', now)).toEqual({
      kind: 'soon',
      label: 'Due tomorrow',
    });
  });

  it('buckets a further-out date as normal "<Mon Day>"', () => {
    expect(dueBucket('2026-07-25T12:00:00', now)).toEqual({
      kind: 'normal',
      label: 'Jul 25',
    });
  });
});

describe('readDueDate', () => {
  it('reads a string due_date and tolerates its absence', () => {
    expect(readDueDate({ due_date: '2026-07-24' })).toBe('2026-07-24');
    expect(readDueDate({})).toBeUndefined();
    expect(readDueDate(null)).toBeUndefined();
    expect(readDueDate({ due_date: 123 })).toBeUndefined();
  });
});
