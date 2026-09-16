/**
 * 3c §4.2：rollup 的账与 `done` 帧的账怎么合。
 *
 * 帧是**补充**，不是替代。它比下一次轮询早几秒到，但它只带得动两个字段，而且这两个
 * 字段各自都可能是 null（`_run_cost` 读失败、或这条 run 压根没人收费）。一个 null
 * 覆盖掉 rollup 明明有的数字，就是把「读不到」画成了「没有」——而且每轮轮询都被再抹
 * 一次，屏幕上那个 `—` 永远不会自己好。
 */
import { describe, expect, it } from 'vitest';

import { mergeRunCosts, type LiveRunCost } from './mergeRunCosts';
import type { IssueProgressRun } from '../../services/issuesService';

const run = (over: Partial<IssueProgressRun> = {}): IssueProgressRun => ({
  id: '701',
  status: 'completed',
  started_at: null,
  ended_at: null,
  model: 'doubao-seed-2-0-lite',
  error_code: null,
  cost_cents: 0.82,
  ended: null,
  step: null,
  charged_points: 0.5,
  ...over,
});

describe('mergeRunCosts', () => {
  it('rollup 的每条 run 各成一行，键是字符串 run id', () => {
    const out = mergeRunCosts([run()], null);
    expect(out['701']).toEqual({
      cost_cents: 0.82,
      charged_points: 0.5,
      model: 'doubao-seed-2-0-lite',
      status: 'completed',
      prompt_tokens: 0,
      completion_tokens: 0,
    });
  });

  it('帧带了新数字就用帧的——它比下一次轮询早几秒', () => {
    const live: LiveRunCost = { runId: '701', cost_cents: 1.5, charged_points: 1.5 };
    const out = mergeRunCosts([run()], live);
    expect(out['701'].cost_cents).toBe(1.5);
    expect(out['701'].charged_points).toBe(1.5);
  });

  it('帧上是 null 的字段不覆盖 rollup——读不到不等于没有', () => {
    const live: LiveRunCost = { runId: '701', cost_cents: null, charged_points: null };
    const out = mergeRunCosts([run()], live);
    expect(out['701'].cost_cents).toBe(0.82);
    expect(out['701'].charged_points).toBe(0.5);
  });

  it('逐字段生效：帧带得动花费、带不动积分时，积分留 rollup 的', () => {
    const live: LiveRunCost = { runId: '701', cost_cents: 1.5, charged_points: null };
    const out = mergeRunCosts([run()], live);
    expect(out['701'].cost_cents).toBe(1.5);
    expect(out['701'].charged_points).toBe(0.5);
  });

  it('rollup 还没带这条 run 时，帧自己立一行——刚结束的那轮不必等一次轮询', () => {
    const live: LiveRunCost = { runId: '999', cost_cents: 0.3, charged_points: null };
    const out = mergeRunCosts([run()], live);
    expect(out['999']).toEqual({
      cost_cents: 0.3,
      charged_points: null,
      model: null,
      status: 'completed',
      prompt_tokens: 0,
      completion_tokens: 0,
    });
    expect(out['701'].cost_cents).toBe(0.82); // 别的 run 不受影响
  });

  it('rollup 自己的 charged_points 缺席读作 null，不是 0', () => {
    const out = mergeRunCosts([run({ charged_points: undefined })], null);
    expect(out['701'].charged_points).toBeNull();
  });
});
