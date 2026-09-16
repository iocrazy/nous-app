/**
 * 一个议题里每个 run 的消耗，来自 rollup 的 `runs[]`（3c §4.2）。走 context 而不是
 * props：`RunTrajectory` 隔着 `IssueChatThread` → `AgentRunRow` 两层，而这两层与花费
 * 无关——让它们透传一个自己不用的对象，下一个改签名的人就会漏掉。同 `replayContext`。
 *
 * 键是 run id 的**字符串**形（Snowflake BIGINT，JS 过了 2^53 会丢精度）。
 * 键不在 = 这条 run 的账还没到，**不是**「它是免费的」——宿主据此不画这一行。
 */
import { createContext, useContext } from 'react';

import type { RunCost } from '../../types';

export const RunCostContext = createContext<Record<string, RunCost>>({});

export function useRunCost(runId: string | null): RunCost | null {
  const map = useContext(RunCostContext);
  return runId ? (map[runId] ?? null) : null;
}
