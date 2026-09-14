/**
 * 「这个议题的一个回合结束了」——跨 React 树的一次广播（harness 3b §4）。
 *
 * 同一个回合结束会被说两遍：WS 的 `status{phase:'done'}` 帧一遍，`useIssueProgress`
 * 的轮询边沿一遍。**一个 run 只结束一次**，所以 `(issueId, runId)` 车道由第一条信号
 * 封口，之后同车道的一律丢掉 —— 两个说话人报的 seq 本来就不是同一个东西（轮询边沿
 * 报的是上一次读到的 `last_seq`，WS 报的是这个 run 真正的终局 seq），拿 seq 比大小
 * 会让后到的那条越过水位再放一次。seq 只在本地车道（`runId: null`）有序号意义。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { nextLocalSeq, notifyTurn, subscribeTurn, __listenerCount, __resetTurnSignals } from './issueTurnSignal';

beforeEach(() => __resetTurnSignals());

const RUN = '727145299382534100';

describe('issueTurnSignal', () => {
  it('fires once for a run, and drops the replay of the same seq', () => {
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

  it('seals the run lane on the poll edge, so the WS done frame adds nothing', () => {
    // 真栈上的原样顺序（Task 9 报告第 9 行）：`useIssueProgress` 的轮询边沿先落，它报
    // 的 seq 是**上一次读到的** `current_run.last_seq`(3)；87–144ms 后 WS 的
    // `status{phase:'done'}` 才到，带着这个 run 真正的终局 seq(42)。两个数字描述的
    // 不是同一件事，拿它们比大小 = 后到的那条越过水位再放一次 = 每回合重拉两遍产出。
    const cb = vi.fn();
    subscribeTurn('5', cb);
    notifyTurn('5', { runId: RUN, seq: 3 });
    notifyTurn('5', { runId: RUN, seq: 42 });
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it('seals the same way when the WS frame lands first', () => {
    // 两个说话人谁先到不确定（轮询 tick 与 WS 帧没有顺序保证），封口必须两向都成立。
    const cb = vi.fn();
    subscribeTurn('5', cb);
    notifyTurn('5', { runId: RUN, seq: 42 });
    notifyTurn('5', { runId: RUN, seq: 3 });
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it('gives each of two runs its own single signal', () => {
    // 封口是按 run 的，不是按 issue 的 —— 下一个 run 结束照样要说一次。
    const cb = vi.fn();
    subscribeTurn('5', cb);
    notifyTurn('5', { runId: 'run-a', seq: 3 });
    notifyTurn('5', { runId: 'run-a', seq: 42 });
    notifyTurn('5', { runId: 'run-b', seq: 3 });
    notifyTurn('5', { runId: 'run-b', seq: 42 });
    expect(cb).toHaveBeenCalledTimes(2);
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

  it('gives the local lane a strictly increasing seq', () => {
    // Two reverts inside the same millisecond must both be heard. The version's
    // Snowflake id cannot carry that: past 2^53 a JS number rounds, so two ids
    // minted in one millisecond collapse to the same value and the second one
    // is dropped as a replay.
    const cb = vi.fn();
    subscribeTurn('5', cb);
    notifyTurn('5', { runId: null, seq: nextLocalSeq() });
    notifyTurn('5', { runId: null, seq: nextLocalSeq() });
    expect(cb).toHaveBeenCalledTimes(2);
  });

  it('a local seq never collides with itself across issues', () => {
    const a = vi.fn();
    subscribeTurn('5', a);
    const first = nextLocalSeq();
    const second = nextLocalSeq();
    expect(second).toBeGreaterThan(first);
  });

  it('forgets its seals on reset', () => {
    // 模块级状态活得比一个用例长；不清封口表会让后面的用例静默丢掉自己的信号。
    const first = vi.fn();
    subscribeTurn('5', first);
    notifyTurn('5', { runId: RUN, seq: 3 });
    expect(first).toHaveBeenCalledTimes(1);

    __resetTurnSignals();
    const second = vi.fn();
    subscribeTurn('5', second);
    notifyTurn('5', { runId: RUN, seq: 3 });
    expect(second).toHaveBeenCalledTimes(1);
  });

  it('stops delivering after unsubscribe', () => {
    const cb = vi.fn();
    const off = subscribeTurn('5', cb);
    off();
    notifyTurn('5', { runId: 'r1', seq: 1 });
    expect(cb).not.toHaveBeenCalled();
    expect(__listenerCount('5')).toBe(0);
  });
});
