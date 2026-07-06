/**
 * Unit tests for the v2 editor scene service client.
 * Pins the If-Match ops contract, typed 409/422 errors, the flat
 * convert-to-scenes envelope, and bigint-id string safety.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  applyOps,
  convertToScenes,
  listScenes,
  newElementId,
  OpRejectedError,
  VersionConflictError,
} from '../sceneService';

vi.mock('../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));

function stubJson(body: unknown, status: number = 200) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: status < 400,
    status,
    headers: new Headers(),
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('applyOps — If-Match ops contract', () => {
  it('sends If-Match header and { ops } body', async () => {
    const ops = [{ op: 'delete' as const, element_id: 'el_aaaaaaaa' }];
    const spy = stubJson({
      success: true,
      data: { content_version: 4, elements: [] },
    });
    await applyOps('42', ops, 3);

    const url = spy.mock.calls[0][0] as string;
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(url).toBe('https://api.test/api/v1/scenes/42/elements/ops');
    expect(init.method).toBe('POST');
    const headers = init.headers as Record<string, string>;
    expect(headers['If-Match']).toBe('3');
    expect(JSON.parse(init.body as string)).toEqual({ ops });
  });

  it('unwraps 200 envelope to { content_version, elements }', async () => {
    stubJson({
      success: true,
      data: { content_version: 7, elements: [{ id: 'el_11111111', type: 'action', text: 'hi' }] },
    });
    const out = await applyOps('42', [], 6);
    expect(out.content_version).toBe(7);
    expect(out.elements).toHaveLength(1);
    expect(out.elements[0].id).toBe('el_11111111');
  });

  it('throws VersionConflictError on 409 with current_version + elements', async () => {
    stubJson(
      {
        success: false,
        code: 'version_conflict',
        current_version: 5,
        elements: [{ id: 'el_22222222', type: 'dialogue', text: 'theirs' }],
      },
      409,
    );
    await expect(applyOps('42', [], 3)).rejects.toMatchObject({
      currentVersion: 5,
    });
    // Re-run to inspect the thrown instance directly.
    stubJson(
      {
        success: false,
        code: 'version_conflict',
        current_version: 5,
        elements: [{ id: 'el_22222222', type: 'dialogue', text: 'theirs' }],
      },
      409,
    );
    try {
      await applyOps('42', [], 3);
      throw new Error('should have thrown');
    } catch (err) {
      expect(err).toBeInstanceOf(VersionConflictError);
      const e = err as VersionConflictError;
      expect(e.currentVersion).toBe(5);
      expect(e.elements[0].text).toBe('theirs');
    }
  });

  it('throws OpRejectedError on 422 with code passthrough', async () => {
    stubJson({ success: false, code: 'missing_anchor', detail: { at: 'el_x' } }, 422);
    try {
      await applyOps('42', [], 3);
      throw new Error('should have thrown');
    } catch (err) {
      expect(err).toBeInstanceOf(OpRejectedError);
      const e = err as OpRejectedError;
      expect(e.code).toBe('missing_anchor');
      expect(e.detail).toEqual({ at: 'el_x' });
    }
  });
});

describe('convertToScenes — flat dispatch envelope', () => {
  it('reads top-level task_id (not .data.task_id)', async () => {
    const spy = stubJson({ success: true, task_id: 'x' });
    const taskId = await convertToScenes('s1', 'c1');
    expect(taskId).toBe('x');
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/scripts/s1/chapters/c1/convert-to-scenes',
    );
  });
});

describe('newElementId', () => {
  it('matches el_ + 8 hex and is unique across calls', () => {
    const a = newElementId();
    const b = newElementId();
    expect(a).toMatch(/^el_[0-9a-f]{8}$/);
    expect(b).toMatch(/^el_[0-9a-f]{8}$/);
    expect(a).not.toBe(b);
  });
});

describe('listScenes — bigint id string safety', () => {
  it('keeps snowflake ids as strings', async () => {
    stubJson({
      success: true,
      data: [
        {
          id: '324520385049690',
          script_id: '324520385049001',
          chapter_id: null,
          heading_int_ext: 'INT',
          location_text: 'Kitchen',
          time_of_day: 'DAY',
          content_version: 1,
          elements: [],
          sort_order: 0,
        },
      ],
    });
    const scenes = await listScenes('s1');
    expect(scenes).toHaveLength(1);
    expect(typeof scenes[0].id).toBe('string');
    expect(scenes[0].id).toBe('324520385049690');
  });
});

it('normalizes backend content_json rows into SceneDoc.elements', async () => {
  const row = {
    id: '324564631057459',
    script_id: '324564621063214',
    chapter_id: null,
    heading_int_ext: 'INT',
    location_text: 'Blank Studio',
    time_of_day: 'NIGHT',
    content_version: 1,
    content_json: [{ id: 'el_a', type: 'action', text: 'Rain.' }],
    sort_order: 1000,
  };
  stubJson({ success: true, data: [row] });
  const scenes = await listScenes('324564621063214');
  expect(scenes[0].elements).toEqual([{ id: 'el_a', type: 'action', text: 'Rain.' }]);
  expect('content_json' in scenes[0]).toBe(false);
});

it('defaults elements to [] when a row has null content_json', async () => {
  stubJson({
    success: true,
    data: [{ id: '1', script_id: '2', content_version: 0, content_json: null, sort_order: 0 }],
  });
  const scenes = await listScenes('2');
  expect(scenes[0].elements).toEqual([]);
});
