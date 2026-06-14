import { afterEach, describe, expect, it } from 'vitest';

import {
  _resetIdCounter,
  createOutputNode,
  createPromptNode,
  createShotNode,
} from './factories';

afterEach(() => _resetIdCounter());

const fixedSuffix = () => 'aaaa';

describe('createShotNode', () => {
  it('defaults to "New shot" title + empty refs + empty notes', () => {
    const node = createShotNode({}, { randomSuffix: fixedSuffix });
    expect(node.type).toBe('shot');
    expect(node.id).toBe('shot-1-aaaa');
    expect(node.position).toEqual({ x: 0, y: 0 });
    expect(node.data.title).toBe('New shot');
    expect(node.data.reference_resource_ids).toEqual([]);
    expect(node.data.notes).toBe('');
  });

  it('honours position override and partial data', () => {
    const node = createShotNode(
      { title: 'Wide establishing', notes: 'morning' },
      { position: { x: 100, y: 50 }, randomSuffix: fixedSuffix },
    );
    expect(node.position).toEqual({ x: 100, y: 50 });
    expect(node.data.title).toBe('Wide establishing');
    expect(node.data.notes).toBe('morning');
  });

  it('id counter increments per call', () => {
    const a = createShotNode({}, { randomSuffix: fixedSuffix });
    const b = createShotNode({}, { randomSuffix: fixedSuffix });
    expect(a.id).toBe('shot-1-aaaa');
    expect(b.id).toBe('shot-2-aaaa');
  });
});

describe('createPromptNode', () => {
  it('defaults to idle status, empty body, null agent', () => {
    const node = createPromptNode({}, { randomSuffix: fixedSuffix });
    expect(node.type).toBe('prompt');
    expect(node.data.body).toBe('');
    expect(node.data.provider_slug).toBe('');
    expect(node.data.agent_id).toBeNull();
    expect(node.data.run_status).toBe('idle');
    expect(node.data.run_started_at).toBeNull();
    expect(node.data.run_finished_at).toBeNull();
    expect(node.data.run_error).toBeNull();
  });

  it('round-trips a fully-specified prompt', () => {
    const node = createPromptNode(
      {
        body: 'Make it cinematic',
        provider_slug: 'nous/anthropic',
        agent_id: '99999',
        run_status: 'succeeded',
        run_started_at: '2026-06-10T12:00:00Z',
        run_finished_at: '2026-06-10T12:00:30Z',
      },
      { randomSuffix: fixedSuffix },
    );
    expect(node.data.body).toBe('Make it cinematic');
    expect(node.data.agent_id).toBe('99999');
    expect(node.data.run_status).toBe('succeeded');
  });

  it('round-trips a persisted "blocked" run_status (cascade downstream)', () => {
    const node = createPromptNode(
      { run_status: 'blocked' },
      { randomSuffix: fixedSuffix },
    );
    expect(node.data.run_status).toBe('blocked');
  });

  it('still accepts legacy run_status values (backward compatible)', () => {
    for (const legacy of ['idle', 'queued', 'running', 'succeeded', 'failed'] as const) {
      const node = createPromptNode(
        { run_status: legacy },
        { randomSuffix: fixedSuffix },
      );
      expect(node.data.run_status).toBe(legacy);
    }
  });
});

describe('createOutputNode', () => {
  it('defaults to text kind, null resource', () => {
    const node = createOutputNode({}, { randomSuffix: fixedSuffix });
    expect(node.type).toBe('output');
    expect(node.data.kind).toBe('text');
    expect(node.data.resource_id).toBeNull();
    expect(node.data.preview_text).toBe('');
  });

  it('honours kind + preview_text overrides', () => {
    const node = createOutputNode(
      { kind: 'image', resource_id: '12345', preview_text: 'Caption' },
      { randomSuffix: fixedSuffix },
    );
    expect(node.data.kind).toBe('image');
    expect(node.data.resource_id).toBe('12345');
    expect(node.data.preview_text).toBe('Caption');
  });
});
