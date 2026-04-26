/**
 * Pin the contract for the sub-task card's collapsed-row label/status.
 * The renderer is just JSX around this — the interesting decisions are
 * in summarizeToolCall (which tool name, which fields to surface,
 * error vs. success).
 */

import { describe, it, expect } from 'vitest';

import { summarizeToolCall } from './SubTaskCard';
import type { ChatToolCall } from '../../types';

function call(over: Partial<ChatToolCall>): ChatToolCall {
  return {
    name: 'Delegate',
    iteration: 1,
    args: {},
    result: {},
    ...over,
  };
}

describe('summarizeToolCall', () => {
  it('Delegate: shows arrow + agent slug, status from result', () => {
    const s = summarizeToolCall(
      call({
        name: 'Delegate',
        args: { agent_slug: 'summary', prompt: 'do X' },
        result: { status: 'queued', inbox_message_id: 'abc' },
      }),
    );
    expect(s.label).toBe('→ summary');
    expect(s.status).toBe('queued');
    expect(s.isError).toBe(false);
  });

  it('Delegate: defaults status to "queued" when result has none', () => {
    const s = summarizeToolCall(
      call({ name: 'Delegate', args: { agent_slug: 'foo' }, result: {} }),
    );
    expect(s.status).toBe('queued');
  });

  it('Skill: bare read shows skill slug, status "read"', () => {
    const s = summarizeToolCall(
      call({
        name: 'Skill',
        args: { skill: 'script-outline' },
        result: { prompt: '…' },
      }),
    );
    expect(s.label).toBe('script-outline');
    expect(s.status).toBe('read');
  });

  it('Skill: file read shows slug · file, status "loaded"', () => {
    const s = summarizeToolCall(
      call({
        name: 'Skill',
        args: { skill: 'script-outline', file: 'references/style.md' },
        result: { prompt: '…' },
      }),
    );
    expect(s.label).toBe('script-outline · references/style.md');
    expect(s.status).toBe('loaded');
  });

  it('error result wins over normal status, isError=true', () => {
    const s = summarizeToolCall(
      call({
        name: 'Delegate',
        args: { agent_slug: 'summary' },
        result: { error: 'rate limited' },
      }),
    );
    expect(s.status).toBe('rate limited');
    expect(s.isError).toBe(true);
  });

  it('unknown tool name still renders something', () => {
    const s = summarizeToolCall(
      call({ name: 'WeirdCustomTool', args: {}, result: {} }),
    );
    expect(s.label).toBe('WeirdCustomTool');
    expect(s.status).toBe('done');
  });
});
