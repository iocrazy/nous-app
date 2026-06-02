import { describe, it, expect } from 'vitest';
import { taskResultKind } from './taskResultKind';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';

const t = (over: Partial<UnifiedTask>): UnifiedTask =>
  ({
    id: '1',
    user_id: 'u',
    task_type: 'download',
    status: 'completed',
    title: 'x',
    progress: 100,
    metadata: {},
    created_at: '',
    ...over,
  }) as UnifiedTask;

describe('taskResultKind', () => {
  it('media for download/parse/upload/transcode with a resource', () => {
    for (const tt of ['download', 'parse', 'upload', 'transcode'] as const) {
      expect(taskResultKind(t({ task_type: tt, resource_id: 'r1' }))).toBe('media');
    }
  });

  it('agent for agent runs', () => {
    expect(taskResultKind(t({ task_type: 'agent' }))).toBe('agent');
  });

  it('transcript / summary for their ai types', () => {
    expect(taskResultKind(t({ task_type: 'ai_transcription', resource_id: 'r' }))).toBe('transcript');
    expect(taskResultKind(t({ task_type: 'ai_summary', resource_id: 'r' }))).toBe('summary');
  });

  it('vision for ai_extract with a resource', () => {
    expect(taskResultKind(t({ task_type: 'ai_extract', resource_id: 'r' }))).toBe('vision');
  });

  it('generic for ai_extract with no resource', () => {
    expect(taskResultKind(t({ task_type: 'ai_extract', resource_id: undefined }))).toBe('generic');
  });

  it('generic for media tasks with no resource (e.g. failed)', () => {
    expect(taskResultKind(t({ task_type: 'download', resource_id: undefined }))).toBe('generic');
  });
});
