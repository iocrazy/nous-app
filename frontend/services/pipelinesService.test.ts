/**
 * Unit tests for pipelinesService (W2b) — pins the URL shapes, the string-id
 * contract (Snowflake ids never coerced to number), and the run/list wiring.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  createPipeline,
  listIssuePipelineRuns,
  listPipelines,
  runPipeline,
  type Pipeline,
  type PipelineRun,
} from './pipelinesService';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('./parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({ Authorization: 'Bearer t' }),
}));

function stub(body: unknown, status = 200): ReturnType<typeof vi.spyOn> {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: status < 400,
    status,
    headers: new Headers(),
    text: async () => (body === undefined ? '' : JSON.stringify(body)),
    json: async () => body,
  } as unknown as Response);
}

const BIG = '9007199254740993'; // > 2^53

function pipeline(): Pipeline {
  return {
    id: BIG,
    team_id: '7',
    name: 'Relay',
    description: null,
    enabled: true,
    created_by_user_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    steps: [
      {
        id: '1',
        pipeline_id: BIG,
        step_order: 1,
        agent_id: 'a1',
        title_template: 'T',
        prompt_template: 'P',
      },
    ],
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('pipelinesService', () => {
  it('lists pipelines for a team and preserves the snowflake id as a string', async () => {
    const spy = stub([pipeline()]);
    const out = await listPipelines('7');
    expect(spy).toHaveBeenCalledWith(
      'https://api.test/pipelines/?team_id=7',
      expect.anything(),
    );
    expect(out[0].id).toBe(BIG); // string, not Number()-ed
    expect(typeof out[0].id).toBe('string');
  });

  it('creates a pipeline via POST with the steps array', async () => {
    const spy = stub(pipeline(), 201);
    await createPipeline({
      team_id: 7,
      name: 'Relay',
      steps: [{ step_order: 1, agent_id: 'a1', title_template: 'T', prompt_template: 'P' }],
    });
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('https://api.test/pipelines/');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string).steps).toHaveLength(1);
  });

  it('runs a pipeline against a parent issue, sending the id as a string', async () => {
    const run: PipelineRun = {
      id: '900',
      pipeline_id: BIG,
      parent_issue_id: '1000',
      current_step: 1,
      status: 'running',
      halted_reason: null,
      started_by_user_id: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      completed_at: null,
      pipeline_name: 'Relay',
      total_steps: 1,
      current_agent_id: 'a1',
    };
    const spy = stub(run);
    const out = await runPipeline(BIG, 1000);
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`https://api.test/pipelines/${BIG}/run`);
    expect(JSON.parse(init.body as string)).toEqual({ parent_issue_id: '1000' });
    expect(out.status).toBe('running');
  });

  it('unwraps the runs-for-parent list envelope', async () => {
    stub({ items: [{ id: '900', status: 'running' }] });
    const out = await listIssuePipelineRuns(1000);
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe('900');
  });

  it('throws on a non-ok response', async () => {
    stub('boom', 409);
    await expect(runPipeline(BIG, 1000)).rejects.toThrow('409');
  });
});
