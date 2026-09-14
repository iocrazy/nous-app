/**
 * 「这个议题的一个回合结束了」——跨 React 树的一次广播（harness 3b §4）。
 *
 * 照 `outputHighlight.ts` 的微 store：右栏产出块、@ 页签、画布来源块分属三棵树，
 * 上方没有共同 provider，而 `useSyncExternalStore` 让订阅在 React 里是正确的。
 *
 * 同一个回合结束会被说两遍 —— WS 的 `status{phase:'done'}` 帧一遍，
 * `useIssueProgress` 的轮询边沿一遍 —— 所以这里必须去重，否则每个回合都要重拉两次。
 *
 * ⚠️ **水位按 `(issueId, runId)` 记，不是按 issueId。** transcript 的 `seq` 本来就是
 * 每个 run 自己的计数器，全 issue 共用一条水位会把「新 run 的 seq 3」判成「旧 run 的
 * seq 40」的陈旧帧；回退成功时传的又是 Snowflake 量级的 id（`runId: null`），同一条
 * 水位必然被它顶死，此后该议题的实时刷新静默死亡。分车道之后，轮询边沿与 WS done
 * （同一个 runId）照样互相去重 —— 那才是这条规则真正要吃掉的重复。
 */
import { useCallback, useSyncExternalStore } from 'react';

export type TurnSignal = {
  /** 结束的那个 run；本地动作（回退）没有 run，走 `null` 的本地车道。 */
  runId: string | null;
  /** 该车道内单调递增的计数：run 车道是 transcript 的 `seq`，本地车道是新版的 id。 */
  seq: number;
};

const listeners = new Map<string, Set<(s: TurnSignal) => void>>();
const lastSeq = new Map<string, number>();
const latest = new Map<string, TurnSignal>();

const lane = (issueId: string, runId: string | null): string => `${issueId}|${runId ?? '@local'}`;

/** 广播一次「回合结束」。同一车道内重复或倒退的 seq 被丢掉。 */
export function notifyTurn(issueId: string, signal: TurnSignal): void {
  const key = lane(issueId, signal.runId);
  const seen = lastSeq.get(key);
  if (seen !== undefined && signal.seq <= seen) return;
  lastSeq.set(key, signal.seq);
  latest.set(issueId, signal);
  for (const fn of listeners.get(issueId) ?? []) {
    try {
      fn(signal);
    } catch (err) {
      // 一个坏订阅者永远不该饿死排在它后面的（CLAUDE.md 分发器要容纳回调异常）。
      console.error('[issueTurnSignal] listener failed', err);
    }
  }
}

export function subscribeTurn(issueId: string, cb: (s: TurnSignal) => void): () => void {
  const set = listeners.get(issueId) ?? new Set<(s: TurnSignal) => void>();
  set.add(cb);
  listeners.set(issueId, set);
  return () => {
    set.delete(cb);
  };
}

/** 最后一个信号。快照返回 map 里的同一个引用 —— 每次新建对象会让
 *  `useSyncExternalStore` 判定「又变了」并无限重渲染。 */
export function useTurnSignal(issueId: string): TurnSignal | null {
  const subscribe = useCallback(
    (fn: () => void) => subscribeTurn(issueId, () => fn()),
    [issueId],
  );
  const snapshot = useCallback(() => latest.get(issueId) ?? null, [issueId]);
  return useSyncExternalStore(subscribe, snapshot, () => null);
}

let localSeq = 0;

/**
 * 本地车道（`runId: null`）的下一个计数。
 *
 * ⚠️ **不要拿被写对象的 id 当这个 seq。** 那是 Snowflake，`Number()` 之后超过 2^53
 * 会静默取整 —— 同一毫秒内的两次回退会折成同一个值，第二次被水位当成重放丢掉，
 * 页面就停在第一次回退的结果上。一个进程内单调的计数器没有这个问题，而本地车道
 * 本来也不需要和任何服务端序号对齐：它只需要「比上一次大」。
 *
 * 刻意**不**被 `__resetTurnSignals` 清零 —— 它只需单调，跨用例继续增长是对的。
 */
export function nextLocalSeq(): number {
  localSeq += 1;
  return localSeq;
}

/** 测试专用：某个议题当前挂着几个订阅者。用来钉住「挂载/卸载来回几次不会漏订阅」——
 *  泄漏的订阅者会让每个回合信号触发越来越多次重拉，而那是只有长会话才看得见的退化。 */
export function __listenerCount(issueId: string): number {
  return listeners.get(issueId)?.size ?? 0;
}

/** 测试专用：模块级状态活得比一个用例长。 */
export function __resetTurnSignals(): void {
  listeners.clear();
  lastSeq.clear();
  latest.clear();
}
