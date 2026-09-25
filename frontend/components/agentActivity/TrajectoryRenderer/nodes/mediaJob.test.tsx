/**
 * Async GenerateVideo (backend `workflows/agent_video.py`): the outcome shows up
 * twice — as a `media_result` inbox claim on the run that read it, and as a
 * `media_job_done` event on the run that submitted it. Payloads below are the
 * backend's real shapes: `inbox_claimed.content` arrives as a JSON STRING on
 * the wire, and ids are Snowflake strings.
 */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { AgentRunEvent } from '../../../../types/api';
import { foldEvents, type InboxNode, type MediaJobNode } from '../foldEvents';
import { InboxNodeView, MediaJobNodeView } from './builtins';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown, opts?: unknown) => {
      const template = typeof fallback === 'string' ? fallback : key;
      const vars = (typeof fallback === 'object' ? fallback : opts) as Record<string, unknown> | undefined;
      return vars
        ? template.replace(/\{\{(\w+)\}\}/g, (_m, k: string) => String(vars[k] ?? `{{${k}}}`))
        : template;
    },
  }),
}));

afterEach(cleanup);

let seq = 0;
function ev(event_type: string, payload: Record<string, unknown>, coords: { turn?: number; step?: number } = {}): AgentRunEvent {
  seq += 1;
  return { seq, event_type, payload, created_at: '2026-09-22T00:00:00Z', turn: coords.turn ?? null, step: coords.step ?? null };
}

const DONE_PAYLOAD = {
  status: 'completed',
  media_kind: 'video',
  generated_media_id: '349426769401737',
  url: '/api/v1/generated-media/349426769401737/stream',
  error_code: null,
  task_id: '6f1c2c55-8a3e-4d0e-9d7b-2b0c1f7e9a10',
  reply_to_kind: 'conversation',
};

describe('foldEvents — async media jobs', () => {
  it('a media_job_done event becomes its own node, never part of a closed step', () => {
    seq = 0;
    const nodes = foldEvents(
      [
        ev('step_start', { turn: 1, step: 1 }, { turn: 1, step: 1 }),
        ev('turn_end', { reason: 'completed' }),
        ev('media_job_done', DONE_PAYLOAD, { turn: 1, step: 1 }),
      ],
      { isRunning: false },
    );
    const job = nodes.find((n) => n.kind === 'media_job') as MediaJobNode | undefined;
    expect(job?.result).toEqual({
      status: 'completed',
      mediaKind: 'video',
      generatedMediaId: '349426769401737',
      errorCode: null,
      taskId: DONE_PAYLOAD.task_id,
    });
  });

  it('a missing status never reads as success', () => {
    seq = 0;
    const nodes = foldEvents([ev('media_job_done', { generated_media_id: '1' })], { isRunning: false });
    const job = nodes.find((n) => n.kind === 'media_job') as MediaJobNode;
    expect(job.result.status).toBe('failed');
  });

  it('a media_result claim carries the outcome on the inbox node (content as a JSON string)', () => {
    seq = 0;
    const content = JSON.stringify({
      status: 'failed',
      media_kind: 'video',
      generated_media_id: null,
      url: null,
      error_code: 'daemon_offline',
      task_id: 't-1',
      text: 'GenerateVideo job t-1 failed (daemon_offline): not connected.',
    });
    const nodes = foldEvents(
      [ev('inbox_claimed', { inbox_id: 'i1', kind: 'media_result', turn: 1, step: 2, content }, { turn: 1, step: 2 })],
      { isRunning: false },
    );
    const inbox = nodes.find((n) => n.kind === 'inbox') as InboxNode;
    expect(inbox.inboxKind).toBe('media_result');
    expect(inbox.result).toBeNull();
    expect(inbox.media).toEqual({
      status: 'failed',
      mediaKind: 'video',
      generatedMediaId: null,
      errorCode: 'daemon_offline',
      taskId: 't-1',
    });
  });
});

describe('media job rows', () => {
  it('a ready video offers the clip', () => {
    render(
      <MediaJobNodeView
        node={{
          kind: 'media_job',
          key: 'seq:1',
          at: null,
          result: { status: 'completed', mediaKind: 'video', generatedMediaId: '349426769401737', errorCode: null, taskId: 't' },
        }}
        expanded={false}
      />,
    );
    expect(screen.getByTestId('traj-media-job')).toHaveTextContent('Video ready');
    expect(screen.getByTestId('traj-media-job-open').getAttribute('href')).toContain(
      '/api/v1/generated-media/349426769401737/stream',
    );
  });

  it('a failed video names its code, wears the danger tone and offers nothing to open', () => {
    render(
      <MediaJobNodeView
        node={{
          kind: 'media_job',
          key: 'seq:1',
          at: null,
          result: { status: 'failed', mediaKind: 'video', generatedMediaId: null, errorCode: 'daemon_timeout', taskId: 't' },
        }}
        expanded={false}
      />,
    );
    const row = screen.getByTestId('traj-media-job');
    expect(row).toHaveTextContent('daemon_timeout');
    expect(row.className).toMatch(/danger/);
    expect(screen.queryByTestId('traj-media-job-open')).toBeNull();
  });

  it('a media_result inbox row reads as a video result with its verdict', () => {
    const node: InboxNode = {
      kind: 'inbox',
      key: 'seq:2',
      inboxKind: 'media_result',
      turn: 1,
      step: 2,
      at: null,
      result: null,
      source: null,
      media: { status: 'failed', mediaKind: 'video', generatedMediaId: null, errorCode: 'daemon_offline', taskId: 't' },
    };
    render(<InboxNodeView node={node} expanded={false} />);
    const row = screen.getByTestId('traj-inbox');
    expect(row).toHaveTextContent('Video result');
    expect(row).toHaveTextContent('daemon_offline');
    expect(row.className).toMatch(/danger/);
  });
});
