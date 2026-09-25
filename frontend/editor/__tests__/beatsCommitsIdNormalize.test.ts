/**
 * Beats and version commits arrive with Snowflake ids as JSON NUMBERS (the
 * repositories keep bigint ids native; see ScriptBeatRow / ScriptCommitRow in
 * types/api.ts). sceneService stringifies them at the boundary.
 *
 * The bug this pins: beat ids reached the list view as numbers, so a drag
 * reorder sent `{"after_beat_id": 208443000000002}` and the backend (a `str`
 * field) answered 422 — every list-view reorder failed. The fixtures below are
 * the real wire shape (number ids), not the idealized strings.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createCommit, listBeats, listCommits, moveBeat } from '../sceneService';

vi.mock('../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));

function stubJson(body: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: true,
    status: 200,
    headers: new Headers(),
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response);
}

const TS = '2026-09-24T01:02:03.456789+00:00';

const beatRow = (id: number) => ({
  id,
  script_id: 208443000000001,
  title: 'Setup',
  summary: null,
  scene_ids: ['208443000000900'],
  sort_order: 1000,
  start_sec: null,
  duration_sec: null,
  beat_role: null,
  color: null,
  created_at: TS,
  updated_at: TS,
});

const commitRow = (id: number) => ({
  id,
  script_id: 208443000000001,
  message: 'v1',
  watermarks: { '208443000000900': 3 },
  scene_ids: [
    { id: '208443000000900', sort_order: 1000, heading_int_ext: 'INT', location_text: 'Kitchen' },
  ],
  created_by: '00000000-0000-0000-0000-000000000042',
  created_at: TS,
});

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('beats — id normalization', () => {
  it('stringifies numeric beat ids from the wire', async () => {
    stubJson({ success: true, data: [beatRow(208443000000002)] });
    const [beat] = await listBeats('208443000000001');
    expect(beat.id).toBe('208443000000002');
    expect(beat.script_id).toBe('208443000000001');
  });

  it('a reorder anchored on a listed beat sends a string after_beat_id', async () => {
    stubJson({ success: true, data: [beatRow(208443000000002), beatRow(208443000000003)] });
    const [first, second] = await listBeats('208443000000001');
    const spy = stubJson({ success: true, data: beatRow(208443000000003) });
    const moved = await moveBeat(second.id, { after_beat_id: first.id });
    const init = spy.mock.calls.at(-1)?.[1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({ after_beat_id: '208443000000002' });
    expect(moved.id).toBe('208443000000003');
  });
});

describe('commits — id normalization', () => {
  it('stringifies list and create rows', async () => {
    stubJson({ success: true, data: [{ ...commitRow(208443000000010), author_name: null }] });
    const [listed] = await listCommits('208443000000001');
    expect(listed.id).toBe('208443000000010');
    expect(listed.author_name).toBeNull();

    stubJson({ success: true, data: commitRow(208443000000011) });
    const created = await createCommit('208443000000001', 'v1');
    expect(created.id).toBe('208443000000011');
    expect(created.script_id).toBe('208443000000001');
  });
});
