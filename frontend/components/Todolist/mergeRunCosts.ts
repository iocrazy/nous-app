/**
 * 一个议题里每条 run 的账：rollup 的 `runs[]` 打底，WS `done` 帧补最新的那条
 * （3c §4.2）。
 *
 * 帧是**补充，不是替代**。它比下一次轮询早几秒到，但只带得动两个字段，而且这两个
 * 字段各自都可能是 null —— `_run_cost` 读失败是 null，这条 run 没人收费也是 null。
 * 所以覆盖必须**逐字段**且只在非 null 时发生：拿一个 null 盖掉 rollup 明明有的
 * 数字，等于把「读不到」画成「没有」，而且 `liveRunCost` 一直留在 state 里，每轮
 * 轮询回来的新值都会被再抹一次 —— 屏幕上那个 `—` 永远不会自己好。
 *
 * 写入侧还有一道守卫（两个都 null 就不写），与 `AIChatPanel` 的 `done` 分支同口径；
 * 这里的逐字段合并是第二道，因为「花费有、积分没有」是完全正常的一帧。
 */
import type { IssueProgressRun } from '../../services/issuesService';
import type { RunCost } from '../../types';

export interface LiveRunCost {
  runId: string;
  cost_cents: number | null;
  charged_points: number | null;
}

export function mergeRunCosts(
  runs: readonly IssueProgressRun[] | undefined,
  live: LiveRunCost | null,
): Record<string, RunCost> {
  const out: Record<string, RunCost> = {};
  for (const r of runs ?? []) {
    // ⚠️ rollup 的 `runs[]` 没有 token 列，所以议题侧浮层第一行恒为
    // `0 prompt · 0 completion`。这是取舍不是缺陷：补它要改 Part B 的聚合 SQL
    // （Task 23 已记票），而这一行要回答的「花了多少」本身是准的。
    out[String(r.id)] = {
      cost_cents: r.cost_cents,
      charged_points: r.charged_points ?? null,
      model: r.model,
      status: r.status,
      prompt_tokens: 0,
      completion_tokens: 0,
    };
  }
  if (!live) return out;
  const prev = out[live.runId];
  out[live.runId] = {
    cost_cents: live.cost_cents ?? prev?.cost_cents ?? null,
    charged_points: live.charged_points ?? prev?.charged_points ?? null,
    model: prev?.model ?? null,
    status: prev?.status ?? 'completed',
    prompt_tokens: prev?.prompt_tokens ?? 0,
    completion_tokens: prev?.completion_tokens ?? 0,
  };
  return out;
}
