/**
 * 「这个议题的一个回合结束了」——跨 React 树的一次广播（harness 3b §4）。
 *
 * 同一个回合结束会被说两遍：WS 的 `status{phase:'done'}` 帧一遍，`useIssueProgress`
 * 的轮询边沿一遍。去重的水位按 `(issueId, runId)` 记 —— transcript 的 `seq` 本来就是
 * 每个 run 自己的计数器，全 issue 共用一条水位会把「新 run 的 seq 3」误判成陈旧帧。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { notifyTurn, subscribeTurn, __resetTurnSignals } from './issueTurnSignal';

beforeEach(() => __resetTurnSignals());

const RUN = '727145299382534100';

describe('issueTurnSignal', () => {
  it('fires once for a seq, and drops the replay of the same seq', () => {
    // 轮询边沿与 WS done 描述的是同一个回合结束，先后到达两次。
    const cb = vi.fn();
    subscribeTurn('5', cb);
    notifyTurn('5', { runId: RUN, seq: 5 });
    notifyTurn('5', { runId: RUN, seq: 5 });
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it('drops a stale frame that arrives out of order', () => {
    const cb = vi.fn();
    subscribeTurn('5', cb);
    notifyTurn('5', { runId: RUN, seq: 5 });
    notifyTurn('5', { runId: RUN, seq: 4 });
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it('keeps two issues independent', () => {
    const a = vi.fn();
    const b = vi.fn();
    subscribeTurn('5', a);
    subscribeTurn('6', b);
    notifyTurn('5', { runId: 'r1', seq: 9 });
    notifyTurn('6', { runId: 'r2', seq: 1 }); // 比 issue 5 的水位低，但不是同一条链
    expect([a.mock.calls.length, b.mock.calls.length]).toEqual([1, 1]);
  });

  it('keeps two runs of ONE issue independent', () => {
    // 一个 issue 连着跑两个 run，第二个 run 的 seq 从头开始数。共用水位会把
    // 新 run 的头几个回合全判成陈旧帧。
    const cb = vi.fn();
    subscribeTurn('5', cb);
    notifyTurn('5', { runId: 'run-a', seq: 40 });
    notifyTurn('5', { runId: 'run-b', seq: 3 });
    expect(cb).toHaveBeenCalledTimes(2);
  });

  it('a local action (no run) never raises a run lane watermark', () => {
    // 回退用响应新版的 id 当 seq —— 那是 Snowflake，比任何 transcript seq 大十个
    // 数量级。一条全 issue 共用的水位会被它顶到天上，之后该 issue 的真实回合信号
    // 全被当成陈旧帧丢掉，实时就此静默死亡。
    const cb = vi.fn();
    subscribeTurn('5', cb);
    notifyTurn('5', { runId: null, seq: 347786145852739099 });
    notifyTurn('5', { runId: RUN, seq: 3 });
    expect(cb).toHaveBeenCalledTimes(2);
  });

  it('one bad subscriber never starves the next', () => {
    const bad = vi.fn(() => {
      throw new Error('boom');
    });
    const good = vi.fn();
    subscribeTurn('5', bad);
    subscribeTurn('5', good);
    notifyTurn('5', { runId: 'r1', seq: 1 });
    expect(good).toHaveBeenCalledTimes(1);
  });

  it('stops delivering after unsubscribe', () => {
    const cb = vi.fn();
    const off = subscribeTurn('5', cb);
    off();
    notifyTurn('5', { runId: 'r1', seq: 1 });
    expect(cb).not.toHaveBeenCalled();
  });
});
