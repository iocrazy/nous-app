/**
 * 「这个议题的一个回合结束了」——跨 React 树的一次广播（harness 3b §4）。
 *
 * 照 `outputHighlight.ts` 的微 store：右栏产出块、@ 页签、画布来源块分属三棵树，
 * 上方没有共同 provider，而 `useSyncExternalStore` 让订阅在 React 里是正确的。
 *
 * 同一个回合结束会被说两遍 —— WS 的 `status{phase:'done'}` 帧一遍，
 * `useIssueProgress` 的轮询边沿一遍 —— 所以这里必须去重，否则每个回合都要重拉两次。
 *
 * ⚠️ **run 车道由第一条信号封口，seq 只在本地车道有序号意义。** 一个 run 只结束一次，
 * 所以 `(issueId, runId)` 车道上第一条信号之后的一律丢掉，不比 seq。理由是两个说话人
 * 报的 seq 根本不是同一个东西：轮询边沿报的是**上一次读到的** `current_run.last_seq`，
 * WS done 报的是这个 run 真正的终局 seq，后者必然更大。按水位比大小时，先落的轮询边沿
 * （小 seq）放行一次，87–144ms 后的 WS 帧（大 seq）越过水位再放一次 —— 真栈上每个回合
 * 重拉两遍产出（harness 三期 Task 9 报告第 9 行，5 次重现 4 次）。
 *
 * ⚠️ **车道必须带 runId，不能只按 issueId 分。** 回退走的是 `runId: null` 的本地车道，
 * 它按 `nextLocalSeq()` 单调计数排序（同一次会话里可以回退很多次，每次都要说）；跟 run
 * 车道混在一起会让任一侧顶死另一侧。
 */
import { useCallback, useSyncExternalStore } from 'react';

export type TurnSignal = {
  /** 结束的那个 run；本地动作（回退）没有 run，走 `null` 的本地车道。 */
  runId: string | null;
  /** run 车道：这个说话人所知的 transcript `seq`，**不参与去重**（两个说话人报的不是
   *  同一个数，见文件头）——只是给消费方看的。本地车道：`nextLocalSeq()` 的单调计数，
   *  那里它就是排序依据。 */
  seq: number;
};

const listeners = new Map<string, Set<(s: TurnSignal) => void>>();
/** 只给本地车道用 —— run 车道不比 seq，见 `sealed`。 */
const lastSeq = new Map<string, number>();
/** 已经结束过的 run 车道。进了这个集合就再也不会广播第二次。 */
const sealed = new Set<string>();
const latest = new Map<string, TurnSignal>();

const lane = (issueId: string, runId: string | null): string => `${issueId}|${runId ?? '@local'}`;

/**
 * 广播一次「回合结束」。
 *
 * run 车道：第一条封口，之后同 `(issueId, runId)` 的一律静默丢掉（不记日志——重复是
 * 设计里就有的常态，两个说话人本来就都会说）。本地车道：按 seq 单调排序，重复或倒退
 * 的丢掉。
 */
export function notifyTurn(issueId: string, signal: TurnSignal): void {
  const key = lane(issueId, signal.runId);
  if (signal.runId !== null) {
    if (sealed.has(key)) return;
    sealed.add(key);
  } else {
    const seen = lastSeq.get(key);
    if (seen !== undefined && signal.seq <= seen) return;
    lastSeq.set(key, signal.seq);
  }
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
  sealed.clear();
  latest.clear();
}
